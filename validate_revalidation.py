"""Independent raw-passenger and frozen-protocol checks for the ordered rerun."""
import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean

import numpy as np

from check_timestep import analyze, digest
from controllers import features
from revalidate_switching import OUTPUT, Study, input_records
from traffic import Passenger, load_timetable


ROOT = Path(__file__).resolve().parent


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def equal(actual, expected, message):
    if actual != expected:
        raise AssertionError(message)


def validate_numerical(path):
    protocol = load(path / "protocol.json")
    equal(protocol["status"], "completed", "Numerical stage is not complete")
    for name, sha in protocol["sources"].items():
        equal(hashlib.sha256((path / "sources" / name).read_bytes()).hexdigest(), sha,
              f"Numerical source snapshot changed: {name}")
        equal(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), sha,
              f"Current numerical source revision differs: {name}")
    strings = {"profile", "combo", "status", "input_hash", "passenger_output", "passenger_sha256"}
    rows = []
    with (path / "metrics.csv").open(newline="", encoding="utf-8") as stream:
        for row in csv.DictReader(stream):
            rows.append({key: value if key in strings else None if value == "" else float(value)
                         for key, value in row.items()})
    equal(analyze(rows), load(path / "screen.json"), "Numerical screen cannot be recomputed")
    equal(len(rows), 162, "Wrong numerical-screen case count")
    trajectories = {}
    for row in rows:
        raw_path = path / "passengers" / row["passenger_output"]
        equal(hashlib.sha256(raw_path.read_bytes()).hexdigest(), row["passenger_sha256"],
              "Numerical raw-passenger hash differs")
        with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
            passengers = json.load(stream)
        cohort = [p for p in passengers if row["evaluate_from"] <= p[1] < row["duration"]]
        waits = np.array([p[4]-p[1] for p in cohort])
        for value, key in ((waits.mean(), "awt"), (np.percentile(waits, 90), "p90"),
                           ((waits > 60).mean()*100, "lwt")):
            if abs(float(value)-row[key]) > 1e-10:
                raise AssertionError(f"Numerical {key} cannot be recomputed")
        if any(p[4] is None or p[5] is None or not p[1] <= p[4] < p[5] for p in passengers):
            raise AssertionError("Invalid numerical passenger chronology")
        key = (row["profile"], row["combo"], row["seed"])
        if key in trajectories:
            equal(passengers, trajectories[key], "Integration cap changed a passenger trajectory")
        else:
            trajectories[key] = passengers
    result = dict(status="passed", verified_cases=len(rows), source_snapshots_match=True,
                  current_sources_match=True, raw_metrics_recomputed=True,
                  identical_passenger_trajectories_across_all_three_caps=True)
    (path / "validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def validate_study(path):
    protocol = load(path / "protocol.json")
    equal(protocol["status"], "completed", "Research is still running; refusing final validation")
    for name, sha in protocol["sources"].items():
        equal(hashlib.sha256((path / "sources" / name).read_bytes()).hexdigest(), sha,
              f"Frozen source changed: {name}")
        equal(hashlib.sha256((ROOT / name).read_bytes()).hexdigest(), sha,
              f"Current source changed since research: {name}")
    equal(hashlib.sha256((path / "sources" / "timetable_synthetic.csv").read_bytes()).hexdigest(),
          protocol["timetable_sha256"], "Frozen timetable changed")
    equal(hashlib.sha256((ROOT / "data" / "timetable_synthetic.csv").read_bytes()).hexdigest(),
          protocol["timetable_sha256"], "Current timetable differs from frozen input")
    equal(digest(dict(sources=protocol["sources"], timetable=protocol["timetable_sha256"],
                      legacy=load(path / "legacy_rule.json"))),
          protocol["fingerprint"], "Protocol fingerprint cannot be recomputed")
    baseline, boundary, frozen, confirmation, robust = [
        load(path / f"{name}.json") for name in
        ("baseline", "boundary", "frozen_rule", "confirmation", "robustness")]
    for stage in (baseline, boundary, frozen, confirmation, robust):
        equal(stage["status"], "completed", "A research stage is incomplete")
    equal(hashlib.sha256((path / "baseline.json").read_bytes()).hexdigest(),
          frozen["baseline_sha256"], "Baseline changed after freeze")
    equal(hashlib.sha256((path / "boundary.json").read_bytes()).hexdigest(),
          frozen["boundary_sha256"], "Boundary development changed after freeze")
    equal(hashlib.sha256((path / "frozen_rule.json").read_bytes()).hexdigest(),
          confirmation["frozen_sha256"], "Rule changed after freezing")
    records = {}
    timetable = load_timetable(path / "sources" / "timetable_synthetic.csv")
    peak_windows = [(c["start"]-600, c["start"]+120) for c in timetable]
    peak_windows += [(c["end"], c["end"]+360) for c in timetable]
    for case_path in (path / "cases").glob("*.json"):
        row = load(case_path)
        equal(row["status"], "completed", "Undrained or invalid case present")
        equal(row["fingerprint"], protocol["fingerprint"], "Mixed study revision")
        equal(digest(row["case"]), row["case_id"], "Case key does not match specification")
        raw_path = path / "passengers" / f"{row['case_id']}.json.gz"
        equal(hashlib.sha256(raw_path.read_bytes()).hexdigest(), row["passenger_sha256"],
              "Raw passenger output changed")
        with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
            passengers = json.load(stream)
        equal(digest([p[:5] for p in passengers]), row["input_hash"], "Input hash does not match passengers")
        generated, _ = input_records(json.dumps(row["case"]["input"], sort_keys=True))
        equal(digest(generated), row["input_hash"], "Frozen seed/input cannot reproduce the passenger stream")
        if any(p[5] is None or p[6] is None or not p[1] <= p[5] < p[6] <= row["end_time"]+1e-8
               for p in passengers):
            raise AssertionError("Invalid or incomplete study passenger chronology")
        end = 39600 if row["case"]["input"]["kind"] == "day" else row["case"]["input"]["scenario"]["duration"]
        cohorts = dict(metrics=[p for p in passengers if 0 <= p[1] < end])
        if row["case"]["input"]["kind"] == "day":
            cohorts["lunch"] = [p for p in passengers if 4*3600 <= p[1] < 6*3600]
            cohorts["peak"] = [p for p in passengers if 0 <= p[1] < end and
                               any(a <= p[1] < b for a, b in peak_windows)]
        for name, cohort in cohorts.items():
            waits = np.array([p[5]-p[1] for p in cohort])
            journeys = np.array([p[6]-p[1] for p in cohort])
            equal(len(cohort), row[name]["n"], "Wrong study evaluation cohort")
            for value, key in ((waits.mean(), "awt"), (journeys.mean(), "att"),
                               (np.percentile(waits, 90), "p90"), ((waits > 60).mean()*100, "lwt")):
                if abs(float(value)-row[name][key]) > 1e-10:
                    raise AssertionError(f"Study {name}/{key} cannot be recomputed")
        equal(features([Passenger(*p[:5]) for p in passengers], end),
              row["features"], "Measured input features cannot be recomputed")
        records[row["case_id"]] = row
    baseline_rows = [records[key] for key in baseline["cases"]]
    means = {combo: mean(row["metrics"]["awt"] for row in baseline_rows
                         if row["case"]["controller"]["combo"] == combo)
             for combo in protocol["fixed_candidates"]}
    equal(means, baseline["mean_awt"], "Fixed baseline means differ")
    equal(min(protocol["fixed_candidates"], key=lambda combo: means[combo]),
          baseline["selected"], "Wrong development-selected fixed baseline")
    for point in boundary["points"]:
        group = [records[key] for key in point["cases"]]
        for combo in ("C1", "C7"):
            equal(mean(row["metrics"]["awt"] for row in group if row["case"]["controller"]["combo"] == combo),
                  point[f"awt_{combo}"], "Boundary point mean cannot be recomputed")
    seeds = [set(protocol[name]) for name in
             ("dev_seeds", "boundary_seeds", "confirmation_seeds", "robust_seeds")]
    for index, group in enumerate(seeds):
        for other in seeds[index+1:]:
            if group & other:
                raise AssertionError("Development and confirmation seeds overlap")
    verifier = Study.__new__(Study)
    for stage, reference in ((confirmation, "best_fixed"), (robust, "ideal")):
        rows = [records[key] for key in stage["cases"]]
        actual = verifier.comparisons(stage["inputs"], stage["specs"], rows, reference)
        equal(actual, stage["comparisons"], "Paired daily result cannot be recomputed")
        equal(stage["used_for_selection"], False, "Holdout cases used for selection")
    result = dict(status="passed", unique_completed_cases=len(records),
                  all_raw_passenger_metrics_recomputed=True, zero_incomplete_passengers=True,
                  frozen_rule_hash_matches=True, fixed_baseline_recomputed=True,
                  confirmation_and_robustness_recomputed=True, disjoint_seed_groups=True,
                  frozen_and_current_sources_match=True)
    (path / "validation.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("numerical", "complete"), default="complete")
    args = parser.parse_args()
    if args.stage == "numerical":
        print(validate_numerical(OUTPUT.parent / "step_gate_2p5_chronological_v2"))
    else:
        print(validate_study(OUTPUT))
