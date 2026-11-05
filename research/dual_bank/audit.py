"""Reconstruct passenger, latch, bank eligibility, paired-input and replay evidence."""
import argparse
from collections import Counter
import hashlib
from pathlib import Path
import tempfile

import numpy as np

from research.dual_bank.control import DayControl, FixedControl
from research.dual_bank.demand import WARMUP, measurement_seconds, sustained
from research.dual_bank.engine import DualBankSim
from research.dual_bank.experiment import (
    DEFAULT, cohort_metrics, configure, digest, fit_rules, load, locked,
    read_raw, request_record, save, training_record, transition_day, verify,
)
from research.dual_bank.timetable import generate


def reconstruct(raw):
    inputs, service = raw["inputs"], raw["service"]
    expected = {p["id"]: p for p in inputs}
    if len(expected) != len(inputs) or len(service) != len(inputs):
        raise AssertionError("Passenger duplication or loss")
    if digest(inputs) != raw["input_hash"]:
        raise AssertionError("Input hash changed")
    if len({p["id"] for p in service}) != len(service):
        raise AssertionError("Duplicate service record")
    serviced = {p["id"]: p for p in service}
    boardings, alightings = Counter(), Counter()
    for p in service:
        source = expected[p["id"]]
        if (source["t"], source["o"], source["d"]) != (
            p["arrival"], p["origin"], p["destination"]):
            raise AssertionError("Service/input identity mismatch")
        if (p["board"] is None or p["alight"] is None or p["physical_board"] is None
                or p["physical_alight"] is None or not 0 <= p["car"] < 6):
            raise AssertionError("Incomplete passenger service")
        if not (p["arrival"] <= p["registration"]+1e-8
                and p["registration"] <= p["physical_board"]+1e-8
                and p["physical_board"] <= p["board"]+1e-8
                and p["board"] <= p["alight"]+1e-8
                and p["physical_board"] <= p["physical_alight"]+1e-8
                and p["physical_alight"] <= p["alight"]+1e-8):
            raise AssertionError("Invalid service chronology")
        boardings[p["physical_board"], p["car"]] += 1
        alightings[p["physical_alight"], p["car"]] += 1
    expected_boardings, expected_alightings = Counter(), Counter()
    clears = set()
    session_boardings = Counter()
    boarded_ids = []
    for event in raw["boardings"]:
        if event["bank"] != event["car"]//3 or event["boarded"] != len(event["passenger_ids"]):
            raise AssertionError("Invalid bank boarding event")
        if event["boarded"]:
            expected_boardings[event["t"], event["car"]] += event["boarded"]
        session_boardings[event["stop"]] += event["boarded"]
        for identifier in event["passenger_ids"]:
            boarded_ids.append(identifier)
            passenger = serviced[identifier]
            if (passenger["physical_board"], passenger["car"]) != (event["t"], event["car"]):
                raise AssertionError("Boarding identity/time differs from service record")
        for key in event["cleared"]:
            if key[2] != event["bank"]:
                raise AssertionError("Cross-bank boarding clear")
            clears.add((event["t"], event["car"], tuple(key)))
    if len(boarded_ids) != len(set(boarded_ids)) or set(boarded_ids) != set(expected):
        raise AssertionError("Boarding events duplicate or omit passengers")
    for index, stop in enumerate(raw["stops"]):
        if stop["bank"] != stop["car"]//3:
            raise AssertionError("Stop assigned outside own bank")
        if stop["boarded"] != session_boardings[index]:
            raise AssertionError("Door-session boarded total mismatch")
        if stop["alighted"]:
            expected_alightings[stop["t"], stop["car"]] += stop["alighted"]
        if stop["empty"] != (stop["boarded"] == 0 and stop["alighted"] == 0):
            raise AssertionError("Invalid empty-stop label")
        for key in stop["calls"]:
            if key[2] != stop["bank"] or key[0] != stop["floor"]:
                raise AssertionError("Cross-bank call clearing")
    if boardings != expected_boardings or alightings != expected_alightings:
        raise AssertionError("Stop counts do not reconstruct passenger service")
    latched = set()
    for event in raw["latches"]:
        key = tuple(event["key"])
        if key[2] != event["bank"]:
            raise AssertionError("Latch has wrong bank")
        if event["action"] == "set":
            if key in latched:
                raise AssertionError("Duplicate latched call")
            latched.add(key)
        elif event["action"] == "clear":
            if (key not in latched or event["car"]//3 != event["bank"]
                    or (event["t"], event["car"], key) not in clears):
                raise AssertionError("Call cleared without its own bank stop")
            latched.remove(key)
        else:
            raise AssertionError("Unknown latch event")
    if latched:
        raise AssertionError("Ghost calls not drained")
    for decision in raw["decisions"]:
        if decision["key"][2] != decision["bank"]:
            raise AssertionError("Decision uses another bank's latch")
        if decision["car"] is not None and decision["car"]//3 != decision["bank"]:
            raise AssertionError("Dispatcher selected another bank's car")
    m, spec = raw["metrics"], raw["spec"]
    if m["incomplete"] or m["unserved"]:
        raise AssertionError("Invalid formal trajectory")
    if m["empty_stops"] != sum(s["empty"] and s["closed"] is not None for s in raw["stops"]):
        raise AssertionError("Empty-stop metric mismatch")
    cohort, start, allow_empty = set(expected), 0., False
    if m["phase"].startswith("sustained"):
        cohort = {p["id"] for p in inputs
                  if WARMUP <= p["t"] < WARMUP+measurement_seconds(m["regime"])}
    elif m["phase"].startswith("transition"):
        cohort = set(raw["extra"]["cohort"])
        start, allow_empty = raw["extra"]["cohort_start"], True
        if digest(raw["extra"]["snapshot"]) != raw["extra"]["snapshot_hash"]:
            raise AssertionError("Snapshot changed")
    calculated = cohort_metrics(service, cohort, start, allow_empty)
    for key in ("awt", "p90", "lwt", "n"):
        if not np.isclose(calculated[key], m[key], rtol=1e-12, atol=1e-9):
            raise AssertionError(f"Metric reconstruction mismatch: {key}")
    return dict(passengers=len(service), decisions=len(raw["decisions"]),
                empty_stops=m["empty_stops"])


