"""实验入口（在 simulation 文件夹里运行）：python3 experiments.py <smoke|e0|e1|e2|e3|all> [--seeds N] [--points N]

第二版实验结构（第一版的"分场景比较"和"单参数扫描"已并入新 E1；旧代码和结果见 results/archive/v1_E1-E4/）：
  E0  模型校验：①与 Barney 往返时间公式比对 ②复现文献"上行分区在标准办公楼减少约 40% 等待"
  E1  策略地图：在 5 维人流参数空间撒点（人数、进楼/离楼/楼层间、过路 LG2↔G、楼层分布），
      每点比较全部组合 → 贴"等待最短"标签 → 拟合决策树规则
  E2  全天验证：静态组合、按时段名称切换（反例）、查地图实时切换、用课表预测参数再查地图；
      不同出席率，以及实际过路比例 10%/30%/50%（预测按 30%）
  E3  稳健性：一台电梯停运时，查地图切换是否仍然有效
所有比较都用"相同随机数"：同一个种子下，各组合面对完全相同的乘客。
"""
import argparse
import json
import os
import time
from dataclasses import replace
from multiprocessing import Pool

import numpy as np
import pandas as pd
from scipy import stats
from scipy.stats import qmc

import config as C
from building import IDX, HEIGHT, flight_time, ENTRANCE_IDX
from traffic import (SCENARIOS, Scenario, Passenger, generate_scenario, generate_day,
                      load_timetable)
from sim import Sim
from strategies import make_combos, Combo, ETA, EntranceParking
from controllers import (Static, LabelSwitch, MapSwitch, ForecastMapSwitch, TreePolicy,
                          features, FEATURES)
from metrics import summarize, mean_ci, paired_test

HERE = os.path.dirname(__file__)
RES = os.path.join(HERE, "results")
TIMETABLE = os.path.join(HERE, "data", "timetable_synthetic.csv")
TREE_PATH = os.path.join(RES, "e1_tree.json")
os.makedirs(RES, exist_ok=True)

MAP_COMBOS = ["C0", "C1", "C2", "C3", "C4", "C7"]         # E1 比较的组合（C6 依赖课表预测，不进地图）
SWITCHABLE = ["C1", "C2", "C3", "C4", "C7"]               # 竞争"最优"的组合（C0 是现状，只作参照）
THRU_MAX = 0.6                                             # E1 地图里过路比例的测试上限


def pool_map(fn, tasks, chunksize=4):
    with Pool(max(1, os.cpu_count() - 1)) as pool:
        return pool.map(fn, tasks, chunksize=chunksize)


def sc_dict(sc, **kw):
    d = dict(sc.__dict__)
    d.update(kw)
    return d


def run_scenario(args):
    """args = (scenario 字段字典, 组合键, 种子, 故障电梯)"""
    sc_fields, ck, seed, broken = args
    sc = Scenario(**sc_fields)
    pax, stairs = generate_scenario(sc, seed)
    sim = Sim(pax, Static(make_combos()[ck]), gen_end=sc.duration, broken_cars=broken).run()
    m = summarize(sim)
    m.update(combo=ck, seed=seed, stairs=stairs)
    m.update(features(pax, sc.duration))         # 观测得到的人流参数（楼梯分流之后的乘梯乘客）
    return m


def mix_weights(alpha):
    """α=0：KKL 课室集中在低层的楼层权重；α=1：各层均匀的办公楼权重。"""
    uni = {f: (1.0 if (f not in C.ENTRANCES and f != "LG3") else 0.0) for f in C.FLOORS}
    tot_c = sum(C.FLOOR_WEIGHTS_CLASS.values())
    tot_u = sum(uni.values())
    return {f: (1 - alpha) * C.FLOOR_WEIGHTS_CLASS.get(f, 0) / tot_c + alpha * uni[f] / tot_u
            for f in C.FLOORS}


