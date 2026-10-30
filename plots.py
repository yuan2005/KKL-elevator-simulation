"""把 results/*.csv 画成报告用的图（在 simulation 文件夹里运行）：python3 plots.py"""
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

HERE = os.path.dirname(__file__)
RES = os.path.join(HERE, "results")
FIG = os.path.join(RES, "figures")
os.makedirs(FIG, exist_ok=True)
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti TC", "Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

NAMES = {"C0": "C0 现状基线", "C1": "C1 ETA+分散停靠", "C2": "C2 上行分区", "C3": "C3 下行分区",
         "C4": "C4 RSR", "C6": "C6 课表预测停靠", "C7": "C7 ETA+入口停靠"}
COLORS = {"C0": "#7f7f7f", "C1": "#1f77b4", "C2": "#d62728", "C3": "#9467bd", "C4": "#2ca02c",
          "C5": "#8c564b", "C6": "#17becf", "C7": "#ff7f0e"}


def _scatter_by_label(ax, df, x, y):
    for k, g in df.groupby("label"):
        ax.scatter(g[x], g[y], s=20, c=COLORS[k], label=NAMES[k], alpha=0.85)


def _draw_c7_box(ax):
    """从 e1_tree.json 读出 C7 区域的边界画虚线框（仅当规则是"一个比例阈值 + 人数区间"时）。"""
    import json
    try:
        d = json.load(open(os.path.join(RES, "e1_tree.json"), encoding="utf-8"))
    except FileNotFoundError:
        return
    nodes, feats = d["nodes"], d["features"]

    def walk(i, box):
        nd = nodes[i]
        if nd["f"] < 0:
            if nd["label"] == "C7":
                yield box
            return
        f = feats[nd["f"]]
        lo, hi = box.get(f, (-np.inf, np.inf))
        yield from walk(nd["l"], {**box, f: (lo, min(hi, nd["thr"]))})
        yield from walk(nd["r"], {**box, f: (max(lo, nd["thr"]), hi)})

    for box in walk(0, {}):
        x0, x1 = box.get("lam5", (1, 400))
        y0, y1 = box.get("s_from_ent", (0, 1))
        x0, x1 = max(x0, 1), min(x1, 400)
        y1 = min(y1, 1.0)
        ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ls="--", ec="#ff7f0e", lw=1.5))


