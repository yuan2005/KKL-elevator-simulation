"""Paired engineering comparison; not threshold training or full E0-E3."""
import argparse
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
import csv
import hashlib
import json
from pathlib import Path
from statistics import mean

import config as C
from banking import DualBankSim
from controllers import Static
from metrics import summarize
from sim import Sim
from strategies import make_combos
from traffic import SCENARIOS, generate_scenario


ROOT = Path(__file__).resolve().parent
SEEDS = (914101, 914102, 914103)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def run_case(case):
    scenario, scale, combo, seed, dt, model = case
    C.DT = dt
    demand = replace(SCENARIOS[scenario], lam5=SCENARIOS[scenario].lam5*scale, duration=600)
    passengers, stairs = generate_scenario(demand, seed)
    input_hash = digest([asdict(p) for p in passengers])
    engine = DualBankSim if model == "independent" else Sim
    sim = engine(passengers, Static(make_combos()[combo]), demand.duration).run()
    # Legacy run() can stop after removing the last onboard passenger but
    # before its scheduled alighting timestamp. Finish that recorded service.
    while any(p.t_alight is not None and p.t_alight > sim.t for p in passengers):
        sim.step()
    result = summarize(sim)
    incomplete = sum(p.t_alight is None or p.t_alight > sim.t for p in passengers)
    if result["unserved"] or incomplete or sim.calls:
        raise AssertionError(f"Pilot did not drain completely: {case}")
    if any(p.t_board < p.t or p.t_alight <= p.t_board for p in passengers):
        raise AssertionError(f"Invalid passenger chronology: {case}")
    result.update(scenario=scenario, scale=scale, combo=combo, seed=seed, dt=dt,
                  model=model, input_hash=input_hash, stairs=stairs, incomplete=incomplete,
                  empty_stops=sim.empty_stops if model == "independent" else None,
                  completed_stops=sim.completed_stops if model == "independent" else None)
    return result


def analyze(rows):
    groups = {}
    for row in rows:
        key = tuple(row[k] for k in ("scenario", "scale", "combo", "dt"))
        groups.setdefault(key, {}).setdefault(row["seed"], {})[row["model"]] = row
    summary = []
    for key, seeds in sorted(groups.items()):
        differences = []
        for pair in seeds.values():
            if set(pair) != {"coordinated", "independent"}:
                raise ValueError("Missing paired model")
            if pair["coordinated"]["input_hash"] != pair["independent"]["input_hash"]:
                raise ValueError("Passenger inputs differ")
            differences.append(pair["independent"]["awt"]-pair["coordinated"]["awt"])
        summary.append(dict(
            zip(("scenario", "scale", "combo", "dt"), key),
            n_seeds=len(seeds),
            coordinated_awt=mean(p["coordinated"]["awt"] for p in seeds.values()),
            independent_awt=mean(p["independent"]["awt"] for p in seeds.values()),
            delta_awt=mean(differences), seed_differences=differences,
            independent_empty_stops=mean(p["independent"]["empty_stops"] for p in seeds.values()),
        ))
    return summary


def main(output, workers):
    if not 1 <= workers <= 4:
        raise ValueError("Use 1-4 workers")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite pilot evidence: {output}")
    output.mkdir(parents=True)
    cases = [(scenario, scale, combo, seed, dt, model)
             for scenario in ("S1", "S3", "S5") for scale in (1., 2.5)
             for combo in ("C0", "C1", "C7") for seed in SEEDS
             for dt in (.25, .125) for model in ("coordinated", "independent")]
    sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
               for name in ("banking.py", "sim.py", "building.py", "strategies.py",
                            "traffic.py", "config.py", "compare_banks.py")}
    protocol = dict(
        kind="engineering_pilot_not_threshold_validation", duration=600, seeds=SEEDS,
        scenarios=["S1", "S3", "S5"], demand_multipliers=[1., 2.5],
        combinations=["C0", "C1", "C7"], dt=[.25, .125], sources=sources,
        bank_model="One physical queue; both banks pressed; no cross-bank cancellation; C0-C7 retained",
        timing="Independent banks use exact motion, full closing and timed reopening; coordinated side retains historical timing",
        expected_trajectories=len(cases), status="running",
    )
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    rows = []
    with (output / "metrics.csv").open("w", encoding="utf-8", newline="") as stream:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            writer = None
            for row in pool.map(run_case, cases):
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                stream.flush()
                rows.append(row)
                if len(rows) % 24 == 0:
                    print(f"Completed {len(rows)}/{len(cases)} trajectories", flush=True)
    summary = analyze(rows)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    report = [
        "# 两个独立三梯组：同输入工程对照",
        "",
        "每格3种子，600秒需求；步长0.25/0.125秒。速度、容量、门服务时间未调大。",
        "仅作模型结构检查，不是全天验证、阈值训练或现实等待标定。",
        "全部轨迹均排空，配对乘客输入哈希一致。旧E0–E3结果未覆盖。",
        "",
        "| 场景 | 需求倍率 | 组合 | 步长/s | 六车协同AWT/s | 独立双组AWT/s | 双组−协同/s | 双组空停数 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in summary:
        report.append(
            f"| {row['scenario']} | {row['scale']} | {row['combo']} | {row['dt']} | "
            f"{row['coordinated_awt']:.3f} | {row['independent_awt']:.3f} | "
            f"{row['delta_awt']:+.3f} | {row['independent_empty_stops']:.1f} |")
    (output / "comparison_zh.md").write_text("\n".join(report)+"\n", encoding="utf-8")
    protocol["status"] = "completed"
    protocol["completed_trajectories"] = len(rows)
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    print(f"Saved {len(rows)} paired trajectories and {len(summary)} summary cells: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results" / "two_independent_banks" / "engineering_pilot")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    main(args.output, args.workers)