# ================================================================ smoke / E0
def smoke():
    combos = make_combos()
    p = Passenger(0, 5.0, IDX["G"], IDX["10"], 1)
    Sim([p], Static(combos["C1"]), gen_end=10).run()
    fl = flight_time(HEIGHT[IDX["10"]] - HEIGHT[IDX["G"]])
    expect_board = 5.0 + C.T_DOOR_OPEN + C.T_PASSENGER
    expect_alight = expect_board + C.T_DOOR_CLOSE + fl + C.T_DOOR_OPEN + C.T_PASSENGER
    print(f"单个乘客 G→10/F：上梯 {p.t_board:.2f}（手算 {expect_board:.2f}），"
          f"到达 {p.t_alight:.2f}（手算约 {expect_alight:.2f}，误差来自 {C.DT}s 步长）")
    assert abs(p.t_board - expect_board) <= C.DT + 1e-6
    assert abs(p.t_alight - expect_alight) <= 2 * C.DT + 0.5
    print("smoke 通过")


def barney_rtt(P, N, df=C.FLOOR_HEIGHT):
    """Barney 上行高峰往返时间公式：RTT = 2H·tv + (S+1)·ts + 2P·tp"""
    H = N - sum((i / N) ** P for i in range(1, N))
    S = N * (1 - (1 - 1 / N) ** P)
    tv = df / C.RATED_SPEED
    ts = flight_time(df) - tv + C.T_DOOR_OPEN + C.T_DOOR_CLOSE
    return 2 * H * tv + (S + 1) * ts + 2 * P * C.T_PASSENGER, H, S


UNIFORM_OFFICE = {f: (1.0 if f.isdigit() else 0.0) for f in C.FLOORS}


def e0(seeds=10):
    # ① 单入口(G)、纯上行、各层均匀的饱和上行高峰：仿真 RTT vs 解析公式
    sc = Scenario("E0 上行高峰校验", lam5=400, split=(1, 0, 0), batch_mean=1.0, duration=1800,
                  profile="uniform", weights=UNIFORM_OFFICE, entrances={"G": 1.0}, stairs=False, thru=0.0)
    combo = Combo("校验:ETA+G层停靠(不分区)", ETA(), EntranceParking({IDX["G"]: 1.0}), bypass=False)
    rows = []
    for seed in range(seeds):
        pax, _ = generate_scenario(sc, seed)
        sim = Sim(pax, Static(combo), gen_end=sc.duration).run()
        rtts, loads = [], []
        for car in sim.cars:
            dep = [(t, n) for t, n in car.lobby_departures if 300 <= t <= sc.duration]
            for (t1, n1), (t2, _) in zip(dep, dep[1:]):
                rtts.append(t2 - t1)
                loads.append(n1)
        P = float(np.mean(loads))
        rtt_f, H, S = barney_rtt(P, 16)
        rows.append(dict(seed=seed, rtt_sim=np.mean(rtts), P=P, rtt_formula=rtt_f, H=H, S=S))
    df = pd.DataFrame(rows)
    df["err_pct"] = (df.rtt_sim - df.rtt_formula) / df.rtt_formula * 100
    df.to_csv(os.path.join(RES, "e0_rtt_validation.csv"), index=False)
    print(f"① 往返时间：仿真 {df.rtt_sim.mean():.1f}s，公式 {df.rtt_formula.mean():.1f}s，"
          f"平均偏差 {df.err_pct.mean():.1f}%（目标 |偏差| ≤ 15%）")

    # ② 标准办公楼（单入口、各层均匀）的重上行高峰：上行分区 C2 vs ETA C1
    tasks, meta = [], []
    for lam in (200, 300):
        s2 = replace(sc, lam5=lam, split=(0.95, 0.0, 0.05), duration=900)
        for ck in ("C1", "C2"):
            for s in range(seeds):
                tasks.append((sc_dict(s2), ck, s, ()))
                meta.append(lam)
    out = pd.DataFrame(pool_map(run_scenario, tasks))
    out["lam5"] = meta
    rows = []
    for lam, g in out.groupby("lam5"):
        a = g[g.combo == "C2"].sort_values("seed").awt.values
        b = g[g.combo == "C1"].sort_values("seed").awt.values
        diff, p = paired_test(a, b)
        rows.append(dict(lam5=lam, awt_C1=b.mean(), awt_C2=a.mean(), change_pct=diff / b.mean() * 100, p=p))
    lit = pd.DataFrame(rows)
    lit.to_csv(os.path.join(RES, "e0_literature_zoning.csv"), index=False)
    print("② 标准办公楼上行分区（文献：重上行高峰时分区明显提高效率）")
    print(lit.round(3).to_string(index=False))


