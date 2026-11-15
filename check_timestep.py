"""Numerical screening before E0-E3; never trains or validates switching rules."""
import argparse
from concurrent.futures import ProcessPoolExecutor
import csv
from dataclasses import asdict, replace
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean

import config as C
import experiments as E
from banking import DualBankSim
from controllers import Static
from metrics import summarize
from strategies import make_combos
from traffic import Passenger, generate_scenario


ROOT = Path(__file__).resolve().parent
SEEDS = (914301, 914302, 914303)
STEPS = (.25, .125, .0625)
PROFILES = ("S3", "S4", "lunch_overlap")
SOURCES = ("config.py", "building.py", "traffic.py", "banking.py", "sim.py",
           "strategies.py", "controllers.py", "metrics.py", "experiments.py",
           "check_timestep.py")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@lru_cache(maxsize=32)
def demand(profile, seed):
    if profile in ("S3", "S4"):
        scenario = E.SCENARIOS[profile]
        passengers, _ = generate_scenario(scenario, seed)
        end, evaluate_from = scenario.duration, 0.
    elif profile == "lunch_overlap":
        # 12:15-12:45 warms the state before evaluating the 12:50/13:00 changeover.
        start, end_absolute = 4.25*3600, 5.25*3600
        passengers = E.generate_day(E.load_timetable(E.TIMETABLE), seed)
        passengers = [replace(p, t=p.t-start) for p in passengers if start <= p.t < end_absolute]
        end, evaluate_from = end_absolute-start, 1800.
    else:
        raise ValueError(f"Unknown profile: {profile}")
    records = tuple((index, p.t, p.o, p.d, p.group) for index, p in enumerate(passengers))
    return records, end, evaluate_from


def run_case(case):
    profile, combo, seed, dt, output = case
    C.DT = dt
    records, end, evaluate_from = demand(profile, seed)
    passengers = [Passenger(*record) for record in records]
    sim = DualBankSim(passengers, Static(make_combos()[combo]), end).run()
    incomplete = sum(p.t_alight is None or p.t_alight > sim.t+1e-8 for p in passengers)
    unserved = sum(p.t_board is None for p in passengers)
    bad_times = sum(
        (p.t_board is not None and p.t_board < p.t-1e-8)
        or (p.t_alight is not None and (p.t_board is None or p.t_alight <= p.t_board))
        for p in passengers)
    drained = not (incomplete or unserved or sim.waiting or sim.calls or sim._sessions or bad_times)
    result = dict(
        profile=profile, combo=combo, seed=seed, dt=dt,
        status="completed" if drained else "invalid_or_undrained",
        input_hash=digest(records), input_n=len(records), duration=end, evaluate_from=evaluate_from,
        evaluation_n=sum(evaluate_from <= p.t < end for p in passengers),
        unserved=unserved, incomplete=incomplete, bad_times=bad_times,
        residual_calls=len(sim.calls), residual_sessions=len(sim._sessions),
        end_time=sim.t, empty_stops=sim.empty_stops, reopenings=sim.reopenings)
    if drained:
        measured = summarize(sim, t_from=evaluate_from, t_to=end)
        if measured["n"] == 0:
            raise ValueError(f"Empty evaluation cohort: {case}")
        result.update(awt=measured["awt"], p90=measured["p90"], lwt=measured["lwt"])
    else:
        result.update(awt=None, p90=None, lwt=None)
    raw = [(p.id, p.t, p.o, p.d, p.t_board, p.t_alight, p.car) for p in passengers]
    name = f"{profile}_{combo}_{seed}_{dt}.json.gz"
    with gzip.open(output / "passengers" / name, "wt", encoding="utf-8") as stream:
        json.dump(raw, stream, separators=(",", ":"))
    result["passenger_output"] = name
    result["passenger_sha256"] = hashlib.sha256((output / "passengers" / name).read_bytes()).hexdigest()
    return result


