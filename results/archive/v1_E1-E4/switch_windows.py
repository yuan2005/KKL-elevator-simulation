"""只读分析：按"切换实际生效的时段"对比切换 vs 静态 C1（不修改项目代码）。"""
import sys, os, json
sys.path.insert(0, "/Users/Wu/HKUstudyMaterial/COMP1110/project")
from multiprocessing import Pool
import numpy as np, pandas as pd
from scipy import stats
from simulation.experiments import TIMETABLE, RES, best_by_scenario, rules_to_controller
from simulation.traffic import generate_day, load_timetable
from simulation.strategies import make_combos
from simulation.controllers import Static, TimetableSwitch
from simulation.sim import Sim

DAY = 11 * 3600

def build(ctrl_name, combos, tt):
    if ctrl_name == "静态 C1":
        return Static(combos["C1"])
    if ctrl_name == "课表预切换":
        mm = best_by_scenario()
        if mm["out"] in ("C1", "C4"):
            mm["out"] = "C6"
        return TimetableSwitch(combos, tt, mm)
    rules = json.load(open(os.path.join(RES, "e2_rules.json"), encoding="utf-8"))
    rules["eco_lam5"] = None
    return rules_to_controller(rules, combos)

def one(args):
    ctrl_name, seed, att = args
    tt = load_timetable(TIMETABLE)
    combos = make_combos([(c["end"], c["floor"], c["size"] * 0.8) for c in tt])
    pax = generate_day(tt, seed, attendance=att)
    sim = Sim(pax, build(ctrl_name, combos, tt), gen_end=DAY).run()
    log = sim.combo_log + [(DAY, None)]
    intervals = [(t0, t1, name.split()[0]) for (t0, name), (t1, _) in zip(log, log[1:]) if t1 > t0]
    waits = {p.id: ((p.t_board if p.t_board is not None else sim.t) - p.t) for p in pax}
    times = {p.id: p.t for p in pax}
    return ctrl_name, seed, att, intervals, waits, times

if __name__ == "__main__":
    seeds = range(10)
    tasks = [(c, s, a) for a in (0.64, 0.8, 0.96) for c in ["静态 C1", "课表预切换", "阈值实时切换"] for s in seeds]
    with Pool(9) as pool:
        out = pool.map(one, tasks)
    base = {(s, a): w for c, s, a, iv, w, t in out if c == "静态 C1"}
    rows = []
    for c, s, a, iv, w, t in out:
        if c == "静态 C1":
            continue
        b = base[(s, a)]
        for t0, t1, combo in iv:
            if combo == "C1":
                continue
            ids = [i for i, ti in t.items() if t0 <= ti < t1]
            for i in ids:
                rows.append(dict(controller=c, attendance=a, seed=s, combo=combo, sw=w[i], c1=b[i]))
    df = pd.DataFrame(rows)
    df.to_csv(os.path.join(os.path.dirname(__file__), "switch_windows_pax.csv"), index=False)
    # 每个种子先求平均，再做配对 t 检验（种子是独立重复单位）
    g = df.groupby(["controller", "attendance", "combo", "seed"]).agg(n=("sw", "size"), sw=("sw", "mean"),
                                                                    c1=("c1", "mean"), lw_sw=("sw", lambda x: (x > 60).mean() * 100),
                                                                    lw_c1=("c1", lambda x: (x > 60).mean() * 100)).reset_index()
    res = []
    for k, gg in g.groupby(["controller", "attendance", "combo"]):
        p = stats.ttest_rel(gg.sw, gg.c1).pvalue if len(gg) > 1 else float("nan")
        res.append(dict(控制方式=k[0], 出席率=k[1], 切到的组合=k[2], 每天受影响乘客=gg.n.mean(),
                        切换后AWT=gg.sw.mean(), 同批乘客静态C1的AWT=gg.c1.mean(),
                        差值=gg.sw.mean() - gg.c1.mean(), 相对变化pct=(gg.sw.mean() / gg.c1.mean() - 1) * 100,
                        p值=p, 有效种子数=len(gg)))
    r = pd.DataFrame(res)
    r.to_csv(os.path.join(os.path.dirname(__file__), "switch_windows_summary.csv"), index=False)
    pd.set_option("display.width", 200)
    print(r.round(3).to_string(index=False))