# ================================================================ E1 策略地图
def e1_points(n_points, seed=2026):
    """拉丁超立方抽样（5 维）+ S1–S6 六个代表点（代表点的过路比例取默认 30%）。"""
    lhs = qmc.LatinHypercube(d=5, seed=seed).random(n_points)
    pts = []
    for i, (u_lam, u1, u2, u_a, u_t) in enumerate(lhs):
        lam5 = 3 + (320 - 3) * u_lam ** 2           # 平方映射：低客流和高客流都有足够的点
        a, b = sorted((u1, u2))
        split = (a, b - a, 1 - b)                    # 非过路乘客的 (进楼, 离楼, 楼层间)，在单纯形上均匀
        thru = THRU_MAX * u_t                        # 过路比例 0–60%
        sc = replace(SCENARIOS["S5"], name=f"P{i:03d}", lam5=lam5, split=split, batch_mean=1.4,
                     duration=900 if lam5 >= 20 else 1800, profile="uniform",
                     weights=mix_weights(u_a), thru=thru)
        pts.append(dict(point=f"P{i:03d}", alpha=u_a, sc=sc))
    for k, sc in SCENARIOS.items():
        pts.append(dict(point=k, alpha=0.0, sc=sc))
    return pts


def label_point(g, alpha=0.05):
    """g：一个点上所有组合 × 种子的结果。
    label：在 C1/C2/C3/C4/C7 中平均等待最短、且显著好过 C1（配对 t 检验 p<0.05）的组合；
           否则 C1（没把握就不换）。"""
    m = g.groupby("combo")[["awt", "lwt", "p90"]].mean()
    by = {ck: g[g.combo == ck].sort_values("seed") for ck in m.index}
    best = m.loc[SWITCHABLE].awt.idxmin()
    diff, p = paired_test(by[best].awt.values, by["C1"].awt.values)
    out = dict(label=best if (best != "C1" and diff < 0 and p < alpha) else "C1",
               best_by_mean=best, best_gain_vs_C1=diff, p_best_vs_C1=p,
               C1_vs_C0_pct=(m.loc["C1", "awt"] / m.loc["C0", "awt"] - 1) * 100)
    for ck in m.index:
        out[f"awt_{ck}"] = m.loc[ck, "awt"]
        out[f"lwt_{ck}"] = m.loc[ck, "lwt"]
        out[f"p90_{ck}"] = m.loc[ck, "p90"]
    for f in FEATURES:
        out[f] = g[f].mean()
    return out


def tree_nodes(clf):
    t = clf.tree_
    return [dict(f=-1 if t.children_left[i] == -1 else int(t.feature[i]), thr=float(t.threshold[i]),
                 l=int(t.children_left[i]), r=int(t.children_right[i]),
                 label=str(clf.classes_[int(np.argmax(t.value[i]))]), n=int(t.n_node_samples[i]))
            for i in range(t.node_count)]


def regret(points, pred):
    """用 pred 代替每个点的最优标签，平均多等多少秒（越小越好，0 = 每个点都选对）。"""
    return np.array([row[f"awt_{k}"] - row[f"awt_{row['label']}"]
                     for (_, row), k in zip(points.iterrows(), pred)])


NAMES_CN = {"lam5": "每5分钟乘梯人数", "s_in": "进楼比例", "s_out": "离楼比例", "s_thru": "过路比例",
            "s_from_ent": "从入口层出发比例", "s_to_ent": "去入口层比例", "s_high": "进楼去5F以上比例"}