def analyze(rows, seeds=SEEDS, steps=STEPS, profiles=PROFILES, combos=E.MAP_COMBOS):
    expected = {(profile, combo, seed, dt)
                for profile in profiles for combo in combos for seed in seeds for dt in steps}
    keyed = {(r["profile"], r["combo"], r["seed"], r["dt"]): r for r in rows}
    if len(keyed) != len(rows) or set(keyed) != expected:
        raise ValueError("Missing or duplicate numerical-screening cases")
    for profile in profiles:
        for seed in seeds:
            group = [keyed[profile, combo, seed, dt] for combo in combos for dt in steps]
            if len({r["input_hash"] for r in group}) != 1:
                raise ValueError("Passenger inputs differ across algorithms or time steps")
    failures = [dict(profile=r["profile"], combo=r["combo"], seed=r["seed"], dt=r["dt"],
                     status=r["status"], incomplete=r["incomplete"], residual_calls=r["residual_calls"])
                for r in rows if r["status"] != "completed"]
    comparisons, averages = [], []
    for profile in profiles:
        for combo in combos:
            for dt in steps:
                group = [keyed[profile, combo, seed, dt] for seed in seeds]
                if all(r["status"] == "completed" for r in group):
                    averages.append(dict(profile=profile, combo=combo, dt=dt,
                                         awt=mean(r["awt"] for r in group),
                                         p90=mean(r["p90"] for r in group),
                                         lwt=mean(r["lwt"] for r in group)))
            for coarse, fine in zip(steps, steps[1:]):
                pairs = [(keyed[profile, combo, seed, coarse], keyed[profile, combo, seed, fine])
                         for seed in seeds]
                if any(r["status"] != "completed" for pair in pairs for r in pair):
                    comparisons.append(dict(profile=profile, combo=combo, coarse=coarse, fine=fine,
                                            passed=False, reason="Invalid or undrained trajectory"))
                    continue
                awt_changes = [a["awt"]-b["awt"] for a, b in pairs]
                p90_change = mean(a["p90"]-b["p90"] for a, b in pairs)
                awt_tolerance = max(1., .05*mean(b["awt"] for _, b in pairs))
                p90_tolerance = max(3., .10*mean(b["p90"] for _, b in pairs))
                passed = abs(mean(awt_changes)) <= awt_tolerance and abs(p90_change) <= p90_tolerance
                comparisons.append(dict(
                    profile=profile, combo=combo, coarse=coarse, fine=fine, passed=passed,
                    delta_awt=mean(awt_changes), paired_awt_changes=awt_changes,
                    mean_absolute_seed_awt_change=mean(abs(x) for x in awt_changes),
                    delta_p90=p90_change, awt_tolerance=awt_tolerance, p90_tolerance=p90_tolerance))
    ranks = []
    for profile in profiles:
        for coarse, fine in zip(steps, steps[1:]):
            winners = []
            for dt in (coarse, fine):
                group = [r for r in averages if r["profile"] == profile and r["dt"] == dt
                         and r["combo"] in E.SWITCHABLE]
                if len(group) != len([combo for combo in combos if combo in E.SWITCHABLE]):
                    winners.append(set())
                else:
                    best = min(r["awt"] for r in group)
                    winners.append({r["combo"] for r in group if r["awt"] <= best+1.})
            ranks.append(dict(profile=profile, coarse=coarse, fine=fine,
                              coarse_practical_winners=sorted(winners[0]),
                              fine_practical_winners=sorted(winners[1]),
                              passed=bool(winners[0] & winners[1])))
    finest_coarse, finest = steps[-2:]
    last_checks = [r for r in comparisons+ranks if r["coarse"] == finest_coarse and r["fine"] == finest]
    return dict(
        kind="three_seed_numerical_screen_not_statistical_or_threshold_validation",
        passed=not failures and all(r["passed"] for r in last_checks),
        expected_cases=len(expected), completed_cases=len(rows)-len(failures), failures=failures,
        comparisons=comparisons, ranking_checks=ranks, means=averages,
        proposed_step=finest_coarse if not failures and all(r["passed"] for r in last_checks) else None)


