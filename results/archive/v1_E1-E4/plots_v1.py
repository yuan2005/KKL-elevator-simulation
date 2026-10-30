"""把 results/*.csv 画成报告用的图：python -m simulation.plots"""
import json
import os

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from .metrics import mean_ci

HERE = os.path.dirname(__file__)
RES = os.path.join(HERE, "results")
FIG = os.path.join(RES, "figures")
os.makedirs(FIG, exist_ok=True)
plt.rcParams["font.sans-serif"] = ["PingFang SC", "Heiti TC", "Arial Unicode MS", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

NAMES = {"C0": "C0 现状基线", "C1": "C1 ETA+分散停靠", "C2": "C2 上行分区", "C3": "C3 下行分区",
         "C4": "C4 RSR", "C5": "C5 节能2台", "C7": "C7 ETA+入口停靠"}
COLORS = {"C0": "#7f7f7f", "C1": "#1f77b4", "C2": "#d62728", "C3": "#9467bd", "C4": "#2ca02c",
          "C5": "#8c564b", "C7": "#ff7f0e"}


def fig_e1():
    s = pd.read_csv(os.path.join(RES, "e1_summary.csv"))
    scen = list(dict.fromkeys(s.scenario))
    combos = [c for c in NAMES if c in set(s.combo) and c != "C5"]
    fig, ax = plt.subplots(figsize=(11, 4.5))
    w = 0.8 / len(combos)
    for i, c in enumerate(combos):
        d = s[s.combo == c].set_index("scenario").loc[scen]
        ax.bar(np.arange(len(scen)) + i * w, d.awt, w, yerr=d.awt_ci, capsize=2, label=NAMES[c],
               color=COLORS[c])
    ax.set_xticks(np.arange(len(scen)) + w * (len(combos) - 1) / 2)
    ax.set_xticklabels(scen)
    ax.set_ylabel("平均等待时间 AWT (秒)")
    ax.set_title("E1 各场景下不同策略组合的平均等待时间（30 次重复，误差线为 95% 置信区间）")
    ax.legend(ncol=3, fontsize=8)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e1_awt_by_scenario.png"), dpi=160)


def _line(ax, raw, sweep, xlabel, title):
    g = raw[raw.sweep == sweep]
    for c in [c for c in NAMES if c in set(g.combo)]:
        gc = g[g.combo == c]
        xs = sorted(gc.value.unique())
        ms = [mean_ci(gc[gc.value == x].awt) for x in xs]
        m = np.array([a for a, _ in ms]); h = np.array([b for _, b in ms])
        ax.plot(xs, m, "o-", ms=3, label=NAMES[c], color=COLORS[c])
        ax.fill_between(xs, m - h, m + h, alpha=0.15, color=COLORS[c])
    ax.set_xlabel(xlabel)
    ax.set_ylabel("AWT (秒)")
    ax.set_title(title, fontsize=10)
    ax.legend(fontsize=7)


def fig_e2():
    raw = pd.read_csv(os.path.join(RES, "e2_raw.csv"))
    fig, axs = plt.subplots(2, 3, figsize=(15, 8))
    _line(axs[0, 0], raw, "lam5", "每 5 分钟到达人数", "到达率（混合客流）")
    _line(axs[0, 1], raw, "s_in", "进楼比例", "上/下行比例（130 人/5 分钟）")
    _line(axs[0, 2], raw, "inter", "楼层间比例", "楼层间移动比例")
    _line(axs[1, 0], raw, "lam5_up_heavy", "每 5 分钟到达人数", "重上行高峰（KKL 楼层分布）")
    _line(axs[1, 1], raw, "lam5_down_heavy", "每 5 分钟到达人数", "重下行高峰")
    _line(axs[1, 2], raw, "lam5_low", "每 5 分钟到达人数", "低客流：节能模式的代价")
    axs[1, 1].set_yscale("log"); axs[1, 0].set_yscale("log")
    fig.suptitle("E2 参数扫描：阴影为 95% 置信区间")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e2_sweeps.png"), dpi=160)

    # 楼层分布 × 到达率：上行分区 C2 相对 ETA C1 的变化
    f = pd.read_csv(os.path.join(RES, "e2_floormix_cells.csv"))
    pv = f.pivot_table(index="high_share", columns="lam5", values="rel")
    sig = f.pivot_table(index="high_share", columns="lam5", values="better")
    fig, ax = plt.subplots(figsize=(7, 4.5))
    im = ax.imshow(pv.values, cmap="RdBu_r", vmin=-50, vmax=50, origin="lower", aspect="auto")
    for i in range(pv.shape[0]):
        for j in range(pv.shape[1]):
            ax.text(j, i, f"{pv.values[i, j]:+.0f}%" + ("*" if sig.values[i, j] else ""), ha="center",
                    va="center", fontsize=9)
    ax.set_xticks(range(pv.shape[1])); ax.set_xticklabels([int(x) for x in pv.columns])
    ax.set_yticks(range(pv.shape[0])); ax.set_yticklabels([f"{y:.0%}" for y in pv.index])
    ax.set_xlabel("每 5 分钟到达人数（重上行高峰）")
    ax.set_ylabel("进楼乘客去 5/F 及以上的比例")
    ax.set_title("上行分区(C2) 相对 ETA(C1) 的等待时间变化\n蓝色=更好，*=显著（p<0.05）")
    fig.colorbar(im, ax=ax, label="AWT 变化 (%)")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e2_floormix_threshold.png"), dpi=160)

    # 到达率 × 进楼比例：每格最优组合
    c = pd.read_csv(os.path.join(RES, "e2_heatmap_raw.csv"))
    best = c.groupby(["lam5", "s_in", "combo"]).awt.mean().reset_index()
    best = best.loc[best.groupby(["lam5", "s_in"]).awt.idxmin()]
    pv = best.pivot(index="lam5", columns="s_in", values="combo")
    keys = sorted(set(best.combo))
    cmap = matplotlib.colors.ListedColormap([COLORS[k] for k in keys])
    idx = pv.apply(lambda col: col.map({k: i for i, k in enumerate(keys)}))
    fig, ax = plt.subplots(figsize=(6.5, 4.5))
    ax.imshow(idx.values, cmap=cmap, vmin=-0.5, vmax=len(keys) - 0.5, origin="lower", aspect="auto")
    for i in range(pv.shape[0]):
        for j in range(pv.shape[1]):
            ax.text(j, i, pv.values[i, j], ha="center", va="center", color="white", fontsize=10)
    ax.set_xticks(range(pv.shape[1])); ax.set_xticklabels(pv.columns)
    ax.set_yticks(range(pv.shape[0])); ax.set_yticklabels(pv.index)
    ax.set_xlabel("进楼比例"); ax.set_ylabel("每 5 分钟到达人数")
    ax.set_title("每种客流条件下平均等待最短的组合（KKL 楼层分布）\n仅按均值比较，显著性见 e2_heatmap_cells.csv", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e2_best_combo_map.png"), dpi=160)


def fig_e3():
    df = pd.read_csv(os.path.join(RES, "e3_raw.csv"))
    d = df[df.attendance == 0.8]
    cols = [c for c in d.columns if c.startswith("awt_")]
    fig, axs = plt.subplots(1, 2, figsize=(14, 4.5), gridspec_kw={"width_ratios": [2, 1]})
    hours = [8 + 0.5 * int(c[4:]) + 0.25 for c in cols]
    for ctrl, g in d.groupby("controller"):
        axs[0].plot(hours, g[cols].mean().values, "o-", ms=3, label=ctrl)
    axs[0].set_xlabel("时刻"); axs[0].set_ylabel("该半小时内到达乘客的 AWT (秒)")
    axs[0].set_title("E3 全天（合成课表，出席率 0.8）各控制方式的等待时间")
    axs[0].legend(fontsize=8)
    s = pd.read_csv(os.path.join(RES, "e3_summary.csv"))
    piv = s.pivot(index="controller", columns="attendance", values="peak_awt")
    piv.plot.bar(ax=axs[1], rot=20)
    axs[1].set_ylabel("上下课窗口内 AWT (秒)")
    axs[1].set_title("需求估计误差（出席率 0.64/0.8/0.96）")
    fig.tight_layout()
    fig.savefig(os.path.join(FIG, "e3_full_day.png"), dpi=160)


if __name__ == "__main__":
    for fn in (fig_e1, fig_e2, fig_e3):
        try:
            fn()
            print("已生成", fn.__name__)
        except FileNotFoundError as e:
            print("跳过", fn.__name__, "（缺少结果文件：", e.filename, "）")
