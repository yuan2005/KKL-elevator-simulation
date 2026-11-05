"""Seed-paired inference; rankings are never retuned with heldout results."""
import argparse
import csv
import math
from pathlib import Path

import numpy as np
from scipy import stats

from research.dual_bank.experiment import DEFAULT, digest, export_metrics, load, locked, save, verify


def paired(values, comparisons):
    x = np.asarray(values, dtype=float)
    if len(x) < 2 or not np.isfinite(x).all():
        raise ValueError("Paired inference requires finite replicate differences")
    mean = float(x.mean())
    se = float(x.std(ddof=1)/math.sqrt(len(x)))
    if se == 0:
        p = 1. if mean == 0 else 0.
    else:
        p = float(2*stats.t.sf(abs(mean/se), len(x)-1))
    radius = float(stats.t.ppf(.975, len(x)-1))*se
    simultaneous = float(stats.t.ppf(1-.05/(2*comparisons), len(x)-1))*se
    return dict(n=len(x), mean=mean, se=se, ci95=[mean-radius, mean+radius],
                simultaneous_ci=[mean-simultaneous, mean+simultaneous], p_raw=p)


def holm(rows):
    order = sorted(range(len(rows)), key=lambda index: rows[index]["p_raw"])
    previous = 0.
    for rank, index in enumerate(order):
        previous = max(previous, min(1., (len(rows)-rank)*rows[index]["p_raw"]))
        rows[index]["p_holm"] = previous


def matrix(rows, phase, policies, levels, seeds, regimes=None):
    subset = [r for r in rows if r["phase"] == phase]
    keyed = {}
    for row in subset:
        identity = (row["level"], row["seed"], row.get("regime"), row["policy"])
        if identity in keyed:
            raise AssertionError("Duplicate paired cell")
        keyed[identity] = row
    expected = {(level, seed, regime, policy) for level in levels for seed in seeds
                for regime in (regimes or [None]) for policy in policies}
    if set(keyed) != expected:
        raise AssertionError(f"Incomplete paired matrix: {phase}")
    return keyed


def daily_statistics(rows, p):
    output = {}
    seed_rows = []
    for phase in ("daily_eval", "daily_fine"):
        policies = ("fixed_level", "fixed_global", "naive", "gated")
        keyed = matrix(rows, phase, policies, p["levels"], p["evaluation_seeds"])
        means = {policy: float(np.mean([keyed[level, seed, None, policy]["awt"]
                 for level in p["levels"] for seed in p["evaluation_seeds"]])) for policy in policies}
        contrasts = []
        for policy in policies[1:]:
            differences = []
            p90_a, p90_b, lwt = [], [], []
            for seed in p["evaluation_seeds"]:
                pairs = [(keyed[level, seed, None, policy],
                          keyed[level, seed, None, "fixed_level"]) for level in p["levels"]]
                difference = float(np.mean([a["awt"]-b["awt"] for a, b in pairs]))
                differences.append(difference)
                seed_rows.append(dict(phase=phase, policy=policy, seed=seed, difference=difference))
                p90_a.append(np.mean([a["p90"] for a, _ in pairs]))
                p90_b.append(np.mean([b["p90"] for _, b in pairs]))
                lwt.append(np.mean([a["lwt"]-b["lwt"] for a, b in pairs]))
            result = dict(policy=policy, **paired(differences, 3),
                          p90_ratio=float(np.mean(p90_a)/np.mean(p90_b)),
                          lwt_increase_pp=float(np.mean(lwt)))
            result["passes_resolution"] = bool(
                result["mean"] <= -p["practical_seconds"] and result["simultaneous_ci"][1] < 0
                and result["p90_ratio"] <= p["p90_ratio"]
                and result["lwt_increase_pp"] <= p["lwt_increase_pp"])
            contrasts.append(result)
        holm(contrasts)
        output[phase] = dict(means=means, contrasts=contrasts)
    for index in range(3):
        passed = all(output[phase]["contrasts"][index]["passes_resolution"]
                     for phase in output)
        for phase in output:
            output[phase]["contrasts"][index]["verified_improvement"] = passed
    return output, seed_rows


