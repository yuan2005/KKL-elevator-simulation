"""实验入口：python -m simulation.experiments <smoke|e0|e1|e2|e3|all> [--seeds N] [--points N]

第二版实验结构（第一版的"分场景比较"和"单参数扫描"已并入新 E1；旧代码和结果见 results/archive/v1_E1-E4/）：
  E0  模型校验：①与 Barney 往返时间公式比对 ②复现文献"上行分区在标准办公楼减少约 40% 等待"
  E1  策略地图：在人流参数空间撒点，每点比较全部组合 → 贴最优标签 → 拟合决策树规则
  E2  全天验证：静态组合、按时段名称切换（反例）、查地图实时切换、用课表预测参数再查地图
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

from . import config as C
from .building import IDX, HEIGHT, flight_time, ENTRANCE_IDX
from .traffic import (SCENARIOS, Scenario, Passenger, generate_scenario, generate_day,
                      load_timetable)
from .sim import Sim
from .strategies import make_combos, Combo, ETA, EntranceParking
from .controllers import (Static, LabelSwitch, MapSwitch, ForecastMapSwitch, TreePolicy,
                          features, FEATURES)
from .metrics import summarize, mean_ci, paired_test

HERE = os.path.dirname(__file__)
RES = os.path.join(HERE, "results")
TIMETABLE = os.path.join(HERE, "data", "timetable_synthetic.csv")
TREE_PATH = os.path.join(RES, "e1_tree.json")
os.makedirs(RES, exist_ok=True)

MAP_COMBOS = ["C0", "C1", "C2", "C3", "C4", "C5", "C7"]   # E1 比较的组合（C6 依赖课表预测，不进地图）
SWITCHABLE = ["C1", "C2", "C3", "C4", "C7"]               # 按等待时间竞争的组合
ACCEPT_AWT, ACCEPT_LWT = 30.0, 5.0                         # 节能模式 C5 的"服务可接受"标准


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
                  profile="uniform", weights=UNIFORM_OFFICE, entrances={"G": 1.0}, stairs=False)
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
    """拉丁超立方抽样 + S1–S6 六个代表点。"""
    lhs = qmc.LatinHypercube(d=4, seed=seed).random(n_points)
    pts = []
    for i, (u_lam, u1, u2, u_a) in enumerate(lhs):
        lam5 = 3 + (320 - 3) * u_lam ** 2           # 平方映射：低客流和高客流都有足够的点
        a, b = sorted((u1, u2))
        split = (a, b - a, 1 - b)                    # (进楼, 离楼, 楼层间) 在单纯形上均匀
        sc = replace(SCENARIOS["S5"], name=f"P{i:03d}", lam5=lam5, split=split, batch_mean=1.4,
                     duration=900 if lam5 >= 20 else 1800, profile="uniform",
                     weights=mix_weights(u_a))
        pts.append(dict(point=f"P{i:03d}", alpha=u_a, sc=sc))
    for k, sc in SCENARIOS.items():
        pts.append(dict(point=k, alpha=0.0, sc=sc))
    return pts


def label_point(g, alpha=0.05):
    """g：一个点上所有组合 × 种子的结果。返回两个标签：
    eco_ok     只开 2 台（C5）时服务是否可接受（平均等待 ≤ 30 s 且 >60 s 的 ≤ 5%）
    wait_label 在 C1/C2/C3/C4/C7 中等待最短、且显著好过 C1 的组合；否则 C1（没把握就不换）
    label      最终标签：eco_ok 时为 C5，否则为 wait_label"""
    m = g.groupby("combo")[["awt", "lwt", "car_hours"]].mean()
    by = {ck: g[g.combo == ck].sort_values("seed") for ck in m.index}
    best = m.loc[SWITCHABLE].awt.idxmin()
    diff, p = paired_test(by[best].awt.values, by["C1"].awt.values)
    wait_label = best if (best != "C1" and diff < 0 and p < alpha) else "C1"
    eco_ok = bool(m.loc["C5", "awt"] <= ACCEPT_AWT and m.loc["C5", "lwt"] <= ACCEPT_LWT)
    out = dict(label="C5" if eco_ok else wait_label, wait_label=wait_label, eco_ok=eco_ok,
               best_by_mean=best, best_gain_vs_C1=diff, p_best_vs_C1=p,
               C1_vs_C0_pct=(m.loc["C1", "awt"] / m.loc["C0", "awt"] - 1) * 100)
    for ck in m.index:
        out[f"awt_{ck}"] = m.loc[ck, "awt"]
        out[f"lwt_{ck}"] = m.loc[ck, "lwt"]
        out[f"carh_{ck}"] = m.loc[ck, "car_hours"]
    for f in FEATURES:
        out[f] = g[f].mean()
    return out


def tree_nodes(clf):
    t = clf.tree_
    return [dict(f=-1 if t.children_left[i] == -1 else int(t.feature[i]), thr=float(t.threshold[i]),
                 l=int(t.children_left[i]), r=int(t.children_right[i]),
                 label=str(clf.classes_[int(np.argmax(t.value[i]))]), n=int(t.n_node_samples[i]))
            for i in range(t.node_count)]


def wait_regret(points, pred):
    """用 pred 代替每个点的 wait_label，平均多等多少秒（越小越好，0 = 每个点都选对）。"""
    return np.array([row[f"awt_{k}"] - row[f"awt_{row['wait_label']}"]
                     for (_, row), k in zip(points.iterrows(), pred)])


NAMES_CN = {"lam5": "每5分钟乘梯人数", "s_in": "进楼比例", "s_out": "离楼比例", "s_high": "进楼去5F以上比例"}


def fit_maps(points, rng_seed=0):
    """第 1 步：节能判断树（误判为节能罚 5 倍，偏保守）；第 2 步：等待最优树（按 5 折交叉验证的后悔值选深度）。"""
    from sklearn.tree import DecisionTreeClassifier, export_text
    from sklearn.model_selection import KFold
    X = points[FEATURES].values
    names = [NAMES_CN[f] for f in FEATURES]

    y_eco = points["eco_ok"].astype(str).values
    eco = DecisionTreeClassifier(max_depth=2, min_samples_leaf=8, class_weight={"False": 5.0, "True": 1.0},
                                 random_state=0).fit(X, y_eco)
    pe = eco.predict(X)
    eco_stats = dict(false_eco=int(((pe == "True") & (y_eco == "False")).sum()),
                     missed_eco=int(((pe == "False") & (y_eco == "True")).sum()),
                     n_eco=int((y_eco == "True").sum()), n=len(y_eco))

    y = points["wait_label"].values
    rows = []
    for depth in range(1, 7):
        accs, regs = [], []
        for tr, te in KFold(5, shuffle=True, random_state=rng_seed).split(X):
            clf = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=8, random_state=0).fit(X[tr], y[tr])
            pred = clf.predict(X[te])
            accs.append((pred == y[te]).mean())
            regs.append(wait_regret(points.iloc[te], pred).mean())
        rows.append(dict(depth=depth, cv_accuracy=np.mean(accs), cv_regret_s=np.mean(regs)))
    cv = pd.DataFrame(rows)
    depth = int(cv[cv.cv_regret_s <= cv.cv_regret_s.min() + 0.1].depth.min())   # 差不多好时取最简单的树
    wait = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=8, random_state=0).fit(X, y)

    with open(TREE_PATH, "w", encoding="utf-8") as f:
        json.dump(dict(features=FEATURES, eco=tree_nodes(eco), wait=tree_nodes(wait)), f,
                  ensure_ascii=False, indent=1)
    text = ("【第 1 步 节能判断】True = 可以只开 2 台\n" + export_text(eco, feature_names=names, class_names=list(eco.classes_), decimals=2)
            + f"\n【第 2 步 等待最优】深度 {depth}\n" + export_text(wait, feature_names=names, class_names=list(wait.classes_), decimals=2))
    return cv, depth, eco_stats, text


def e1(seeds=10, points=400):
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
    cv, depth, eco_stats, text = fit_maps(train)
    cv.to_csv(os.path.join(RES, "e1_tree_cv.csv"), index=False)
    with open(os.path.join(RES, "e1_rules.txt"), "w", encoding="utf-8") as f:
        f.write(text)

    # 总结
    print("\n等待最优标签的点数：", train.wait_label.value_counts().to_dict())
    print(f"节能可接受的点：{eco_stats['n_eco']}/{eco_stats['n']}；节能判断树误判为节能 {eco_stats['false_eco']} 个，"
          f"漏判 {eco_stats['missed_eco']} 个")
    print("\n等待最优树：不同深度的 5 折交叉验证（后悔 = 相对每点最优组合多等的秒数）：")
    print(cv.round(3).to_string(index=False))
    pol = TreePolicy(TREE_PATH)
    pred = [pol.predict(r, eco=False) for _, r in train.iterrows()]
    print(f"\n选用深度 {depth}。训练集上平均后悔：查地图 {wait_regret(train, pred).mean():.2f}s，"
          f"一直用 C1 {wait_regret(train, ['C1'] * len(train)).mean():.2f}s，"
          f"一直用 C0 {wait_regret(train, ['C0'] * len(train)).mean():.2f}s")
    print("\n" + text)
    ref = points_df[~points_df.point.str.startswith("P")][["point", "lam5", "s_in", "s_out", "s_high", "label",
                                                          "awt_C0", "awt_C1", "awt_C7", "awt_C2", "awt_C5"]]
    ref = ref.assign(map_choice=[pol.predict(r) for _, r in ref.iterrows()],
                     map_choice_no_eco=[pol.predict(r, eco=False) for _, r in ref.iterrows()])
    print("S1–S6 代表点：\n" + ref.round(2).to_string(index=False))
    ref.to_csv(os.path.join(RES, "e1_reference_points.csv"), index=False)


# ================================================================ E2 全天验证
DAY_LEN = 11 * 3600
FORECAST_SAMPLES = 5
CTRLS = ["静态 C0", "静态 C1", "按时段名称切换", "地图实时", "地图实时+节能", "课表预测+地图", "课表预测+地图+节能"]


def build_controller(name, combos, tt):
    if name.startswith("静态"):
        return Static(combos[name.split()[1]])
    if name == "按时段名称切换":
        # 第一版的做法：每类时段固定用第一版分场景实验里对应场景的最优组合
        return LabelSwitch(combos, tt, {"in": "C7", "out": "C6", "mix": "C1", "lunch": "C1", "normal": "C1"})
    pol = TreePolicy(TREE_PATH)
    eco = name.endswith("节能")
    if name.startswith("地图实时"):
        return MapSwitch(combos, pol, eco=eco)
    fc = []
    for k in range(FORECAST_SAMPLES):      # 预测只用课表 + 名义出席率 0.8，种子与实际不同
        fc += generate_day(tt, 1000 + k, attendance=0.8)
    return ForecastMapSwitch(combos, pol, fc, FORECAST_SAMPLES, eco=eco)


def run_day(args):
    ctrl_name, seed, attendance, broken = args
    tt = load_timetable(TIMETABLE)
    combos = make_combos([(c["end"], c["floor"], c["size"] * 0.8) for c in tt])
    pax = generate_day(tt, seed, attendance=attendance)
    sim = Sim(pax, build_controller(ctrl_name, combos, tt), gen_end=DAY_LEN, broken_cars=broken).run()
    m = summarize(sim)
    wins = [(c["start"] - 600, c["start"] + 120) for c in tt] + [(c["end"], c["end"] + 360) for c in tt]
    pk = summarize(sim, keep=lambda p: any(a <= p.t <= b for a, b in wins))
    m.update({f"peak_{k}": pk[k] for k in ("n", "awt", "p90", "lwt")})
    m.update(controller=ctrl_name, seed=seed, attendance=attendance, switches=len(sim.combo_log))
    log = sim.combo_log + [(DAY_LEN, None)]
    m["_intervals"] = [(a, b, n.split()[0]) for (a, n), (b, _) in zip(log, log[1:]) if b > a]
    m["_waits"] = [((p.t_board if p.t_board is not None else sim.t) - p.t) for p in pax]
    m["_times"] = [p.t for p in pax]
    return m


def switch_period_table(runs, base_name="静态 C1", alpha=0.05):
    """只看"切到非 C1 组合"的时段：同一批乘客在切换和静态 C1 下的等待对比。"""
    base = {(r["seed"], r["attendance"]): r for r in runs if r["controller"] == base_name}
    rows = []
    for r in runs:
        if r["controller"].startswith("静态"):
            continue
        b = base[(r["seed"], r["attendance"])]
        times = np.array(r["_times"])
        for combo in sorted({iv[2] for iv in r["_intervals"]} - {"C1"}):
            mask = np.zeros(len(times), bool)
            for a, e, k in r["_intervals"]:
                if k == combo:
                    mask |= (times >= a) & (times < e)
            if mask.any():
                w, wb = np.array(r["_waits"])[mask], np.array(b["_waits"])[mask]
                rows.append(dict(controller=r["controller"], attendance=r["attendance"], seed=r["seed"],
                                 combo=combo, n=int(mask.sum()), awt_switch=w.mean(), awt_C1=wb.mean(),
                                 lwt_switch=(w > 60).mean() * 100, lwt_C1=(wb > 60).mean() * 100))
    d = pd.DataFrame(rows)
    if d.empty:
        return d
    out = []
    for (c, a, k), g in d.groupby(["controller", "attendance", "combo"]):
        p = stats.ttest_rel(g.awt_switch, g.awt_C1).pvalue if len(g) > 1 else np.nan
        out.append(dict(controller=c, attendance=a, combo=k, days=len(g), pax_per_day=g.n.mean(),
                        awt_switch=g.awt_switch.mean(), awt_C1=g.awt_C1.mean(),
                        change_pct=(g.awt_switch.mean() / g.awt_C1.mean() - 1) * 100, p=p,
                        lwt_switch=g.lwt_switch.mean(), lwt_C1=g.lwt_C1.mean()))
    return pd.DataFrame(out)


def day_summary(df, base_name="静态 C1"):
    rows = []
    for (a, c), g in df.groupby(["attendance", "controller"]):
        g = g.sort_values("seed")
        b = df[(df.attendance == a) & (df.controller == base_name)].sort_values("seed")
        d_all, p_all = paired_test(g.awt.values, b.awt.values)
        d_pk, p_pk = paired_test(g.peak_awt.values, b.peak_awt.values)
        m, h = mean_ci(g.awt)
        rows.append(dict(attendance=a, controller=c, awt=m, awt_ci=h, lwt=g.lwt.mean(), peak_awt=g.peak_awt.mean(),
                         peak_lwt=g.peak_lwt.mean(), car_hours=g.car_hours.mean(), distance_km=g.distance_km.mean(),
                         vs_C1=d_all, p_vs_C1=p_all, peak_vs_C1=d_pk, p_peak_vs_C1=p_pk,
                         switches=g.switches.mean()))
    return pd.DataFrame(rows)


def _strip(runs):
    return pd.DataFrame([{k: v for k, v in r.items() if not k.startswith("_")} for r in runs])


def e2(seeds=10):
    tasks = [(c, s, a, ()) for a in (0.64, 0.8, 0.96, 1.6, 2.4) for c in CTRLS for s in range(seeds)]
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

    # 节假日：全天很低客流，看地图会不会选节能模式
    hol = replace(SCENARIOS["S6"], duration=DAY_LEN)
    rows = []
    for s in range(seeds):
        pax, _ = generate_scenario(hol, s)
        combos = make_combos()
        for name, ctrl in [("静态 C1", Static(combos["C1"])),
                           ("地图实时+节能", MapSwitch(combos, TreePolicy(TREE_PATH)))]:
            pax_s = [Passenger(p.id, p.t, p.o, p.d, p.group) for p in pax]
            sim = Sim(pax_s, ctrl, gen_end=DAY_LEN).run()
            m = summarize(sim)
            m.update(controller=name, seed=s)
            rows.append(m)
    h = pd.DataFrame(rows).groupby("controller")[["awt", "p90", "lwt", "car_hours", "distance_km"]].mean()
    h.to_csv(os.path.join(RES, "e2_holiday.csv"))
    print("\n节假日：\n" + h.round(2).to_string())


# ================================================================ E3 稳健性
def e3(seeds=10):
    ctrls = ["静态 C0", "静态 C1", "地图实时", "课表预测+地图"]
    tasks = [(c, s, a, (0,)) for a in (0.8, 1.6) for c in ctrls for s in range(seeds)]
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
    ap.add_argument("--points", type=int, default=400, help="E1 抽样点数")
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
