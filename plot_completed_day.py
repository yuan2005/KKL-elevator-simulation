"""Plot verified nominal-day arrivals and paired arrival-cohort waiting diagnostics."""
import gzip
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy import stats

from building import ENTRANCE_IDX
from complete_experiments import OUTPUT, case, digest, load, save, sha


CONTROLLERS = ("C0", "C1", "C2", "C3", "C4", "C7", "label", "realtime", "forecast")
COLORS = {"C1": "#1f77b4", "C2": "#d62728", "C4": "#2ca02c", "realtime": "#9467bd"}


def read_passengers(root, specification):
    identity = digest(specification)
    row = load(root / "cases" / f"{identity}.json")
    path = root / "passengers" / f"{identity}.json.gz"
    if row["status"] != "completed" or row["raw_sha256"] != sha(path):
        raise ValueError("Only intact completed trajectories can be plotted")
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        passengers = json.load(stream)["passengers"]
    if digest([p[:5] for p in passengers]) != row["input_hash"]:
        raise ValueError("Raw passenger input differs from the completed case")
    return passengers, row


def binned_waits(passengers, edges):
    times = np.array([p[1] for p in passengers])
    indices = np.searchsorted(edges, times, side="right")-1
    if np.any(indices < 0) or np.any(indices >= len(edges)-1):
        raise ValueError("Passenger outside the nominal-day arrival cohort")
    if any(p[5] is None or p[6] is None or not p[1] <= p[5] < p[6] for p in passengers):
        raise ValueError("Incomplete or invalid passenger chronology")
    waits = np.array([p[5]-p[1] for p in passengers])
    counts = np.bincount(indices, minlength=len(edges)-1)
    sums = np.bincount(indices, weights=waits, minlength=len(edges)-1)
    means = np.divide(sums, counts, out=np.full(len(counts), np.nan), where=counts > 0)
    return counts, means, waits


def across_days(values):
    means, lower, upper, valid_days = [], [], [], []
    for column in values.T:
        valid = column[np.isfinite(column)]
        valid_days.append(len(valid))
        center = float(valid.mean()) if len(valid) else np.nan
        half = (float(stats.t.ppf(.975, len(valid)-1)*valid.std(ddof=1)/np.sqrt(len(valid)))
                if len(valid) > 1 else np.nan)
        means.append(center)
        lower.append(center-half)
        upper.append(center+half)
    return np.array(means), np.array(lower), np.array(upper), valid_days