def fit_map(points, rng_seed=0):
    """拟合策略地图（决策树）：深度按 5 折交叉验证的后悔值选，差不多好时取最简单的树。"""
    from sklearn.tree import DecisionTreeClassifier, export_text
    from sklearn.model_selection import KFold
    X = points[FEATURES].values
    y = points["label"].values
    rows = []
    for depth in range(1, 7):
        accs, regs = [], []
        for tr, te in KFold(5, shuffle=True, random_state=rng_seed).split(X):
            clf = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=8, random_state=0).fit(X[tr], y[tr])
            pred = clf.predict(X[te])
            accs.append((pred == y[te]).mean())
            regs.append(regret(points.iloc[te], pred).mean())
        rows.append(dict(depth=depth, cv_accuracy=np.mean(accs), cv_regret_s=np.mean(regs)))
    cv = pd.DataFrame(rows)
    # 后悔值比最小值多不到 3% 时取最简单的树（大部分点 C1 本来就最优，平均后悔很小，所以用相对容差）
    depth = int(cv[cv.cv_regret_s <= cv.cv_regret_s.min() * 1.03].depth.min())
    clf = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=8, random_state=0).fit(X, y)
    with open(TREE_PATH, "w", encoding="utf-8") as f:
        json.dump(dict(features=FEATURES, nodes=tree_nodes(clf)), f, ensure_ascii=False, indent=1)
    text = f"策略地图（决策树深度 {depth}）\n" + export_text(
        clf, feature_names=[NAMES_CN[f] for f in FEATURES], class_names=list(clf.classes_), decimals=2)
    return cv, depth, text


def e1(seeds=10, points=500):
    pts = e1_points(points)
    tasks, meta = [], []
    for p in pts:
        for ck in MAP_COMBOS:
            for s in range(seeds):
                tasks.append((sc_dict(p["sc"]), ck, s, ()))
                meta.append((p["point"], p["alpha"]))
    t0 = time.time()
    raw = pd.DataFrame(pool_map(run_scenario, tasks, chunksize=8))
    raw["point"] = [m[0] for m in meta]
    raw["alpha"] = [m[1] for m in meta]
    raw.to_csv(os.path.join(RES, "e1_raw.csv"), index=False)
    print(f"仿真 {len(tasks)} 次，用时 {time.time() - t0:.0f} 秒")

    rows = []
    for pt, g in raw.groupby("point", sort=False):
        r = label_point(g)
        r.update(point=pt, alpha=g.alpha.iloc[0])
        rows.append(r)
    points_df = pd.DataFrame(rows)
    points_df.to_csv(os.path.join(RES, "e1_points.csv"), index=False)

    train = points_df[points_df.point.str.startswith("P")].reset_index(drop=True)
    cv, depth, text = fit_map(train)
    cv.to_csv(os.path.join(RES, "e1_tree_cv.csv"), index=False)
    with open(os.path.join(RES, "e1_rules.txt"), "w", encoding="utf-8") as f:
        f.write(text)

    print("\n最优标签的点数：", train.label.value_counts().to_dict())
    print("\n不同树深度的 5 折交叉验证（后悔 = 相对每点最优组合多等的秒数）：")
    print(cv.round(3).to_string(index=False))
    pol = TreePolicy(TREE_PATH)
    pred = [pol.predict(r) for _, r in train.iterrows()]
    print(f"\n选用深度 {depth}。训练集上平均后悔：查地图 {regret(train, pred).mean():.2f}s，"
          f"一直用 C1 {regret(train, ['C1'] * len(train)).mean():.2f}s，"
          f"一直用 C0 {regret(train, ['C0'] * len(train)).mean():.2f}s")
    print("\n" + text)
    ref = points_df[~points_df.point.str.startswith("P")][["point"] + FEATURES + [
        "label", "awt_C0", "awt_C1", "awt_C7", "awt_C2", "awt_C4"]]
    ref = ref.assign(map_choice=[pol.predict(r) for _, r in ref.iterrows()])
    print("S1–S6 代表点：\n" + ref.round(2).to_string(index=False))
    ref.to_csv(os.path.join(RES, "e1_reference_points.csv"), index=False)