def conditional_statistics(rows, p, ranking):
    names = sorted(p["settings"])
    keyed = matrix(rows, "sustained_eval", names, p["levels"],
                   p["evaluation_seeds"], p["regimes"])
    trials = len(p["levels"])*len(p["regimes"])*(len(names)-1)
    comparisons, conditions = [], []
    for level in p["levels"]:
        for regime in p["regimes"]:
            champion = ranking["ranking"][level][regime][0]
            means = {name: float(np.mean([keyed[level, seed, regime, name]["awt"]
                     for seed in p["evaluation_seeds"]])) for name in names}
            local = []
            for name in names:
                if name == champion:
                    continue
                differences = [keyed[level, seed, regime, name]["awt"]-
                               keyed[level, seed, regime, champion]["awt"]
                               for seed in p["evaluation_seeds"]]
                result = dict(level=level, regime=regime, champion=champion,
                              alternative=name, **paired(differences, trials))
                comparisons.append(result)
                local.append(result)
            not_resolved = [champion]+[r["alternative"] for r in local
                                      if r["simultaneous_ci"][0] <= 0]
            conditions.append(dict(level=level, regime=regime, frozen_champion=champion,
                                   heldout_point_best=min(means, key=lambda n: (means[n], n)),
                                   means=means, not_shown_worse_than_champion=not_resolved))
    holm(comparisons)
    return dict(conditions=conditions, comparisons=comparisons,
                caveat="Unresolved alternatives are not proven equivalent; heldout point best "
                       "is descriptive only and never replaces the frozen champion.")


def transition_statistics(rows, p):
    result = {}
    for phase in ("transition_eval", "transition_fine"):
        subset = [r for r in rows if r["phase"] == phase]
        keyed = {(r["level"], r["seed"], r["boundary"], r["policy"]): r for r in subset}
        expected = {(level, seed, boundary, policy) for level in p["levels"]
                    for seed in p["evaluation_seeds"] for boundary in p["boundaries_minutes"]
                    for policy in ("keep", "naive", "gated")}
        if len(keyed) != len(subset) or set(keyed) != expected:
            raise AssertionError("Incomplete heldout transition pairing")
        comparisons = []
        for policy in ("naive", "gated"):
            differences = [float(np.mean([
                keyed[level, seed, boundary, policy]["awt"]-
                keyed[level, seed, boundary, "keep"]["awt"]
                for level in p["levels"] for boundary in p["boundaries_minutes"]]))
                for seed in p["evaluation_seeds"]]
            comparisons.append(dict(policy=policy, **paired(differences, 2)))
        holm(comparisons)
        result[phase] = comparisons
    return result


def write_csv(path, rows):
    if not rows:
        raise ValueError("Cannot export empty table")
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with Path(path).open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json_cell(value) for key, value in row.items()})


def json_cell(value):
    if isinstance(value, (dict, list)):
        import json
        return json.dumps(value, ensure_ascii=False, allow_nan=False)
    return value