def fig_e1():
    pts = pd.read_csv(os.path.join(RES, "e1_points.csv"))
    tr = pts[pts.point.str.startswith("P")]
    ref = pts[~pts.point.str.startswith("P")]
    fig, axs = plt.subplots(2, 2, figsize=(13, 9.5))

    ax = axs[0, 0]
    _scatter_by_label(ax, tr, "lam5", "s_from_ent")
    for _, r in ref.iterrows():
        ax.annotate(r.point, (r.lam5, r.s_from_ent), fontsize=9, weight="bold")
    _draw_c7_box(ax)
    ax.set_xscale("log"); ax.set_xlabel("每 5 分钟乘梯人数（对数坐标）")
    ax.set_ylabel("从入口层出发的比例（进楼 + 过路）")
    ax.set_title("每个抽样点等待最短的组合\n虚线框 = 地图规则里用 C7 的区域")
    ax.legend(fontsize=7)

    ax = axs[0, 1]
    _scatter_by_label(ax, tr, "lam5", "s_thru")
    ax.set_xscale("log"); ax.set_xlabel("每 5 分钟乘梯人数（对数坐标）"); ax.set_ylabel("过路比例（LG2↔G）")
    ax.set_title("人数 × 过路比例")

    ax = axs[1, 0]
    _scatter_by_label(ax, tr[tr.s_in >= 0.4], "lam5", "s_high")
    ax.set_xlabel("每 5 分钟乘梯人数"); ax.set_ylabel("进楼乘客去 5/F 以上的比例")
    ax.set_title("进楼比例 ≥ 0.4 的点：人数 × 去高层比例")

    ax = axs[1, 1]
    best = np.array([r[f"awt_{r.label}"] for _, r in tr.iterrows()])
    ax.scatter(tr.lam5, -tr.C1_vs_C0_pct, s=12, c="#1f77b4", label="一直用 C1")
    ax.scatter(tr.lam5, (1 - best / tr.awt_C0) * 100, s=9, c="#ff7f0e", alpha=0.7, label="每点最优组合")
    ax.axhline(0, color="k", lw=0.6)
    ax.set_xscale("log"); ax.set_xlabel("每 5 分钟乘梯人数（对数坐标）")
    ax.set_ylabel("平均等待比现状 C0 减少 (%)")
    ax.set_title("相对现状调度的改善"); ax.legend(fontsize=8)
    fig.suptitle("E1 策略地图（颜色 = 该人流条件下平均等待最短、且显著好过 C1 的组合）")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e1_strategy_map.png"), dpi=160)

    cv = pd.read_csv(os.path.join(RES, "e1_tree_cv.csv"))
    fig, ax = plt.subplots(figsize=(5.5, 3.6))
    ax.plot(cv.depth, cv.cv_regret_s, "o-")
    ax.set_xlabel("决策树深度"); ax.set_ylabel("交叉验证平均后悔 (秒)")
    ax.set_title("策略地图：深度与后悔\n（后悔 = 相对每点最优组合多等的秒数）", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e1_tree_depth.png"), dpi=160)


def fig_e2():
    s = pd.read_csv(os.path.join(RES, "e2_summary.csv"))
    order = [o for o in ["静态 C0", "静态 C1", "按时段名称切换", "地图实时", "课表预测+地图"] if o in set(s.controller)]
    conds = list(dict.fromkeys(s["cond"]))
    fig, axs = plt.subplots(1, 2, figsize=(16, 4.8))
    w = 0.8 / len(order)
    for col, ax, ylabel in [("awt", axs[0], "全天平均等待 (秒)"), ("peak_awt", axs[1], "上下课窗口平均等待 (秒)")]:
        for i, c in enumerate(order):
            d = s[s.controller == c].set_index("cond").loc[conds]
            ax.bar(np.arange(len(conds)) + i * w, d[col], w, label=c)
        ax.set_xticks(np.arange(len(conds)) + w * (len(order) - 1) / 2)
        ax.set_xticklabels(conds, fontsize=8, rotation=15)
        ax.set_ylabel(ylabel)
    axs[0].legend(fontsize=7)
    fig.suptitle("E2 全天验证（合成课表，每种情况 10 天；课表预测始终按出席 0.8、过路 30%）")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e2_full_day.png"), dpi=160)

    sw = pd.read_csv(os.path.join(RES, "e2_switch_periods.csv"))
    if len(sw):
        sw["label"] = sw.controller + " → " + sw.combo
        piv = sw.pivot_table(index="label", columns="cond", values="change_pct", sort=False)[conds]
        sig = sw.pivot_table(index="label", columns="cond", values="p", sort=False)[conds]
        fig, ax = plt.subplots(figsize=(13, 0.55 * len(piv) + 2))
        im = ax.imshow(piv.values, cmap="RdBu_r", vmin=-30, vmax=30, aspect="auto")
        for i in range(piv.shape[0]):
            for j in range(piv.shape[1]):
                v = piv.values[i, j]
                if not np.isnan(v):
                    ax.text(j, i, f"{v:+.1f}%" + ("*" if sig.values[i, j] < 0.05 else ""), ha="center",
                            va="center", fontsize=9)
        ax.set_xticks(range(piv.shape[1])); ax.set_xticklabels(piv.columns, fontsize=8, rotation=15)
        ax.set_yticks(range(piv.shape[0])); ax.set_yticklabels(piv.index, fontsize=9)
        ax.set_title("只看切换生效时段：等待相对静态 C1 的变化（蓝 = 更好，* p<0.05）")
        fig.colorbar(im, ax=ax)
        fig.tight_layout()
        fig.savefig(os.path.join(FIG, "e2_switch_periods.png"), dpi=160)


def fig_e3():
    s = pd.read_csv(os.path.join(RES, "e3_one_car_out.csv"))
    fig, ax = plt.subplots(figsize=(7, 4))
    conds = list(dict.fromkeys(s["cond"]))
    ctrls = list(dict.fromkeys(s.controller))
    w = 0.8 / len(ctrls)
    for i, c in enumerate(ctrls):
        d = s[s.controller == c].set_index("cond").loc[conds]
        ax.bar(np.arange(len(conds)) + i * w, d.peak_awt, w, label=c)
    ax.set_xticks(np.arange(len(conds)) + w * (len(ctrls) - 1) / 2)
    ax.set_xticklabels(conds)
    ax.set_ylabel("上下课窗口平均等待 (秒)")
    ax.set_title("E3 一台电梯停运")
    ax.legend(fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e3_one_car_out.png"), dpi=160)


if __name__ == "__main__":
    for fn in (fig_e1, fig_e2, fig_e3):
        try:
            fn()
            print("已生成", fn.__name__)
        except FileNotFoundError as e:
            print("跳过", fn.__name__, "（缺少结果文件：", e.filename, "）")