# ================================================================ E2 全天验证
DAY_LEN = 11 * 3600
FORECAST_SAMPLES = 5
CTRLS = ["静态 C0", "静态 C1", "按时段名称切换", "地图实时", "课表预测+地图"]
# (出席率, 实际过路比例)：课表预测永远按名义出席率 0.8、过路 30% 预测
CONDITIONS = [(0.64, 0.3), (0.8, 0.3), (0.96, 0.3), (1.6, 0.3), (2.4, 0.3), (0.8, 0.1), (0.8, 0.5)]


def cond_name(att, thru):
    return f"出席{att:g}/过路{int(round(thru * 100))}%"


def build_controller(name, combos, tt):
    if name.startswith("静态"):
        return Static(combos[name.split()[1]])
    if name == "按时段名称切换":
        # 第一版的做法：每类时段固定用第一版分场景实验里对应场景的最优组合
        return LabelSwitch(combos, tt, {"in": "C7", "out": "C6", "mix": "C1", "lunch": "C1", "normal": "C1"})
    pol = TreePolicy(TREE_PATH)
    if name == "地图实时":
        return MapSwitch(combos, pol)
    fc = []
    for k in range(FORECAST_SAMPLES):      # 预测只用课表 + 名义出席率 0.8 + 过路 30%，种子与实际不同
        fc += generate_day(tt, 1000 + k, attendance=0.8, thru_share=C.THRU_SHARE)
    return ForecastMapSwitch(combos, pol, fc, FORECAST_SAMPLES)


def run_day(args):
    ctrl_name, seed, attendance, thru, broken = args
    tt = load_timetable(TIMETABLE)
    combos = make_combos([(c["end"], c["floor"], c["size"] * 0.8) for c in tt])
    pax = generate_day(tt, seed, attendance=attendance, thru_share=thru)
    sim = Sim(pax, build_controller(ctrl_name, combos, tt), gen_end=DAY_LEN, broken_cars=broken).run()
    m = summarize(sim)
    wins = [(c["start"] - 600, c["start"] + 120) for c in tt] + [(c["end"], c["end"] + 360) for c in tt]
    pk = summarize(sim, keep=lambda p: any(a <= p.t <= b for a, b in wins))
    m.update({f"peak_{k}": pk[k] for k in ("n", "awt", "p90", "lwt")})
    m.update(controller=ctrl_name, seed=seed, attendance=attendance, thru=thru,
             cond=cond_name(attendance, thru), switches=len(sim.combo_log))
    log = sim.combo_log + [(DAY_LEN, None)]
    m["_intervals"] = [(a, b, n.split()[0]) for (a, n), (b, _) in zip(log, log[1:]) if b > a]
    m["_waits"] = [((p.t_board if p.t_board is not None else sim.t) - p.t) for p in pax]
    m["_times"] = [p.t for p in pax]
    return m


def switch_period_table(runs, base_name="静态 C1"):
    """只看"切到非 C1 组合"的时段：同一批乘客在切换和静态 C1 下的等待对比。"""
    base = {(r["seed"], r["cond"]): r for r in runs if r["controller"] == base_name}
    rows = []
    for r in runs:
        if r["controller"].startswith("静态"):
            continue
        b = base[(r["seed"], r["cond"])]
        times = np.array(r["_times"])
        for combo in sorted({iv[2] for iv in r["_intervals"]} - {"C1"}):
            mask = np.zeros(len(times), bool)
            for a, e, k in r["_intervals"]:
                if k == combo:
                    mask |= (times >= a) & (times < e)
            if mask.any():
                w, wb = np.array(r["_waits"])[mask], np.array(b["_waits"])[mask]
                rows.append(dict(controller=r["controller"], cond=r["cond"], seed=r["seed"], combo=combo,
                                 n=int(mask.sum()), awt_switch=w.mean(), awt_C1=wb.mean(),
                                 lwt_switch=(w > 60).mean() * 100, lwt_C1=(wb > 60).mean() * 100))
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    out = []
    for (c, cond, k), g in d.groupby(["controller", "cond", "combo"], sort=False):
        p = stats.ttest_rel(g.awt_switch, g.awt_C1).pvalue if len(g) > 1 else np.nan
        out.append(dict(controller=c, cond=cond, combo=k, days=len(g), pax_per_day=g.n.mean(),
                        awt_switch=g.awt_switch.mean(), awt_C1=g.awt_C1.mean(),
                        change_pct=(g.awt_switch.mean() / g.awt_C1.mean() - 1) * 100, p=p,
                        lwt_switch=g.lwt_switch.mean(), lwt_C1=g.lwt_C1.mean()))
    return pd.DataFrame(out)