def main(output, workers):
    if not 1 <= workers <= 4:
        raise ValueError("Use 1-4 workers")
    if output.exists():
        raise FileExistsError(f"Refusing to overwrite evidence: {output}")
    (output / "passengers").mkdir(parents=True)
    (output / "sources").mkdir()
    sources = {}
    for name in SOURCES:
        content = (ROOT / name).read_bytes()
        (output / "sources" / name).write_bytes(content)
        sources[name] = hashlib.sha256(content).hexdigest()
    timetable = Path(E.TIMETABLE).read_bytes()
    (output / "sources" / "timetable_synthetic.csv").write_bytes(timetable)
    cases = [(profile, combo, seed, dt, output)
             for profile in PROFILES for combo in E.MAP_COMBOS for seed in SEEDS for dt in STEPS]
    inputs = []
    for profile in PROFILES:
        for seed in SEEDS:
            records, end, evaluate_from = demand(profile, seed)
            inputs.append(dict(profile=profile, seed=seed, input_hash=digest(records),
                               n=len(records), duration=end, evaluate_from=evaluate_from))
    protocol = dict(
        status="running", kind="pre_E0_numerical_screen", seeds=SEEDS, steps=STEPS,
        combos=E.MAP_COMBOS, profiles=PROFILES, expected_cases=len(cases), sources=sources,
        timetable_sha256=hashlib.sha256(timetable).hexdigest(), inputs=inputs,
        class_multiplier=E.CLASS_DEMAND_MULTIPLIER, lunch_multiplier=E.LUNCH_DEMAND_MULTIPLIER,
        scenarios={key: asdict(value) for key, value in E.SCENARIOS.items()}, workers=workers,
        criteria=dict(
            awt="absolute paired mean change <= max(1 second, 5% of fine-step mean)",
            p90="absolute paired mean change <= max(3 seconds, 10% of fine-step mean)",
            ranking="Within-1-second winner sets among C1/C2/C3/C4/C7 must overlap",
            required="All profiles/strategies pass the finest adjacent pair; every trajectory drains",
            limitation="Exploratory screen only; not enough to certify subsecond switching gains"),
        lunch_overlap="12:15-13:15 generated from the new full day; first 30 minutes warm up; score arrivals 12:45-13:15",
        drain_time=C.DRAIN_TIME)
    protocol_path = output / "protocol.json"
    protocol_path.write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    rows = []
    with (output / "metrics.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = None
        with ProcessPoolExecutor(max_workers=workers) as pool:
            for row in pool.map(run_case, cases):
                if writer is None:
                    writer = csv.DictWriter(stream, fieldnames=list(row))
                    writer.writeheader()
                writer.writerow(row)
                stream.flush()
                rows.append(row)
                if len(rows) % 9 == 0 or row["status"] != "completed":
                    print(f"Completed {len(rows)}/{len(cases)}; {row['profile']}/{row['combo']} "
                          f"DT={row['dt']}: {row['status']}", flush=True)
    for name, sha in sources.items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha:
            raise RuntimeError(f"Source changed while numerical screening was running: {name}")
    report = analyze(rows)
    (output / "screen.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    protocol.update(status="completed", completed_cases=len(rows), gate_passed=report["passed"],
                    metrics_sha256=hashlib.sha256((output / "metrics.csv").read_bytes()).hexdigest())
    protocol_path.write_text(json.dumps(protocol, indent=2), encoding="utf-8")
    print(f"NUMERICAL SCREEN {'PASS' if report['passed'] else 'FAIL'}; saved {len(rows)} cases to {output}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "results" / "two_independent_banks" / "step_gate_2p5_v1")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    main(args.output.resolve(), args.workers)