def analyze(root):
    p, ranking, switching = verify(root), locked(root, "ranking"), locked(root, "switching")
    rows = export_metrics(root)
    counts = {phase: sum(r["phase"] == phase for r in rows)
              for phase in p["expected"] if phase != "total"}
    if counts != {k: v for k, v in p["expected"].items() if k != "total"}:
        raise AssertionError(f"Incomplete formal matrix: {counts}")
    if any(r["incomplete"] or r["unserved"] for r in rows):
        raise AssertionError("Incomplete service cannot enter statistical analysis")
    daily, seeds = daily_statistics(rows, p)
    conditional = conditional_statistics(rows, p, ranking)
    transitions = transition_statistics(rows, p)
    fine = matrix(rows, "sustained_fine", ("champion", "reference"),
                  p["levels"], p["evaluation_seeds"], p["regimes"])
    fine_conditions = []
    for level in p["levels"]:
        for regime in p["regimes"]:
            differences = [fine[level, seed, regime, "champion"]["awt"]-
                           fine[level, seed, regime, "reference"]["awt"]
                           for seed in p["evaluation_seeds"]]
            fine_conditions.append(dict(level=level, regime=regime, **paired(differences, 21)))
    holm(fine_conditions)
    value = dict(protocol_hash=digest(p), ranking_hash=digest(ranking),
                 switching_hash=digest(switching), counts=counts, daily=daily,
                 conditional=conditional, transitions=transitions,
                 fine_champion_vs_reference=fine_conditions)
    save(root / "statistics.json", value)
    write_csv(root / "conditional_rankings.csv", conditional["conditions"])
    write_csv(root / "conditional_contrasts.csv", conditional["comparisons"])
    write_csv(root / "daily_seed_differences.csv", seeds)
    lines = [
        "# 双组独立召车：人流选优与同状态切换实验",
        "",
        "## 研究范围与模型",
        "",
        "六部车分为两组各三部。每名乘客同时按两组按钮；两组不共享候梯人数、"
        "目的地或乘客已被另一组接走的信息。真实乘客只有一份，按先到先服务上车。"
        "另一组保留呼梯，直到本组到站开门；无人上车仍消耗运行与门服务时间。"
        "同一时刻按车辆编号处理，是明确的离散时间平局规则。",
        "",
        "需求使用固定课程周期、午餐LG2/G比例和三档强度。"
        "主步长0.5秒，完整匹配的全天及同状态留出验证使用0.25秒。"
        "没有声称达到连续时间极限。候选为接客/边际代价 × 分散/本组按钮历史停车 × "
        "满载直驶关/开，共8种；固定选优时两组用相同配方，未遍历64种非对称配对。"
        "门控阶段允许两组独立选择不同配方，两组始终不共享调度状态。软分区不属于本轮候选。",
        "",
        "## 模型参数与日程",
        "",
        "20层，层高3.6米；六部车各容量16人，额定速度2.5米/秒，加速度1米/秒²。"
        "开门2秒、关门3秒，每人上下客1.2秒；超过60秒等待计入长等待率。",
        "",
        "| 时段/模式 | 中档名义需求（人次/5分钟，楼梯分流前） |",
        "|---|---:|",
        "| 00:00–07:30、22:30–24:00，S6 | 3 |",
        "| 非换课、非午餐背景，S5 | 15 |",
        "| 08:47–09:03，S1 | 43.75 |",
        "| 每小时47分至下一小时03分，S3，共九波 | 56.25 |",
        "| 18:47–19:03，S2 | 50 |",
        "| 11:30–13:30午餐叠加背景 | 135 |",
        "| 午餐叠加换课 | 176.25 |",
        "",
        "课程09:00开始，每节50分钟、课间10分钟，18:50结束；人流窗口前后各扩3分钟，"
        "课程波总量按10/16归一化。午餐增加120人次/5分钟，LG2/G入口选择60%/40%，"
        "进出双向均适用；其余时段40%/60%。低/高档为中档0.5/1.5倍，"
        "日总强度扰动0.85–1.15，各需求成分扰动0.90–1.10，课间峰值偏移±60秒。"
        "这些是模拟假设，不是实测人数。",
        "",
        "## Methodology",
        "",
        f"正式矩阵共{p['expected']['total']}条轨迹，包含短时持续人流、同状态分支及全天模拟，"
        "不应全部称为全天运行。开发种子4个、确认种子8个；原始记录位于raw目录。",
        "",
        "1. **先分人流选优**：7种流向混合 × 3档强度，持续均匀到达，"
        "前10分钟预热，后20分钟到达者用于评分；S6因稀疏改用后120分钟。"
        "需求强度取课表各模式平均值，"
        "不是把课间三角波直接视为平稳人流。4个开发种子选前两名；8个留出种子"
        "评价全部8种。开发冠军不因留出排名变化而替换。",
        "2. **选强固定参照**：另在完整全天重跑所有8种组合，按档位选择固定参照，"
        "另选跨档统一固定参照。流量低、中、高分别为0.5/1/1.5倍。",
        "3. **同状态切换**：从固定参照和直接按模式切换两种开发全天轨迹的"
        "12类代表性边界保存完整状态；留出同状态验证从固定参照起步。"
        "保持与两个候选的单组切换分支由深拷贝起步，车辆、乘客、门、呼梯完全相同。"
        "未来900秒实际输入相同，之后停止生成并排空；评分对象为当时仍候梯的人"
        "和后续到达者，统计从快照起的剩余等待。若某快照无候梯或未来乘客，"
        "明确标记empty_cohort并记为零等待机会，不解释为有乘客样本的均值。"
        "该截断不是无限期收益。",
        "4. **冻结局部切换门槛**：只用开发分支，按目标模式、原策略和本组"
        "clear/occupied/busy状态分类；先在每个种子内平均，至少3个种子，"
        "平均收益至少0.5秒、开发多重比较同时区间上界小于0，且满足尾部保护才切换。"
        "不满足则维持。只保留按此规则支持的切换，不强迫每个时段都换。",
        "5. **独立验证**：新种子上的同状态验证，以及整天固定/直接按模式切换/"
        "局部门控切换的真实轨迹。两组分别读取本组状态；训练收益可用全系统指标，"
        "在线决策不读取真实未来或另一组状态。",
        "",
        "统计以8个配对种子为单位；全天先在种子内部等权平均三档，"
        "不能把乘客或24个档位日当独立样本。全天3个主要对比用Holm校正p值"
        "和Bonferroni同时95%区间，同状态留出对比独立组成2项家族。"
        "条件冠军对7个其他组合共147项对比独立校正。尾部保护为点估计，"
        "不是统计非劣效证明。不显著不代表等效。",
        "",
        "## 各人流最佳候选",
        "",
        "详见 [条件排名表](conditional_rankings.csv)。其中frozen_champion是开发选定候选，"
        "heldout_point_best只是留出最低均值，不用于改规则；"
        "not_shown_worse_than_champion列表示尚未证明更差，不是证明等效或最优。",
        "",
        "| 档位 | S1 | S2 | S3 | S5 | S6 | 午餐+S3 | 午餐+S5 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for level in p["levels"]:
        lines.append("| "+level+" | "+" | ".join(ranking["ranking"][level][r][0]
                                                for r in p["regimes"])+" |")
    matches = sum(c["frozen_champion"] == c["heldout_point_best"] for c in conditional["conditions"])
    resolved = sum(c["simultaneous_ci"][0] > 0 for c in conditional["comparisons"])
    lines += [
        "",
        f"21个条件中，开发冠军也是留出最低均值者的有{matches}个。"
        f"147个校正后对比中，{resolved}项支持替代组合更差；"
        "其余未能辨认的差异不代表等效。没有用留出排名重新选择策略。",
        "",
        "名称：pickup=新呼梯接客代价；marginal=本组已分派呼梯的加权插入代价代理；"
        "uniform=本组三车分散停车；demand=本组已观察到的按钮按压起点停车；"
        "b0/b1=满载直驶关/开。控制器只看到二值呼梯代理，不能看到远端真实队列长度/OD；"
        "因此marginal不是拥有乘客计数真值的理想全知算法。",
        "",
        "## 全天留出结果",
        "",
        "| 策略 | 主步长平均等待/秒 | 细步长平均等待/秒 |",
        "|---|---:|---:|",
    ]
    for policy in ("fixed_level", "fixed_global", "naive", "gated"):
        lines.append(f"| {policy} | {daily['daily_eval']['means'][policy]:.4f} | "
                     f"{daily['daily_fine']['means'][policy]:.4f} |")
    lines += ["", "负差值表示优于各档固定参照：", "",
              "| 分辨率 | 策略 | 等待差/秒 | 同时95%区间 | Holm p | 两步长均达收益条件 |",
              "|---|---|---:|---|---:|---|"]
    for phase, results in daily.items():
        for r in results["contrasts"]:
            lines.append(f"| {phase} | {r['policy']} | {r['mean']:+.4f} | "
                         f"[{r['simultaneous_ci'][0]:+.4f}, {r['simultaneous_ci'][1]:+.4f}] | "
                         f"{r['p_holm']:.5g} | {r['verified_improvement']} |")
    lines += ["", "## 同状态留出验证", "",
              "| 分辨率 | 切换方式 | 剩余等待差/秒 | 同时95%区间 | Holm p |",
              "|---|---|---:|---|---:|"]
    for phase, results in transitions.items():
        for r in results:
            lines.append(f"| {phase} | {r['policy']} | {r['mean']:+.4f} | "
                         f"[{r['simultaneous_ci'][0]:+.4f}, {r['simultaneous_ci'][1]:+.4f}] | "
                         f"{r['p_holm']:.5g} |")
    counts_rules = {level: len(switching["rules"][level]) for level in p["levels"]}
    verified = [r["policy"] for r in daily["daily_eval"]["contrasts"] if r["verified_improvement"]]
    lines += ["", "## 结果解释与限制", "",
              f"- 固定参照按档位：`{ranking['fixed']}`；跨档：`{ranking['global_fixed']}`。",
              f"- 冻结后支持的局部切换规则数量：`{counts_rules}`。没有规则时保持，"
              "不能解释成已知原策略永远最优。",
              "- 若门控没有支持的动作，实际执行与固定参照相同；这不是不同控制"
              "被证明统计等效。开发种子较少、分组及多重校正较多，检验能力有限。",
              f"- 同时满足预声明主细步长收益条件的策略：`{verified}`。",
              "- 全日和同状态结果回答不同问题：前者包含长期状态反馈，后者是"
              "固定参照状态分布下900秒输入加排空的局部比较，不能互相代替。",
              "- 局部规则只在开发快照覆盖的原策略/状态上支持切换；连续切换进入"
              "未覆盖状态时会保持，不声称穷尽所有可达状态或最优切换路径。",
              "- 本轮没有证明全体算法或所有负荷的绝对最优，也没有覆盖所有提前量、"
              "所有可达状态、任意迟到或取消课程。开发选优仍可能过拟合。",
              "- 本实验不用于量化双组与统一调度的架构差异，未设置相应配对对照。",
              "- 研究仅为理论与模型实验，需求数值不是实测人数。",
              "",
              "## 复现与审计", "",
              "冻结协议见[protocol.json](protocol.json)，选优锁见[ranking.json](ranking.json)"
              "与[switching.json](switching.json)。原始服务、呼梯和停站记录位于raw目录，"
              "统计见[statistics.json](statistics.json)，审计见[audit.json](audit.json)。"
              "最终结论必须结合审计状态；该报告本身不替代审计通过证明。",
              ""]
    audit_path = root / "audit.json"
    if audit_path.exists():
        audit = load(audit_path)
        lines += [
            "## 验证结果",
            "",
            f"独立审计状态：**{'通过' if audit['passed'] else '未通过'}**。"
            f"重建{audit['reconstructed']['passengers']}条服务记录、"
            f"{audit['reconstructed']['decisions']}条决策，"
            f"完整回放{audit['full_replays']}条轨迹及"
            f"{audit['snapshot_branch_replays']}条同状态分支。",
            "这些记录含相同输入在不同策略、分支中的重复评价，不是独立乘客样本数量。",
            "",
        ]
    manifest_path = root / "source_manifest.json"
    if manifest_path.exists():
        validation = load(manifest_path)["cleanup_validation"]
        lines += [
            f"代码整理后通过{validation['unit_tests_passed']}项测试，并对"
            f"{validation['exact_replays']}条原始轨迹进行一致性回放。"
            "原始轨迹仅将执行目录替换为相对路径，输入、服务与决策数值不变。",
            "上述完整审计是实验完成时的审计；本次整理未重跑整套实验或完整审计。"
            "精简源码摘要见[source_manifest.json](source_manifest.json)。",
            "",
        ]
    (root / "report_zh.md").write_text("\n".join(lines), encoding="utf-8")
    return value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT)
    analyze(parser.parse_args().root)