def day_summary(df, base_name="静态 C1"):
    rows = []
    for (cond, c), g in df.groupby(["cond", "controller"], sort=False):
        g = g.sort_values("seed")
        b = df[(df.cond == cond) & (df.controller == base_name)].sort_values("seed")
        d_all, p_all = paired_test(g.awt.values, b.awt.values)
        d_pk, p_pk = paired_test(g.peak_awt.values, b.peak_awt.values)
        m, h = mean_ci(g.awt)
        rows.append(dict(cond=cond, controller=c, awt=m, awt_ci=h, p90=g.p90.mean(), lwt=g.lwt.mean(),
                         peak_awt=g.peak_awt.mean(), peak_lwt=g.peak_lwt.mean(),
                         vs_C1=d_all, p_vs_C1=p_all, peak_vs_C1=d_pk, p_peak_vs_C1=p_pk,
                         switches=g.switches.mean()))
    return pd.DataFrame(rows)


def _strip(runs):
    return pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in runs])


def e2(seeds=10):
    tasks = [(c, s, a, t, ()) for a, t in CONDITIONS for c in CTRLS for s in range(seeds)]
    runs = pool_map(run_day, tasks, chunksize=2)
    df = _strip(runs)
    df.to_csv(os.path.join(RES, "e2_raw.csv"), index=False)
    summ = day_summary(df)
    summ.to_csv(os.path.join(RES, "e2_summary.csv"), index=False)
    sw = switch_period_table(runs)
    sw.to_csv(os.path.join(RES, "e2_switch_periods.csv"), index=False)
    pd.set_option("display.width", 220)
    print("全天汇总（对比静态 C1，peak = 上下课窗口内）：")
    print(summ.round(3).to_string(index=False))
    print("\n只看切换生效时段（同一批乘客对比静态 C1）：")
    print(sw.round(3).to_string(index=False))


# ================================================================ E3 稳健性
def e3(seeds=10):
    ctrls = ["静态 C0", "静态 C1", "地图实时", "课表预测+地图"]
    tasks = [(c, s, a, C.THRU_SHARE, (0,)) for a in (0.8, 1.6) for c in ctrls for s in range(seeds)]
    runs = pool_map(run_day, tasks, chunksize=2)
    df = _strip(runs)
    summ = day_summary(df)
    summ.to_csv(os.path.join(RES, "e3_one_car_out.csv"), index=False)
    sw = switch_period_table(runs)
    sw.to_csv(os.path.join(RES, "e3_switch_periods.csv"), index=False)
    print("一台电梯停运（地图仍按 6 台训练）：")
    print(summ.round(3).to_string(index=False))
    print(sw.round(3).to_string(index=False))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", choices=["smoke", "e0", "e1", "e2", "e3", "all"])
    ap.add_argument("--seeds", type=int, default=None)
    ap.add_argument("--points", type=int, default=500, help="E1 抽样点数")
    a = ap.parse_args()
    t0 = time.time()
    kw = {} if a.seeds is None else {"seeds": a.seeds}
    if a.exp in ("smoke", "all"):
        smoke()
    if a.exp in ("e0", "all"):
        print("\n===== E0 模型校验 =====")
        e0(**kw)
    if a.exp in ("e1", "all"):
        print("\n===== E1 策略地图 =====")
        e1(points=a.points, **kw)
    if a.exp in ("e2", "all"):
        print("\n===== E2 全天验证 =====")
        e2(**kw)
    if a.exp in ("e3", "all"):
        print("\n===== E3 稳健性 =====")
        e3(**kw)
    print(f"\n用时 {time.time() - t0:.0f} 秒")