def replay(raw):
    spec = raw["spec"]
    configure(spec)
    if spec["kind"] == "sustained":
        passengers, end, _ = sustained(spec["level"], spec["regime"], spec["seed"])
        control = FixedControl(spec["setting"])
    else:
        passengers, end, _ = generate(spec["level"], spec["seed"])
        if spec["policy"] in ("fixed_level", "fixed_global") or spec["kind"] == "fixed_dev":
            control = FixedControl(spec["setting"])
        else:
            control = DayControl(spec["initial"], spec["mapping"], spec["policy"], spec.get("rules"))
    if digest([request_record(p) for p in passengers]) != raw["input_hash"]:
        raise AssertionError("Replayed generated input differs")
    sim = DualBankSim(passengers, control, end).run()
    for actual, expected in ((sim.service_records(), raw["service"]),
                             (sim.stop_log, raw["stops"]), (sim.latch_log, raw["latches"]),
                             (sim.board_log, raw["boardings"]),
                             (sim.decision_log, raw["decisions"])):
        if digest(actual) != digest(expected):
            raise AssertionError("Full replay mismatch")


def audit(root, replays=True):
    p, ranking, switching = verify(root), locked(root, "ranking"), locked(root, "switching")
    stats_before = load(root / "statistics.json")
    counts, totals, hashes, paired_inputs, snapshots = Counter(), Counter(), {}, {}, {}
    transition_records = []
    replay_paths = []
    development = {"sustained_dev": [], "fixed_dev": []}
    for phase in p["expected"]:
        if phase == "total":
            continue
        paths = sorted((root / "raw" / phase).glob("*.json.gz"))
        counts[phase] = len(paths)
        if counts[phase] != p["expected"][phase]:
            raise AssertionError(f"Incomplete phase: {phase}")
        for path in paths:
            raw = read_raw(path)
            totals.update(reconstruct(raw))
            hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
            m = raw["metrics"]
            if phase in development:
                development[phase].append(m)
            if phase == "transition_dev":
                transition_records.append(training_record(raw))
            if phase.startswith("transition"):
                identity = (phase, m["level"], m["seed"], m["boundary"], m["background"])
                pair = (raw["input_hash"], raw["extra"]["snapshot_hash"],
                        digest(raw["extra"]["cohort"]))
                if identity in snapshots and snapshots[identity] != pair:
                    raise AssertionError("Branches do not share identical state/input/cohort")
                snapshots[identity] = pair
            else:
                family = "sustained" if phase.startswith("sustained") else "daily"
                identity = (family, m["level"], m["seed"], m.get("regime"))
                if identity in paired_inputs and paired_inputs[identity] != raw["input_hash"]:
                    raise AssertionError("Policies or resolutions have different actual input")
                paired_inputs[identity] = raw["input_hash"]
            if phase in ("daily_eval", "daily_fine") and m["seed"] == p["evaluation_seeds"][0]:
                if m["policy"] in ("fixed_level", "naive", "gated"):
                    replay_paths.append(path)
            if phase == "sustained_eval" and m["seed"] == p["evaluation_seeds"][0]:
                if m["regime"] == "lunch_S3" and m["policy"] == ranking["ranking"][m["level"]][m["regime"]][0]:
                    replay_paths.append(path)
    if sum(counts.values()) != p["expected"]["total"]:
        raise AssertionError("Formal total mismatch")
    if digest([development["sustained_dev"], development["fixed_dev"]]) != ranking["development_hash"]:
        raise AssertionError("Ranking development evidence changed")
    refit = fit_rules(transition_records)
    for key in ("rules", "trials", "details"):
        if digest(refit[key]) != digest(switching[key]):
            raise AssertionError("Development gate cannot be reconstructed")
    replayed_branches = 0
    if replays:
        for path in replay_paths:
            replay(read_raw(path))
            print(f"replayed {path.name}", flush=True)
        parent_paths = sorted((root / "raw" / "snapshot_parents").glob("*.json.gz"))
        selected = [
            next(path for path in parent_paths
                 if path.name.startswith(f"transition_eval__low__{p['evaluation_seeds'][0]}")),
            next(path for path in parent_paths
                 if path.name.startswith(f"transition_fine__high__{p['evaluation_seeds'][0]}")),
        ]
        with tempfile.TemporaryDirectory() as directory:
            temporary = Path(directory)
            for path in selected:
                original = read_raw(path)
                spec = dict(original["spec"], root=str(temporary))
                transition_day(spec)
                fresh_parent = read_raw(temporary / "raw" / "snapshot_parents" / path.name)
                if digest(fresh_parent["service"]) != digest(original["service"]):
                    raise AssertionError("Snapshot parent replay differs")
                for fresh in (temporary / "raw" / spec["phase"]).glob(f"{spec['key']}__*.json.gz"):
                    expected = read_raw(root / "raw" / spec["phase"] / fresh.name)
                    actual = read_raw(fresh)
                    for key in ("signature", "input_hash", "service", "stops", "latches",
                                "boardings", "decisions"):
                        if digest(actual[key]) != digest(expected[key]):
                            raise AssertionError("Snapshot branch replay differs")
                    replayed_branches += 1
                print(f"replayed snapshot day {path.name}", flush=True)
    from research.dual_bank.analysis import analyze
    stats_after = analyze(root)
    if digest(stats_after) != digest(stats_before):
        raise AssertionError("Repeated statistical analysis changed")
    save(root / "raw_hashes.json", hashes)
    result = dict(passed=True, counts=dict(counts), total=sum(counts.values()),
                  reconstructed=dict(totals), paired_input_days=len(paired_inputs),
                  paired_snapshots=len(snapshots), full_replays=len(replay_paths) if replays else 0,
                  snapshot_branch_replays=replayed_branches,
                  sources=len(p["sources"]), source_hash=digest(p["sources"]),
                  ranking_hash=digest(ranking), switching_hash=digest(switching),
                  raw_hashes_hash=digest(hashes))
    save(root / "audit.json", result)
    print(result, flush=True)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=DEFAULT)
    parser.add_argument("--no-replays", action="store_true")
    args = parser.parse_args()
    audit(args.root, not args.no_replays)
