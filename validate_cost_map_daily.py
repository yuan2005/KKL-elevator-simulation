"""Fresh daily development gate for the cost-map candidate, then untouched confirmation."""
from concurrent.futures import ProcessPoolExecutor, as_completed
import gzip
import json

import numpy as np

import pandas as pd

from complete_experiments import (
    ROOT, SOURCE_NAMES, case, day, demand, digest, expected_features, fixed, load, paired_summary, run_case, save, sha,
)
from research_map_methods import OUTPUT as METHODS
from revalidate_switching import FrozenPolicy


OUTPUT = ROOT / "results" / "two_independent_banks" / "cost_map_daily_gate_v1"
DEV_SEEDS = tuple(range(954101, 954111))
CONFIRM_SEEDS = tuple(range(954201, 954216))
CONDITIONS = ((.64, .3), (.8, .3), (.96, .3), (.8, .1), (.8, .5))


def compare(inputs, controls, records):
    rows = []
    for specification in inputs:
        candidate = records[digest(case(specification, controls["candidate"]))]
        reference = records[digest(case(specification, controls["C4"]))]
        if candidate["input_hash"] != reference["input_hash"]:
            raise AssertionError("Daily inputs are not paired")
        rows.append(dict(seed=specification["seed"], attendance=specification["attendance"],
                         thru=specification["thru"], candidate_awt=candidate["metrics"]["awt"],
                         C4_awt=reference["metrics"]["awt"], delta=candidate["metrics"]["awt"]-reference["metrics"]["awt"],
                         switches=candidate["switches"], passengers=candidate["input_n"]))
    summary = paired_summary([row["candidate_awt"] for row in rows], [row["C4_awt"] for row in rows])
    return rows, summary


def execute(inputs, controls, output, fingerprint, settings, stage):
    jobs = [case(specification, controller) for specification in inputs for controller in controls.values()]
    records = {}
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(run_case, (job, output, fingerprint, settings)) for job in jobs]
        for future in as_completed(futures):
            row = future.result()
            records[row["case_id"]] = row
            save(output / "progress.json", dict(phase=stage, completed=len(records), total=len(jobs)))
            print(f"{stage}: {len(records)}/{len(jobs)}", flush=True)
    return records


def validate_records(records, fingerprint):
    for row in records.values():
        raw_path = OUTPUT / "passengers" / f"{row['case_id']}.json.gz"
        if row["fingerprint"] != fingerprint or row["status"] != "completed" or sha(raw_path) != row["raw_sha256"]:
            raise AssertionError("Invalid or modified daily case")
        with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
            raw = json.load(stream)
        people = raw["passengers"]
        generated, duration = demand(json.dumps(row["case"]["input"], sort_keys=True))
        if digest(generated) != row["input_hash"] or digest([p[:5] for p in people]) != row["input_hash"]:
            raise AssertionError("Daily input cannot be regenerated")
        if len(people) != row["input_n"] or any(
                p[5] is None or p[6] is None or not p[1] <= p[5] < p[6] <= row["end_time"]+1e-8 for p in people):
            raise AssertionError("Incomplete or invalid daily passenger")
        waits = np.array([p[5]-p[1] for p in people if 0 <= p[1] < duration])
        expected = dict(awt=float(waits.mean()), p90=float(np.percentile(waits, 90)),
                        lwt=float((waits > 60).mean()*100),
                        att=float(np.mean([p[6]-p[1] for p in people if 0 <= p[1] < duration])))
        if len(waits) != row["metrics"]["n"] or any(abs(value-row["metrics"][key]) > 1e-9 for key, value in expected.items()):
            raise AssertionError("Daily metrics cannot be recomputed")
        for timestamp, feat, perturbed, chosen, clean in raw["decisions"]:
            spec = row["case"]["controller"]
            if (feat != expected_features(row["case"]["input"], timestamp, "realtime", "none", ())
                    or feat != perturbed or chosen != clean or chosen != FrozenPolicy(spec["tree"]).predict(feat)):
                raise AssertionError("Cost-map decision differs from frozen policy/window")