def clock(seconds):
    minutes = int(seconds//60)+8*60
    return f"{minutes//60:02d}:{minutes%60:02d}"


def decorate(axes):
    ticks = np.arange(0, 39601, 3600)
    for axis in axes:
        axis.axvspan(4*3600, 6*3600, color="#ffd166", alpha=.16)
        axis.grid(alpha=.22)
        axis.set_xlim(0, 39600)
        axis.set_xticks(ticks, [clock(t) for t in ticks])
    axes[-1].set_xlabel("时刻（黄色背景为12:00–14:00午餐时段）")


def main(root=OUTPUT):
    root = Path(root)
    validation = load(root / "validation.json")
    if validation["status"] != "passed" or validation["smoke"]:
        raise ValueError("Require completed, validated formal study, not smoke evidence")
    e2 = load(root / "e2.json")
    inputs = [item for item in e2["inputs"] if item["attendance"] == .8 and item["thru"] == .3]
    if len(inputs) != 10 or any(item["duration"] != 39600 for item in inputs):
        raise ValueError("Expected ten full nominal days")
    edges = np.arange(0, 39601, 300)
    centers = (edges[:-1]+edges[1:])/2
    counts, kinds = [], {name: [] for name in ("incoming", "outgoing", "interfloor", "through")}
    waits = {name: [] for name in CONTROLLERS}
    whole_day = {name: [] for name in CONTROLLERS}
    for specification in inputs:
        base, _ = read_passengers(root, case(specification, e2["specs"]["C4"]))
        daily_counts, _, _ = binned_waits(base, edges)
        counts.append(daily_counts)
        origins = np.array([p[2] in ENTRANCE_IDX for p in base])
        destinations = np.array([p[3] in ENTRANCE_IDX for p in base])
        masks = dict(incoming=origins & ~destinations, outgoing=~origins & destinations,
                     interfloor=~origins & ~destinations, through=origins & destinations)
        for kind, mask in masks.items():
            times = [p[1] for p, keep in zip(base, mask) if keep]
            kinds[kind].append(np.histogram(times, edges)[0])
        if not np.array_equal(sum(kinds[k][-1] for k in kinds), daily_counts):
            raise AssertionError("OD classes do not sum to total flow")
        for name in CONTROLLERS:
            passengers, row = read_passengers(root, case(specification, e2["specs"][name]))
            if [p[:5] for p in passengers] != [p[:5] for p in base]:
                raise AssertionError("Controllers are not compared on identical passengers")
            observed, means, actual_waits = binned_waits(passengers, edges)
            if not np.array_equal(observed, daily_counts) or abs(actual_waits.mean()-row["metrics"]["awt"]) > 1e-9:
                raise AssertionError("Arrival bins or whole-day AWT differ from validated case")
            waits[name].append(means)
            whole_day[name].append(float(actual_waits.mean()))
    counts = np.asarray(counts)
    waits = {name: np.asarray(value) for name, value in waits.items()}
    table = pd.DataFrame(dict(
        start_seconds=edges[:-1], start_clock=[clock(t) for t in edges[:-1]],
        end_clock=[clock(t) for t in edges[1:]], mean_passengers=counts.mean(axis=0),
        p10_passengers=np.percentile(counts, 10, axis=0), p90_passengers=np.percentile(counts, 90, axis=0)))
    for kind in kinds:
        table[kind] = np.asarray(kinds[kind]).mean(axis=0)
    for name in CONTROLLERS:
        table[f"awt_{name}"] = across_days(waits[name])[0]
    differences, low, high, valid_days = across_days(waits["realtime"]-waits["C4"])
    table["realtime_minus_C4"] = differences
    table["paired_ci95_low"] = low
    table["paired_ci95_high"] = high
    table["paired_days"] = valid_days
    table.to_csv(root / "daily_nominal_5min.csv", index=False)
    plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    figures = root / "figures"
    figures.mkdir(exist_ok=True)
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    axes[0].plot(centers, table.mean_passengers, color="#1f77b4", label="10日平均实际乘梯人数")
    axes[0].fill_between(centers, table.p10_passengers, table.p90_passengers,
                         color="#1f77b4", alpha=.18, label="日间P10–P90范围（非置信区间）")
    axes[0].axhline(300.97, color="#d62728", ls="--", lw=1,
                   label="候选阈值约301人（控制使用滚动窗口）")
    axes[0].set_ylabel("人数／固定5分钟窗口")
    axes[0].set_title("当前名义全天人流：出席参数0.8、过路30%、课表与午餐需求基准×2.5\n"
                      "08:00–19:00，楼梯分流后的模型乘梯人数；10个独立确认日，无平滑")
    axes[0].legend(fontsize=9, loc="upper right")
    axes[1].stackplot(centers, *(table[k] for k in kinds),
                      labels=["进楼", "离楼", "楼层间", "过路"],
                      colors=["#4c78a8", "#f58518", "#54a24b", "#b279a2"], alpha=.85)
    axes[1].set_ylabel("平均人数／5分钟")
    axes[1].legend(fontsize=9, loc="upper right")
    decorate(axes)
    fig.tight_layout()
    flow_path = figures / "daily_flow_nominal_zh.png"
    fig.savefig(flow_path, dpi=160)
    plt.close(fig)
    fig, axes = plt.subplots(2, 1, figsize=(12, 8), sharex=True)
    for name in ("C4", "C1", "C2", "realtime"):
        label = "实时候选地图（C1/C2）" if name == "realtime" else name
        axes[0].plot(centers, table[f"awt_{name}"], color=COLORS[name], label=label, lw=1.3)
    axes[0].set_ylabel("平均等待／秒")
    axes[0].set_title("同一批乘客、按到达时刻划分5分钟群体：各日群体AWT的等权平均\n"
                      "仅作时段诊断，不代表该时刻切换后的因果收益；不用于重新选择阈值")
    axes[0].legend(fontsize=9)
    axes[1].plot(centers, differences, color=COLORS["realtime"], label="实时地图−C4（负值表示地图少等）")
    axes[1].fill_between(centers, low, high, color=COLORS["realtime"], alpha=.18,
                         label="逐窗口日配对95%区间，未作多重校正")
    axes[1].axhline(0, color="black", lw=.8)
    axes[1].set_ylabel("等待差值／秒")
    axes[1].legend(fontsize=9)
    decorate(axes)
    fig.tight_layout()
    wait_path = figures / "daily_wait_nominal_zh.png"
    fig.savefig(wait_path, dpi=160)
    plt.close(fig)
    peak = int(np.argmax(table.mean_passengers))
    lunch = (edges[:-1] >= 4*3600) & (edges[:-1] < 6*3600)
    summary = dict(
        seeds=[item["seed"] for item in inputs], mean_daily_passengers=float(counts.sum(axis=1).mean()),
        mean_lunch_passengers=float(counts[:, lunch].sum(axis=1).mean()),
        peak_5min=dict(start=table.start_clock.iloc[peak], end=table.end_clock.iloc[peak],
                      mean_passengers=float(table.mean_passengers.iloc[peak])),
        mean_daily_awt={name: float(np.mean(value)) for name, value in whole_day.items()},
        validation_sha256=sha(root / "validation.json"),
        flow_band="P10-P90 across ten days; not a confidence interval",
        waiting_ci="Unadjusted day-paired 95% intervals per arrival bin; exploratory, not causal switch-window benefit")
    save(root / "daily_nominal_summary.json", summary)
    print(json.dumps(summary, ensure_ascii=True, indent=2))
    print(f"Flow: {flow_path}")
    print(f"Waiting: {wait_path}")


if __name__ == "__main__":
    main()
