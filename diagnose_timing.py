"""Matched timing corrections and demand stress tests; not threshold training."""
import argparse
import ast
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
import csv
import hashlib
import importlib.util
import json
from pathlib import Path
from statistics import mean

import config as C
from banking import DualBankSim
from controllers import Static
from metrics import summarize
from strategies import make_combos
from traffic import SCENARIOS, generate_day, generate_scenario, load_timetable


ROOT = Path(__file__).resolve().parent
DEFAULT_ROOT = ROOT / "results" / "two_independent_banks" / "timing_diagnosis"
SEEDS = (914201, 914202, 914203)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def baseline_engine(folder):
    spec = importlib.util.spec_from_file_location("pre_timing_banking", folder / "banking.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DualBankSim


def run_case(case):
    profile, scale, combo, seed, dt, model, baseline = case
    C.DT = dt
    if profile == "day":
        passengers = generate_day(load_timetable(str(ROOT / "data" / "timetable_synthetic.csv")),
                                  seed, attendance=.8)
        end = 11*3600
    else:
        scenario = replace(SCENARIOS[profile], lam5=SCENARIOS[profile].lam5*scale, duration=900)
        passengers, _ = generate_scenario(scenario, seed)
        end = scenario.duration
    input_hash = digest([asdict(p) for p in passengers])
    engine = DualBankSim if model == "corrected" else baseline_engine(baseline)

    class Observed(engine):
        def __init__(self):
            super().__init__(passengers, Static(make_combos()[combo]), end)
            self.observations = []
            self.next_sample = 0.

        def step(self):
            super().step()
            if self.t+1e-8 >= self.next_sample:
                # Queued plus passengers reserved for boarding later in the current door service.
                waiting = sum(len(q) for q in self.waiting.values())+sum(
                    p.t_board > self.t for car in self.cars for p in car.passengers)
                self.observations.append((self.t, waiting,
                                          sum(car.state != "idle" for car in self.cars),
                                          sum(car.state == "moving" for car in self.cars)))
                self.next_sample += 1.

    sim = Observed().run()
    result = summarize(sim)
    incomplete = sum(p.t_alight is None or p.t_alight > sim.t for p in passengers)
    if result["unserved"] or incomplete or sim.calls or sim._sessions:
        raise AssertionError(f"Undrained case: {case}")
    if any(p.t_board < p.t or p.t_alight <= p.t_board for p in passengers):
        raise AssertionError(f"Invalid passenger chronology: {case}")
    samples = [row for row in sim.observations if row[0] <= end]
    states = sorted(row[1] for row in samples)
    result.update(profile=profile, scale=scale, combo=combo, seed=seed, dt=dt, model=model,
                  input_hash=input_hash, incomplete=incomplete, duration=end,
                  realized_per_minute=len(passengers)/(end/60),
                  average_waiting=mean(row[1] for row in samples),
                  p95_waiting=states[int(.95*(len(states)-1))], max_waiting=max(states),
                  busy_fraction=mean(row[2]/6 for row in samples),
                  moving_fraction=mean(row[3]/6 for row in samples),
                  empty_stops=sim.empty_stops, completed_stops=sim.completed_stops,
                  reopenings=sim.reopenings if model == "corrected" else None)
    return result


def analyze(rows):
    groups = {}
    seen = set()
    for row in rows:
        key = tuple(row[k] for k in ("profile", "scale", "combo", "dt"))
        unique = key+(row["seed"], row["model"])
        if unique in seen:
            raise ValueError(f"Duplicate trajectory: {unique}")
        seen.add(unique)
        groups.setdefault(key, {}).setdefault(row["seed"], {})[row["model"]] = row
    summary = []
    for key, seeds in sorted(groups.items()):
        if set(seeds) != set(SEEDS):
            raise ValueError("Missing confirmation seed")
        for pair in seeds.values():
            if set(pair) != {"before", "corrected"} or pair["before"]["input_hash"] != pair["corrected"]["input_hash"]:
                raise ValueError("Incomplete pair or different input demand")
        row = dict(zip(("profile", "scale", "combo", "dt"), key))
        for model in ("before", "corrected"):
            for name in ("awt", "p90", "lwt", "realized_per_minute", "average_waiting",
                         "p95_waiting", "max_waiting", "busy_fraction", "moving_fraction", "empty_stops"):
                row[f"{model}_{name}"] = mean(pair[model][name] for pair in seeds.values())
        row["seed_delta_awt"] = [seeds[s]["corrected"]["awt"]-seeds[s]["before"]["awt"] for s in SEEDS]
        row["delta_awt"] = mean(row["seed_delta_awt"])
        summary.append(row)
    return summary


def main(output, baseline, workers):
    if not 1 <= workers <= 4:
        raise ValueError("Use 1-4 workers")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite evidence: {output}")
    sources = ("banking.py", "sim.py", "building.py", "config.py", "strategies.py", "traffic.py")
    # The frozen subclass imports these dependencies from the current workspace.
    for name in ("sim.py", "config.py", "strategies.py", "traffic.py"):
        if (baseline / name).read_bytes() != (ROOT / name).read_bytes():
            raise ValueError(f"Frozen baseline dependency changed: {name}")
    old_building = ast.parse((baseline / "building.py").read_text(encoding="utf-8"))
    current_building = ast.parse((ROOT / "building.py").read_text(encoding="utf-8"))
    current_building.body = [node for node in current_building.body
                             if not isinstance(node, ast.FunctionDef) or node.name != "advance_motion"]
    if ast.dump(old_building) != ast.dump(current_building):
        raise ValueError("Legacy building definitions changed")
    output.mkdir(parents=True)
    cases = [(profile, scale, combo, seed, dt, model, baseline)
             for profile, scales, combinations, steps in (
                 ("S3", (1., 2.5, 4.), ("C0", "C1", "C7"), (.25, .125)),
                 ("day", (1.,), ("C0", "C1"), (.25,)))
             for scale in scales for combo in combinations for seed in SEEDS
             for dt in steps for model in ("before", "corrected")]
    protocol = dict(
        kind="timing_diagnosis_not_threshold_validation", seeds=SEEDS, expected_runs=len(cases),
        status="running", workers=workers, baseline=str(baseline),
        sources={name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
                 for name in sources+("diagnose_timing.py", "test_timing.py")},
        baseline_sources={name: hashlib.sha256((baseline / name).read_bytes()).hexdigest() for name in sources},
        timing="Exact bounded-acceleration motion, strict stoppability, separate door phases; arrivals during closing incur full reopening, boarding and another full closing cycle",
        parameters="Unchanged physical defaults; S3 demand-rate stress, not real-world calibration",
        daily_input="Synthetic 18-class timetable; attendance=.8; 08:00-19:00",
        measurement="Queue and non-idle car count sampled every second during demand; no queue-derived calibration",
    )
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    rows = []
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            writer = None
            for row in pool.map(run_case, cases):
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                stream.flush()
                rows.append(row)
                if len(rows) % 12 == 0:
                    print(f"Completed {len(rows)}/{len(cases)} trajectories", flush=True)
    summary = analyze(rows)
    (output / "summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    protocol.update(status="completed", completed_runs=len(rows), paired_cells=len(summary),
                    metrics_sha256=hashlib.sha256((output / "metrics.csv").read_bytes()).hexdigest())
    (output / "protocol.json").write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    print(f"Saved {len(rows)} fully drained trajectories and {len(summary)} paired cells: {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_ROOT / "baseline")
    parser.add_argument("--output", type=Path, default=DEFAULT_ROOT / "verified_reopening")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    main(args.output.resolve(), args.baseline.resolve(), args.workers)
