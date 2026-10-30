"""实验入口：python -m simulation.experiments <smoke|e0|e1|e2|e3|e4|all> [--seeds N]

结果写到 simulation/results/*.csv，图由 plots.py 生成。
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

from . import config as C
from .building import IDX, HEIGHT, flight_time, ENTRANCE_IDX
from .traffic import (SCENARIOS, Scenario, Passenger, generate_scenario, generate_day,
                      load_timetable)
from .sim import Sim
from .strategies import make_combos, Combo, ETA, EntranceParking
from .controllers import Static, TimetableSwitch, ThresholdSwitch
from .metrics import summarize, mean_ci, paired_test

HERE = os.path.dirname(__file__)
RES = os.path.join(HERE, "results")
TIMETABLE = os.path.join(HERE, "data", "timetable_synthetic.csv")
os.makedirs(RES, exist_ok=True)


# ---------------------------------------------------------------- 单次运行
def run_scenario(args):
    """args = (scenario 字段字典, 组合键, 种子, 故障电梯)"""
    sc_fields, ck, seed, broken = args
    sc = Scenario(**sc_fields)
    pax, stairs = generate_scenario(sc, seed)
    combos = make_combos()
    sim = Sim(pax, Static(combos[ck]), gen_end=sc.duration, broken_cars=broken).run()
    m = summarize(sim)
    m.update(combo=ck, seed=seed, stairs=stairs)
    return m


def sc_dict(sc, **kw):
    d = dict(sc.__dict__)
    d.update(kw)
    return d


def pool_map(fn, tasks):
    with Pool(max(1, os.cpu_count() - 1)) as pool:
        return pool.map(fn, tasks, chunksize=4)


# ---------------------------------------------------------------- smoke
def smoke():
    combos = make_combos()
    p = Passenger(0, 5.0, IDX["G"], IDX["10"], 1)
    Sim([p], Static(combos["C1"]), gen_end=10).run()
    fl = flight_time(HEIGHT[IDX["10"]] - HEIGHT[IDX["G"]])
    expect_board = 5.0 + C.T_DOOR_OPEN + C.T_PASSENGER        # G 层有空梯：开门 + 1 人上梯
    expect_alight = (expect_board + (C.T_DOOR_OPEN + C.T_PASSENGER + C.T_DOOR_CLOSE - C.T_DOOR_OPEN
                                     - C.T_PASSENGER) + fl + C.T_DOOR_OPEN + C.T_PASSENGER)
    print(f"单个乘客 G→10/F：上梯时刻 {p.t_board:.2f}（手算 {expect_board:.2f}），"
          f"到达时刻 {p.t_alight:.2f}（手算约 {expect_alight:.2f}，误差来自 {C.DT}s 步长）")
    assert abs(p.t_board - expect_board) <= C.DT + 1e-6
    assert abs(p.t_alight - expect_alight) <= 2 * C.DT + 0.5
    print("smoke 通过")


# ---------------------------------------------------------------- E0 校验
def barney_rtt(P, N, df=C.FLOOR_HEIGHT):
    """Barney 上行高峰往返时间公式：RTT = 2H·tv + (S+1)·ts + 2P·tp"""
    H = N - sum((i / N) ** P for i in range(1, N))
    S = N * (1 - (1 - 1 / N) ** P)
    tv = df / C.RATED_SPEED
    ts = flight_time(df) - tv + C.T_DOOR_OPEN + C.T_DOOR_CLOSE
    return 2 * H * tv + (S + 1) * ts + 2 * P * C.T_PASSENGER, H, S


def e0(seeds=10):
    """单入口(G)、纯上行、各层均匀的饱和上行高峰：仿真 RTT 与解析公式比较。"""
    upper = [str(i) for i in range(1, 17)]
    w = {f: (1.0 if f in upper else 0.0) for f in C.FLOORS}
    sc = Scenario("E0 上行高峰校验", lam5=400, split=(1, 0, 0), batch_mean=1.0, duration=1800,
                  profile="uniform", weights=w, entrances={"G": 1.0}, stairs=False)
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
        rtt_f, H, S = barney_rtt(P, len(upper))
        rows.append(dict(seed=seed, rtt_sim=np.mean(rtts), P=P, rtt_formula=rtt_f, H=H, S=S))
    df = pd.DataFrame(rows)
    df["err_pct"] = (df.rtt_sim - df.rtt_formula) / df.rtt_formula * 100
    df.to_csv(os.path.join(RES, "e0_rtt_validation.csv"), index=False)
    print(df.round(2).to_string(index=False))
    print(f"平均偏差 {df.err_pct.mean():.1f}%（目标 |偏差| ≤ 15%）")
    return df


# ---------------------------------------------------------------- E1 分场景比较
def e1(seeds=30):
    keys = ["C0", "C1", "C2", "C3", "C4", "C5", "C7"]
    tasks = [(sc_dict(sc), ck, s, ()) for sk, sc in SCENARIOS.items() for ck in keys for s in range(seeds)]
    out = pool_map(run_scenario, tasks)
    df = pd.DataFrame(out)
    df["scenario"] = [sk for sk in SCENARIOS for _ in keys for _ in range(seeds)]
    df.to_csv(os.path.join(RES, "e1_raw.csv"), index=False)
    rows = []
    for sk in SCENARIOS:
        base = df[(df.scenario == sk) & (df.combo == "C0")].sort_values("seed")
        for ck in keys:
            d = df[(df.scenario == sk) & (df.combo == ck)].sort_values("seed")
            m, h = mean_ci(d.awt)
            diff, p = paired_test(d.awt.values, base.awt.values)
            rows.append(dict(scenario=SCENARIOS[sk].name, combo=ck, awt=m, awt_ci=h, p90=d.p90.mean(),
                             lwt=d.lwt.mean(), att=d.att.mean(), car_hours=d.car_hours.mean(),
                             distance_km=d.distance_km.mean(), vs_C0=diff, p_vs_C0=p, n_pax=d.n.mean()))
    summ = pd.DataFrame(rows)
    summ.to_csv(os.path.join(RES, "e1_summary.csv"), index=False)
    print(summ.round(2).to_string(index=False))
    return summ


# ---------------------------------------------------------------- E2 参数扫描与阈值
BASE = Scenario("扫描基准", lam5=100, split=(0.35, 0.35, 0.30), batch_mean=1.5, duration=900,
                profile="uniform", weights=C.FLOOR_WEIGHTS_CLASS)


def _sweep(name, param_values, make_sc, keys, seeds):
    tasks, meta = [], []
    for v in param_values:
        sc = make_sc(v)
        for ck in keys:
            for s in range(seeds):
                tasks.append((sc_dict(sc), ck, s, ()))
                meta.append((name, v))
    out = pool_map(run_scenario, tasks)
    df = pd.DataFrame(out)
    df["sweep"] = [m[0] for m in meta]
    df["value"] = [m[1] for m in meta]
    return df


HIGH_FLOOR = IDX["5"]       # "高层"= 5/F 及以上


def mix_weights(alpha):
    """α=0：KKL 课室集中在低层的权重；α=1：各层均匀的办公楼权重。"""
    uni = {f: (1.0 if (f not in C.ENTRANCES and f != "LG3") else 0.0) for f in C.FLOORS}
    tot_c = sum(C.FLOOR_WEIGHTS_CLASS.values())
    tot_u = sum(uni.values())
    return {f: (1 - alpha) * C.FLOOR_WEIGHTS_CLASS.get(f, 0) / tot_c + alpha * uni[f] / tot_u
            for f in C.FLOORS}


def high_share(weights):
    """上行乘客中去 5/F 及以上的比例（由楼层权重算出）。"""
    w = {IDX[f]: v for f, v in weights.items() if IDX[f] not in ENTRANCE_IDX}
    tot = sum(w.values())
    return sum(v for f, v in w.items() if f >= HIGH_FLOOR) / tot


def split_from(s_in, inter):
    return (s_in * (1 - inter), (1 - s_in) * (1 - inter), inter)


def e2(seeds=20):
    frames = [
        _sweep("lam5", [20, 40, 60, 80, 100, 130, 160, 200],
               lambda v: replace(BASE, lam5=v), ["C0", "C1", "C2", "C3", "C4"], seeds),
        _sweep("s_in", [0.05, 0.2, 0.35, 0.5, 0.65, 0.8, 0.95],
               lambda v: replace(BASE, lam5=130, split=split_from(v, 0.15)), ["C1", "C2", "C3", "C4"], seeds),
        _sweep("inter", [0.0, 0.1, 0.2, 0.3, 0.45, 0.6],
               lambda v: replace(BASE, lam5=130, split=split_from(0.5, v)), ["C1", "C2", "C3", "C4"], seeds),
        _sweep("batch", [1.0, 1.5, 2.0, 2.5, 3.0],
               lambda v: replace(BASE, lam5=130, batch_mean=v, split=(0.4, 0.4, 0.2)), ["C1", "C4"], seeds),
        _sweep("lam5_up_heavy", [100, 150, 200, 250, 300, 350, 400],
               lambda v: replace(BASE, lam5=v, split=(0.92, 0.03, 0.05), batch_mean=1.3),
               ["C1", "C2", "C7", "C4"], seeds),
        _sweep("lam5_down_heavy", [100, 150, 200, 250, 300, 350, 400],
               lambda v: replace(BASE, lam5=v, split=(0.03, 0.92, 0.05), batch_mean=1.6),
               ["C1", "C3", "C4"], seeds),
        _sweep("lam5_low", [1, 2, 4, 6, 10, 15, 25],
               lambda v: replace(BASE, lam5=v, split=(0.45, 0.45, 0.10), duration=1800, batch_mean=1.1),
               ["C1", "C5"], seeds),
    ]
    raw = pd.concat(frames, ignore_index=True)
    raw.to_csv(os.path.join(RES, "e2_raw.csv"), index=False)

    # 二维热力图：到达率 × 进楼比例，每格哪个组合最好
    hm_tasks, hm_meta = [], []
    for lam in [40, 70, 100, 130, 160, 190]:
        for s_in in [0.1, 0.3, 0.5, 0.7, 0.9]:
            sc = replace(BASE, lam5=lam, split=split_from(s_in, 0.15))
            for ck in ["C1", "C2", "C3", "C4", "C7"]:
                for s in range(max(8, seeds // 2)):
                    hm_tasks.append((sc_dict(sc), ck, s, ()))
                    hm_meta.append((lam, s_in))
    hm = pd.DataFrame(pool_map(run_scenario, hm_tasks))
    hm["lam5"] = [m[0] for m in hm_meta]
    hm["s_in"] = [m[1] for m in hm_meta]
    hm.to_csv(os.path.join(RES, "e2_heatmap_raw.csv"), index=False)

    # 楼层分布 × 到达率：重上行高峰时，需求从"集中在低层课室"(α=0) 逐步变成"各层均匀"(α=1)
    fm_tasks, fm_meta = [], []
    for alpha in [0.0, 0.25, 0.5, 0.75, 1.0]:
        w = mix_weights(alpha)
        for lam in [150, 200, 250, 300]:
            sc = replace(BASE, lam5=lam, split=(0.92, 0.03, 0.05), batch_mean=1.3, weights=w)
            for ck in ["C1", "C2"]:
                for s in range(max(8, seeds // 2)):
                    fm_tasks.append((sc_dict(sc), ck, s, ()))
                    fm_meta.append((alpha, high_share(w), lam))
    fm = pd.DataFrame(pool_map(run_scenario, fm_tasks))
    fm["alpha"] = [m[0] for m in fm_meta]
    fm["high_share"] = [m[1] for m in fm_meta]
    fm["lam5"] = [m[2] for m in fm_meta]
    fm.to_csv(os.path.join(RES, "e2_floormix_raw.csv"), index=False)

    th = thresholds(raw, hm, fm)
    print(th["table"].to_string(index=False))
    print("切换规则：", json.dumps(th["rules"], ensure_ascii=False))
    return raw, hm, th


def thresholds(raw, hm, fm=None, ref="C1", alpha=0.05):
    """每个扫描里，找出"某组合相对 ETA(C1) 显著更好/更差"的参数区间。"""
    rows = []
    for sweep, g in raw.groupby("sweep"):
        for ck in sorted(set(g.combo) - {ref}):
            for v, gv in g.groupby("value"):
                a = gv[gv.combo == ck].sort_values("seed").awt.values
                b = gv[gv.combo == ref].sort_values("seed").awt.values
                diff, p = paired_test(a, b)
                verdict = "显著更好" if (diff < 0 and p < alpha) else ("显著更差" if (diff > 0 and p < alpha)
                                                                     else "无显著差异")
                rows.append(dict(sweep=sweep, value=v, combo=ck, awt_diff_vs_C1=diff, p=p, verdict=verdict))
    table = pd.DataFrame(rows)
    table.to_csv(os.path.join(RES, "e2_thresholds.csv"), index=False)

    # 从热力图推导实时切换规则
    rules = {"eco_lam5": None, "up": None, "up_park": None, "down": None, "mixed": ref}
    cells = []
    for (lam, s_in), g in hm.groupby(["lam5", "s_in"]):
        b = g[g.combo == ref].sort_values("seed").awt.values
        for ck in ["C2", "C3", "C4", "C7"]:
            a = g[g.combo == ck].sort_values("seed").awt.values
            diff, p = paired_test(a, b)
            cells.append(dict(lam5=lam, s_in=s_in, combo=ck, diff=diff, p=p, better=diff < 0 and p < alpha))
    cells = pd.DataFrame(cells)
    up = cells[(cells.combo == "C2") & cells.better]
    if len(up):
        rules["up"] = [dict(s_in_min=float(up.s_in.min()), lam5_min=float(up.lam5.min()), high_min=0.0)]
    # 入口层停靠（C7）：找出"进楼比例达到多少后，在所有到达率下都不比 C1 差、且至少一半格子显著更好"
    pk = cells[cells.combo == "C7"]
    for s_in in sorted(pk.s_in.unique()):
        sub = pk[pk.s_in >= s_in]
        worse = ((sub["diff"] > 0) & (sub.p < alpha)).any()
        if not worse and sub.better.mean() >= 0.5:
            rules["up_park"] = dict(s_in_min=float(s_in))
            break
    if fm is not None:
        fcells = []
        for (a, lam), g in fm.groupby(["alpha", "lam5"]):
            b = g[g.combo == ref].sort_values("seed").awt.values
            x = g[g.combo == "C2"].sort_values("seed").awt.values
            diff, p = paired_test(x, b)
            fcells.append(dict(alpha=a, high_share=g.high_share.iloc[0], lam5=lam, diff=diff, p=p,
                               rel=diff / b.mean() * 100, better=diff < 0 and p < alpha))
        fcells = pd.DataFrame(fcells)
        fcells.to_csv(os.path.join(RES, "e2_floormix_cells.csv"), index=False)
        # 每个楼层分布水平：从哪个到达率开始（且之后都）显著更好 → 一条边界
        bound = []
        for a, g in fcells.groupby("alpha"):
            g = g.sort_values("lam5")
            for lam in g.lam5:
                if g[g.lam5 >= lam].better.all():
                    bound.append(dict(s_in_min=0.8, lam5_min=float(lam), high_min=float(g.high_share.iloc[0])))
                    break
        if bound and rules["up"] is None:
            rules["up"] = bound
    down = cells[(cells.combo == "C3") & cells.better]
    if len(down):
        rules["down"] = dict(s_out_min=float(1 - down.s_in.max()) * 0.85, lam5_min=float(down.lam5.min()))
    mid = cells[(cells.s_in == 0.5) & (cells.combo == "C4")]
    if len(mid) and (mid.better.sum() > len(mid) / 2):
        rules["mixed"] = "C4"
    # 低客流节能：C5 平均等待不超过 30 秒、LWT 不超过 5% 的最大到达率
    low = raw[(raw.sweep == "lam5_low") & (raw.combo == "C5")].groupby("value")[["awt", "lwt"]].mean()
    ok = low[(low.awt <= 30) & (low.lwt <= 5)]
    if len(ok):
        rules["eco_lam5"] = float(ok.index.max())
    with open(os.path.join(RES, "e2_rules.json"), "w", encoding="utf-8") as f:
        json.dump(rules, f, ensure_ascii=False, indent=2)
    cells.to_csv(os.path.join(RES, "e2_heatmap_cells.csv"), index=False)
    return dict(table=table, rules=rules, cells=cells)


def rules_to_controller(rules, combos):
    r = []
    if rules.get("eco_lam5"):
        e = rules["eco_lam5"]
        r.append((lambda lam, si, so, it, sh, e=e: lam <= e, "C5"))
    if rules.get("up"):
        bound = rules["up"]
        r.append((lambda lam, si, so, it, sh, b=bound: any(
            si >= u["s_in_min"] and lam >= u["lam5_min"] and sh >= u["high_min"] for u in b), "C2"))
    if rules.get("up_park"):
        p = rules["up_park"]
        r.append((lambda lam, si, so, it, sh, p=p: si >= p["s_in_min"], "C7"))
    if rules.get("down"):
        d = rules["down"]
        r.append((lambda lam, si, so, it, sh, d=d: so >= d["s_out_min"] and lam >= d["lam5_min"], "C3"))
    return ThresholdSwitch(combos, r, default=rules.get("mixed", "C1"))


# ---------------------------------------------------------------- E3 全天
def best_by_scenario():
    path = os.path.join(RES, "e1_summary.csv")
    default = {"in": "C2", "out": "C1", "mix": "C1", "lunch": "C1", "normal": "C1"}
    if not os.path.exists(path):
        return default
    s = pd.read_csv(path)
    s = s[s.combo != "C5"]
    pick = lambda name: s[s.scenario == name].sort_values("awt").combo.iloc[0]
    return {"in": pick(SCENARIOS["S1"].name), "out": pick(SCENARIOS["S2"].name),
            "mix": pick(SCENARIOS["S3"].name), "lunch": pick(SCENARIOS["S4"].name),
            "normal": pick(SCENARIOS["S5"].name)}


def run_day(args):
    ctrl_name, seed, attendance = args
    tt = load_timetable(TIMETABLE)
    forecast = [(c["end"], c["floor"], c["size"] * 0.8) for c in tt]      # 预测永远用名义出席率 0.8
    combos = make_combos(forecast)
    if ctrl_name.startswith("静态"):
        ctrl = Static(combos[ctrl_name.split()[1]])
    elif ctrl_name == "课表预切换":
        mm = best_by_scenario()
        if mm["out"] in ("C1", "C4"):
            mm["out"] = "C6"                     # 下课：在最优调度器上加课表预测停靠
        ctrl = TimetableSwitch(combos, tt, mm)
    else:
        with open(os.path.join(RES, "e2_rules.json"), encoding="utf-8") as f:
            rules = json.load(f)
        rules["eco_lam5"] = None                 # 上课日不进入节能模式
        ctrl = rules_to_controller(rules, combos)
    pax = generate_day(tt, seed, attendance=attendance)
    day_len = 11 * 3600
    sim = Sim(pax, ctrl, gen_end=day_len).run()
    m = summarize(sim)
    m.update(controller=ctrl_name, seed=seed, attendance=attendance)
    # 上下课窗口内（开课前 10 分钟到开课后 2 分钟、下课后 6 分钟内）到达的乘客
    wins = [(c["start"] - 600, c["start"] + 120) for c in tt] + [(c["end"], c["end"] + 360) for c in tt]
    pk = summarize(sim, keep=lambda p: any(a <= p.t <= b for a, b in wins))
    for k in ("n", "awt", "p90", "lwt"):
        m[f"peak_{k}"] = pk[k]
    # 分时段（每 30 分钟）的平均等待，用于画全天曲线
    for k in range(22):
        mk = summarize(sim, k * 1800, (k + 1) * 1800)
        m[f"awt_{k:02d}"] = mk["awt"]
    m["switches"] = len(sim.combo_log)
    return m


def e3(seeds=10):
    ctrls = ["静态 C0", "静态 C1", "静态 C4", "课表预切换", "阈值实时切换"]
    tasks = [(c, s, a) for a in (0.64, 0.8, 0.96) for c in ctrls for s in range(seeds)]
    df = pd.DataFrame(pool_map(run_day, tasks))
    df.to_csv(os.path.join(RES, "e3_raw.csv"), index=False)
    rows = []
    for (a, c), g in df.groupby(["attendance", "controller"]):
        base = df[(df.attendance == a) & (df.controller == "静态 C0")].sort_values("seed").awt.values
        m, h = mean_ci(g.awt)
        diff, p = paired_test(g.sort_values("seed").awt.values, base)
        pm, ph = mean_ci(g.peak_awt)
        bpk = df[(df.attendance == a) & (df.controller == "静态 C1")].sort_values("seed").peak_awt.values
        pdiff, pp = paired_test(g.sort_values("seed").peak_awt.values, bpk)
        rows.append(dict(attendance=a, controller=c, awt=m, awt_ci=h, p90=g.p90.mean(), lwt=g.lwt.mean(),
                         peak_awt=pm, peak_awt_ci=ph, peak_p90=g.peak_p90.mean(), peak_lwt=g.peak_lwt.mean(),
                         distance_km=g.distance_km.mean(), vs_C0=diff, p_vs_C0=p,
                         peak_vs_C1=pdiff, p_peak_vs_C1=pp, switches=g.switches.mean()))
    summ = pd.DataFrame(rows)
    summ.to_csv(os.path.join(RES, "e3_summary.csv"), index=False)
    print(summ.round(2).to_string(index=False))

    # 节假日：全天客流很低，静态 C1 vs 校历切到节能模式 C5
    hol = replace(SCENARIOS["S6"], duration=11 * 3600)
    tasks = [(sc_dict(hol), ck, s, ()) for ck in ("C1", "C5") for s in range(seeds)]
    h = pd.DataFrame(pool_map(run_scenario, tasks))
    hs = h.groupby("combo")[["awt", "p90", "lwt", "car_hours", "distance_km", "starts"]].mean()
    hs.to_csv(os.path.join(RES, "e3_holiday.csv"))
    print("节假日：\n", hs.round(2).to_string())
    return summ


# ---------------------------------------------------------------- E3b 人流放大
def e3b(seeds=8):
    """把课表人数放大（出席率 × 2、× 3，相当于名义出席率 1.6、2.4），
    看人流变大后"切换"是否开始比最好的单一组合有收益。例如改造后的研发中心、或大课集中排在 KKL。"""
    ctrls = ["静态 C1", "课表预切换", "阈值实时切换"]
    tasks = [(c, s, a) for a in (1.6, 2.4) for c in ctrls for s in range(seeds)]
    df = pd.DataFrame(pool_map(run_day, tasks))
    df.to_csv(os.path.join(RES, "e3b_scaled_raw.csv"), index=False)
    rows = []
    for (a, c), g in df.groupby(["attendance", "controller"]):
        g = g.sort_values("seed")
        b = df[(df.attendance == a) & (df.controller == "静态 C1")].sort_values("seed")
        diff, p = paired_test(g.peak_awt.values, b.peak_awt.values)
        rows.append(dict(attendance=a, controller=c, awt=g.awt.mean(), peak_awt=g.peak_awt.mean(),
                         peak_p90=g.peak_p90.mean(), peak_lwt=g.peak_lwt.mean(),
                         peak_vs_C1=diff, p_peak_vs_C1=p, switches=g.switches.mean()))
    summ = pd.DataFrame(rows)
    summ.to_csv(os.path.join(RES, "e3b_scaled_summary.csv"), index=False)
    print(summ.round(3).to_string(index=False))
    return summ


# ---------------------------------------------------------------- E4 稳健性
def e4(seeds=30):
    keys = ["C0", "C1", "C2", "C4"]
    tasks, meta = [], []
    for sk in ["S1", "S3"]:
        for broken in [(), (0,)]:
            for ck in keys:
                for s in range(seeds):
                    tasks.append((sc_dict(SCENARIOS[sk]), ck, s, broken))
                    meta.append((SCENARIOS[sk].name, "1 台停运" if broken else "全部正常"))
    df = pd.DataFrame(pool_map(run_scenario, tasks))
    df["scenario"] = [m[0] for m in meta]
    df["condition"] = [m[1] for m in meta]
    summ = df.groupby(["scenario", "condition", "combo"])[["awt", "p90", "lwt"]].mean().reset_index()
    summ.to_csv(os.path.join(RES, "e4_robustness.csv"), index=False)
    print(summ.round(2).to_string(index=False))
    return summ


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("exp", choices=["smoke", "e0", "e1", "e2", "e3", "e3b", "e4", "all"])
    ap.add_argument("--seeds", type=int, default=None)
    a = ap.parse_args()
    t0 = time.time()
    kw = {} if a.seeds is None else {"seeds": a.seeds}
    if a.exp in ("smoke", "all"):
        smoke()
    for name, fn in [("e0", e0), ("e1", e1), ("e2", e2), ("e3", e3), ("e3b", e3b), ("e4", e4)]:
        if a.exp in (name, "all"):
            print(f"\n===== {name} =====")
            fn(**kw)
    print(f"\n用时 {time.time() - t0:.0f} 秒")