def main():
    candidate_path = METHODS / "relative_cost_tree_candidate.json"
    if load(METHODS / "validation.json")["status"] != "passed":
        raise RuntimeError("Candidate development comparison must be verified")
    controls = dict(candidate=dict(kind="realtime", tree=load(candidate_path)), C4=fixed("C4"))
    sources = {name: sha(ROOT / name) for name in (*SOURCE_NAMES, "research_map_methods.py", "validate_cost_map_daily.py")}
    settings = dict(dt=.25, drain_seconds=21600, training_rate_max=800, forecast_seeds=[])
    fingerprint = digest(dict(sources=sources, candidate=sha(candidate_path), dev=DEV_SEEDS,
                              confirmation=CONFIRM_SEEDS, conditions=CONDITIONS, settings=settings))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    protocol_path = OUTPUT / "protocol.json"
    if protocol_path.exists():
        protocol = load(protocol_path)
        if protocol["fingerprint"] != fingerprint:
            raise RuntimeError("Cannot mix source revisions")
    else:
        if any(OUTPUT.iterdir()):
            raise RuntimeError("Refusing unrecognized output directory")
        for folder in ("cases", "passengers", "sources"):
            (OUTPUT / folder).mkdir()
        for name in sources:
            (OUTPUT / "sources" / name).write_bytes((ROOT / name).read_bytes())
        protocol = dict(status="registered", fingerprint=fingerprint, sources=sources,
                        candidate_sha256=sha(candidate_path), controls=controls, dev_seeds=DEV_SEEDS,
                        confirmation_seeds=CONFIRM_SEEDS, confirmation_conditions=CONDITIONS,
                        gate="candidate-C4 <= -0.25s and paired95%CI upper<0",
                        baseline="Previously frozen C4, not reselected on these days",
                        no_rule_tuning=True, settings=settings)
        save(protocol_path, protocol)
    protocol["status"] = "running"
    save(protocol_path, protocol)
    try:
        inputs = [day(seed) for seed in DEV_SEEDS]
        records = execute(inputs, controls, OUTPUT, fingerprint, settings, "new_daily_development")
        rows, summary = compare(inputs, controls, records)
        pd.DataFrame(rows).to_csv(OUTPUT / "development_paired.csv", index=False)
        adopted = summary["delta"] <= -.25 and summary["ci_high"] < 0
        frozen_path = OUTPUT / "frozen_rule.json"
        frozen = dict(status="completed", adopted=bool(adopted),
                      controller=controls["candidate"] if adopted else controls["C4"],
                      candidate=controls["candidate"], candidate_sha256=sha(candidate_path),
                      development=summary, confirmation_started=False)
        if frozen_path.exists():
            if load(frozen_path) != frozen:
                raise RuntimeError("Existing freeze differs")
        else:
            save(frozen_path, frozen)
        freeze_sha = sha(frozen_path)
        print(f"Development gate: {summary}; adopted={adopted}", flush=True)
        if adopted:
            inputs = [day(seed, *CONDITIONS[index % len(CONDITIONS)])
                      for index, seed in enumerate(CONFIRM_SEEDS)]
            confirmation = execute(inputs, controls, OUTPUT, fingerprint, settings, "untouched_confirmation")
            records.update(confirmation)
            rows, summary = compare(inputs, controls, records)
            pd.DataFrame(rows).to_csv(OUTPUT / "confirmation_paired.csv", index=False)
            save(OUTPUT / "confirmation.json", dict(status="completed", summary=summary,
                 frozen_sha256=freeze_sha, used_for_tuning=False))
        validate_records(records, fingerprint)
        dev_rows, dev_summary = compare([day(seed) for seed in DEV_SEEDS], controls, records)
        if dev_summary != frozen["development"]:
            raise AssertionError("Development comparison cannot be recomputed")
        if sha(frozen_path) != freeze_sha or sha(candidate_path) != protocol["candidate_sha256"] or any(
                sha(ROOT / name) != checksum for name, checksum in sources.items()):
            raise AssertionError("Sources or frozen rule changed")
        save(OUTPUT / "summary.json", dict(status="completed", development=frozen["development"],
             adopted=bool(adopted), confirmation=summary if adopted else None,
             confirmation_skipped_reason=None if adopted else "Failed preregistered development adoption gate",
             cases=sorted(records)))
        save(OUTPUT / "validation.json", dict(status="passed", cases=len(records),
             raw_metrics=True, regenerated_inputs=True, rolling_decisions=True, freeze_preserved=True,
             development_pairing=True, new_disjoint_seeds=True))
        protocol["status"] = "completed"
        save(protocol_path, protocol)
        save(OUTPUT / "progress.json", dict(phase="completed", cases=len(records), adopted=bool(adopted)))
    except Exception as error:
        protocol.update(status="failed", failure=str(error))
        save(protocol_path, protocol)
        raise


if __name__ == "__main__":
    main()
