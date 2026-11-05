"""Finite, source-locked traffic ranking, same-state branching, and daily validation."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
import copy
import csv
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import time

import numpy as np
from scipy import stats

import config as C
from research.dual_bank.control import BranchControl, DayControl, FixedControl, rule_key
from research.dual_bank.demand import (
    BOUNDARIES, MEASURE, REGIMES, WARMUP, measurement_seconds, sustained,
)
from research.dual_bank.timetable import LEVELS, clock_regime, generate, make_plan

ROOT = Path(__file__).resolve().parents[2]
DEFAULT = ROOT / "results" / "dual_bank_v2_2026-11-04"


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                    encoding="utf-8")


def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode("utf-8")).hexdigest()


def request_record(p):
    return dict(id=p.id, t=p.t, o=p.o, d=p.d, group=p.group)


def read_raw(path):
    with gzip.open(path, "rt", encoding="utf-8") as stream:
        return json.load(stream)


def write_raw(path, value):
    path = Path(path)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+".tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    temporary.replace(path)


def protocol():
    from research.dual_bank.engine import configurations
    return dict(
        version=2, created_at=datetime.now(timezone.utc).isoformat(),
        model_revision="Full cars depart without clearing unserved local latches.",
        banks={"0": [0, 1, 2], "1": [3, 4, 5]},
        semantics="Every physical arrival presses both banks. No cross-bank cancellation or "
                  "queue/OD observation. Own opening clears own direction latch; remaining "
                  "passengers re-press. Unique shared physical FIFO queue, car-id tie break.",
        settings=configurations(), levels=list(LEVELS), regimes=list(REGIMES),
        plans={level: make_plan(level) for level in LEVELS},
        development_seeds=list(range(8531001, 8531005)),
        evaluation_seeds=list(range(8551001, 8551009)),
        dt=.5, fine_dt=.25, drain_time=3600., reassign_interval=10.,
        warmup=WARMUP, measure=MEASURE, night_measure=7200., transition_horizon=900.,
        boundaries_minutes=list(BOUNDARIES), practical_seconds=.5,
        p90_ratio=1.05, lwt_increase_pp=1.,
        ranking="Eight symmetric configurations (same recipe in both independent banks; "
                "not all64 asymmetric pairs), sustained uniform OD mixtures "
                "at the daily model's mean rates. Discard first600s; score next1200s arrivals. "
                "S6 scored for7200s to avoid sparse empty windows. "
                "Rank equal-day development AWT, tie by configuration name; freeze top2. "
                "These are finite-candidate episode rankings, not true universal optima.",
        fixed="Re-evaluate all8 on complete development days; select fixed by load and global "
              "equal-load/equal-seed champion.",
        transitions="At12 representative clock boundaries of each development day under load's "
                    "frozen fixed champion AND naive clock champion switching, deepcopy "
                    "identical full state. Keep and change "
                    "one bank at a time to each of target regime's top2 sustained candidates. "
                    "Other bank holds incumbent. Truncate future arrivals at boundary+900s, "
                    "then drain. Score remaining waits of physically waiting plus future "
                    "arrivals (onboard excluded); an empty transition cohort has explicitly "
                    "flagged zero waiting opportunity, not an estimated passenger mean. "
                    "No warm-start reset or future-leaking policy.",
        gate="Group development paired switch-minus-keep by load,target regime,incumbent, "
             "bank-local bin(clear/occupied/busy). Average banks/boundaries within seed first. "
             "Require >=3 seed replicates, mean<=-.5s, upper simultaneous95% CI<0 across "
             "all tested development cells/actions, p90ratio<=1.05 and LWTincrease<=1pp. "
             "Choose lowest mean eligible action, otherwise retain. Bins: clear=no latches "
             "or riders; busy=at least3 latches or16 onboard; occupied=other.",
        daily="Compare frozen fixed_level,fixed_global,naive regime champions,gated local rule "
              "on unseen full24h days. Gated checks only at regime changes, per-bank local "
              "observations, retaining when unsupported. No anticipatory parking/lead tuning.",
        heldout_transitions="From independent heldout fixed-policy snapshots compare keep, "
                            "both-bank naive champion and simultaneous independent local gate. "
                            "Recreate snapshots separately at both resolutions.",
        inference="Daily primary unit8 seed-days after equal averaging3 levels. Three primary "
                  "contrasts versus fixed_level (global,naive,gated), Holm p and Bonferroni "
                  "simultaneous95% CIs per resolution. Verified gain needs at least.5s, "
                  "simultaneous upper<0 and tail guards at BOTH resolutions. Conditional "
                  "ranking contrasts use separate147-test family against frozen champion. "
                  "Transitions average boundaries and levels within seed, separate2-test "
                  "family. All eight heldout configurations may be described, never retuned.",
        expected=dict(sustained_dev=672, fixed_dev=96, transition_dev=1440,
                      sustained_eval=1344, sustained_fine=336,
                      transition_eval=864, transition_fine=864, daily_eval=96,
                      daily_fine=96, snapshot_parents=72, total=5880),
        testing_strategy=dict(
            appType="Pure Python discrete-time simulation; no API, DB, browser or external service.",
            stack="unittest plus full CLI pilots, paired raw-record reconstruction and deterministic replay",
            journeys=["both-bank press and ghost response", "unique boarding and conservation",
                      "bank information isolation", "same-state branching and policy continuity",
                      "selection locks and heldout paired statistics"],
            external_dependencies="Existing numpy/scipy only; temporary directories for test files.",
            infra_tier="N/A: no infrastructure dependency", browser_tier="N/A: no rendered UI",
            gaps="Synthetic demand assumptions; finite candidate family; no physical field validation."),
        sources={},
    )


def freeze(root):
    if (root / "protocol.json").exists():
        raise FileExistsError("Protocol already frozen")
    specification = protocol()
    files = [ROOT / f"{name}.py" for name in ("config", "traffic", "building", "sim")]
    files += sorted((ROOT / "research" / "dual_bank").glob("*.py"))
    for path in files:
        relative = str(path.relative_to(ROOT))
        specification["sources"][relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        destination = root / "frozen_sources" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    save(root / "protocol.json", specification)


def verify(root):
    specification = load(root / "protocol.json")
    manifest_path = root / "source_manifest.json"
    if manifest_path.exists():
        manifest = load(manifest_path)
        if digest(specification) != manifest["protocol_hash"]:
            raise AssertionError("Published protocol changed")
        for relative, expected_hash in manifest["sources"].items():
            if hashlib.sha256((ROOT / relative).read_bytes()).hexdigest() != expected_hash:
                raise AssertionError(f"Packaged source changed: {relative}")
        return specification
    for relative, expected in specification["sources"].items():
        for path in (ROOT / relative, root / "frozen_sources" / relative):
            if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                raise AssertionError(f"Frozen source changed: {path}")
    return specification


def lock(root, name, value):
    path = root / f"{name}.json"
    if path.exists():
        raise FileExistsError(f"Selection already frozen: {name}")
    save(path, value)
    save(root / f"{name}_lock.json", dict(sha256=digest(value),
                                        frozen_at=datetime.now(timezone.utc).isoformat()))


def locked(root, name):
    value = load(root / f"{name}.json")
    if digest(value) != load(root / f"{name}_lock.json")["sha256"]:
        raise AssertionError(f"Selection lock changed: {name}")
    return value


def cohort_metrics(service, identifiers, start=0., allow_empty=False):
    cohort = [p for p in service if p["id"] in identifiers]
    if len(cohort) != len(identifiers):
        raise AssertionError("Missing measurement cohort")
    if not cohort:
        if not allow_empty:
            raise AssertionError("Empty sustained measurement cohort")
        return dict(awt=0., p90=0., lwt=0., n=0, empty_cohort=True)
    if any(p["board"] is None or p["alight"] is None for p in cohort):
        raise AssertionError("Incomplete measurement cohort")
    waits = np.asarray([p["board"]-max(p["arrival"], start) for p in cohort])
    if np.min(waits) < -1e-8:
        raise AssertionError("Negative remaining waiting time")
    return dict(awt=float(waits.mean()), p90=float(np.quantile(waits, .9)),
                lwt=float(np.mean(waits > C.LONG_WAIT)*100), n=len(cohort), empty_cohort=False)


def payload(sim, specification, signature, inputs, metric, extra=None):
    service = sim.service_records()
    if metric["incomplete"] or metric["unserved"]:
        raise AssertionError(f"Incomplete formal simulation: {metric}")
    portable_spec = dict(specification, root=".")
    return dict(signature=signature, spec=portable_spec, input_hash=digest(inputs),
                inputs=inputs, metrics=metric, service=service,
                decisions=sim.decision_log, latches=sim.latch_log, stops=sim.stop_log,
                boardings=sim.board_log,
                control_log=getattr(sim.controller, "log", []), extra=extra or {})


def configure(spec):
    C.DT, C.DRAIN_TIME = spec["dt"], spec["drain_time"]
    C.REASSIGN_INTERVAL = spec["reassign_interval"]


def execute(spec):
    from research.dual_bank.engine import DualBankSim
    path = Path(spec["root"]) / "raw" / spec["phase"] / f"{spec['key']}.json.gz"
    signature = digest({k: v for k, v in spec.items() if k != "root"})
    if path.exists():
        raw = read_raw(path)
        if raw["signature"] != signature:
            raise AssertionError(f"Resume signature mismatch: {path}")
        if raw["metrics"]["incomplete"] or raw["metrics"]["unserved"]:
            raise AssertionError(f"Cannot resume incomplete output: {path}")
        return raw["metrics"]
    configure(spec)
    started = time.perf_counter()
    if spec["kind"] == "sustained":
        passengers, end, generation = sustained(spec["level"], spec["regime"], spec["seed"])
        control = FixedControl(spec["setting"])
    else:
        passengers, end, generation = generate(spec["level"], spec["seed"])
        if spec["policy"] in ("fixed_level", "fixed_global") or spec["kind"] == "fixed_dev":
            control = FixedControl(spec["setting"])
        else:
            control = DayControl(spec["initial"], spec["mapping"], spec["policy"],
                                 spec.get("rules"))
    inputs = [request_record(p) for p in passengers]
    sim = DualBankSim(passengers, control, end).run()
    metric = dict(sim.metrics(), phase=spec["phase"], level=spec["level"], seed=spec["seed"],
                  policy=spec["policy"], setting=spec.get("setting"),
                  regime=spec.get("regime"), dt=C.DT, elapsed=time.perf_counter()-started)
    if spec["kind"] == "sustained":
        identifiers = {p.id for p in passengers
                       if WARMUP <= p.t < WARMUP+measurement_seconds(spec["regime"])}
        metric.update(cohort_metrics(sim.service_records(), identifiers))
    write_raw(path, payload(sim, spec, signature, inputs, metric,
                            dict(generation=generation)))
    return metric


def jobs(root, specification, phase, **fields):
    return dict(root=str(root), phase=phase, sources=digest(specification["sources"]),
                dt=specification["fine_dt"] if phase.endswith("fine") else specification["dt"],
                drain_time=specification["drain_time"],
                reassign_interval=specification["reassign_interval"], **fields)


def run_jobs(worker, work, workers):
    results = []
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(worker, item): item["key"] for item in work}
        for future in as_completed(futures):
            result = future.result()
            results.append(result)
            print(f"completed {len(results)}/{len(work)} {futures[future]}", flush=True)
    return results


def phase_rows(root, phase):
    return [read_raw(path)["metrics"] for path in sorted((root / "raw" / phase).glob("*.json.gz"))]


def develop(root, workers):
    p = verify(root)
    if (root / "ranking.json").exists():
        raise FileExistsError("Development selection already frozen")
    work = []
    for level in p["levels"]:
        for seed in p["development_seeds"]:
            for setting in sorted(p["settings"]):
                for regime in p["regimes"]:
                    key = f"{level}__{regime}__{seed}__{setting}"
                    work.append(jobs(root, p, "sustained_dev", key=key, kind="sustained",
                                     level=level, seed=seed, regime=regime,
                                     setting=setting, policy=setting))
                key = f"{level}__{seed}__{setting}"
                work.append(jobs(root, p, "fixed_dev", key=key, kind="fixed_dev",
                                 level=level, seed=seed, setting=setting, policy=setting))
    run_jobs(execute, work, workers)
    sustained_rows, fixed_rows = phase_rows(root, "sustained_dev"), phase_rows(root, "fixed_dev")
    if (len(sustained_rows), len(fixed_rows)) != (672, 96):
        raise AssertionError("Incomplete development matrix")
    ranking, fixed = {}, {}
    for level in p["levels"]:
        ranking[level] = {}
        for regime in p["regimes"]:
            scores = {name: float(np.mean([r["awt"] for r in sustained_rows
                      if r["level"] == level and r["regime"] == regime and r["policy"] == name]))
                      for name in p["settings"]}
            ranking[level][regime] = sorted(scores, key=lambda name: (scores[name], name))
        scores = {name: float(np.mean([r["awt"] for r in fixed_rows
                  if r["level"] == level and r["policy"] == name])) for name in p["settings"]}
        fixed[level] = min(scores, key=lambda name: (scores[name], name))
    global_scores = {name: float(np.mean([r["awt"] for r in fixed_rows if r["policy"] == name]))
                     for name in p["settings"]}
    lock(root, "ranking", dict(ranking=ranking, fixed=fixed,
                              global_fixed=min(global_scores, key=lambda n: (global_scores[n], n)),
                              development_hash=digest([sustained_rows, fixed_rows])))


def branch(snapshot, choices, cutoff, identifiers, source_hash, spec):
    fork = copy.deepcopy(snapshot)
    before = digest(fork.state_record())
    if before != source_hash:
        raise AssertionError("Fork did not preserve the exact source state")
    fork.controller = BranchControl(choices)
    fork.next_ctrl = fork.t
    fork.pax = [p for p in fork.pax if p.t < cutoff]
    fork.gen_end = cutoff
    fork.run()
    metric = dict(fork.metrics())
    metric.update(cohort_metrics(fork.service_records(), identifiers, snapshot.t, allow_empty=True))
    if metric["incomplete"] or metric["unserved"]:
        raise AssertionError("Incomplete transition branch")
    return fork, metric, before


def transition_day(spec):
    from research.dual_bank.engine import DualBankSim
    configure(spec)
    parent_path = Path(spec["root"]) / "raw" / "snapshot_parents" / f"{spec['key']}.json.gz"
    signature = digest({k: v for k, v in spec.items() if k != "root"})
    phase_dir = Path(spec["root"]) / "raw" / spec["phase"]
    expected = len(BOUNDARIES)*(5 if spec["development"] else 3)
    existing = sorted(phase_dir.glob(f"{spec['key']}__*.json.gz"))
    if len(existing) == expected and parent_path.exists():
        if read_raw(parent_path)["signature"] != signature:
            raise AssertionError("Transition resume parent signature mismatch")
        for path in existing:
            raw = read_raw(path)
            if raw["extra"]["parent_signature"] != signature:
                raise AssertionError("Transition branch resume mismatch")
        return dict(branches=expected, key=spec["key"])
    passengers, end, generation = generate(spec["level"], spec["seed"])
    inputs = [request_record(p) for p in passengers]
    background = spec.get("background", "fixed")
    control = (FixedControl(spec["initial"]) if background == "fixed"
               else DayControl(spec["initial"], {r: v[0] for r, v in spec["ranking"].items()}))
    parent = DualBankSim(passengers, control, end)
    for minute in BOUNDARIES:
        boundary = minute*60.
        while parent.t < boundary-1e-8:
            parent.step()
        if abs(parent.t-boundary) > 1e-8:
            raise AssertionError("Snapshot grid missed the boundary")
        state = parent.state_record()
        source_hash = digest(state)
        regime = clock_regime(boundary)
        cutoff = boundary+spec["horizon"]
        identifiers = {p.id for p in parent.pax
                       if (p.t < boundary and p.t_board is None) or boundary <= p.t < cutoff}
        incumbent = dict(parent.policy_names)
        observations = {bank: parent.observable(bank) for bank in (0, 1)}
        actions = [("keep", incumbent.copy(), None, None)]
        if spec["development"]:
            for bank in (0, 1):
                for rank, candidate in enumerate(spec["ranking"][regime][:2]):
                    choices = incumbent.copy()
                    choices[bank] = candidate
                    actions.append((f"bank{bank}_rank{rank}", choices, bank, candidate))
        else:
            naive = {bank: spec["ranking"][regime][0] for bank in (0, 1)}
            gated = {bank: spec["rules"].get(
                rule_key(regime, incumbent[bank], observations[bank]), incumbent[bank])
                for bank in (0, 1)}
            actions += [("naive", naive, None, None), ("gated", gated, None, None)]
        for action, choices, bank, candidate in actions:
            key = f"{spec['key']}__{minute}__{action}"
            path = phase_dir / f"{key}.json.gz"
            action_signature = digest(dict(parent=signature, minute=minute, action=action,
                                           choices=choices, snapshot=source_hash))
            if path.exists():
                if read_raw(path)["signature"] != action_signature:
                    raise AssertionError("Existing snapshot branch mismatch")
                continue
            fork, metric, before = branch(parent, choices, cutoff, identifiers, source_hash, spec)
            metric.update(phase=spec["phase"], level=spec["level"], seed=spec["seed"],
                          policy=action, regime=regime, dt=C.DT, boundary=minute,
                          bank=bank, candidate=candidate, background=background)
            extra = dict(snapshot=state, snapshot_hash=before, choices=choices,
                         observations=observations, parent_signature=signature,
                         cohort=sorted(identifiers), cohort_start=boundary, cutoff=cutoff)
            write_raw(path, payload(fork, spec, action_signature,
                                    [request_record(p) for p in fork.pax], metric, extra))
        if digest(parent.state_record()) != source_hash:
            raise AssertionError("Branch mutated the parent snapshot")
    parent.run()
    metric = dict(parent.metrics(), phase="snapshot_parents", level=spec["level"],
                  seed=spec["seed"], policy=spec["initial"], dt=C.DT)
    if not parent_path.exists():
        write_raw(parent_path, payload(parent, spec, signature, inputs, metric,
                                       dict(generation=generation)))
    return dict(branches=expected, key=spec["key"])


def transition_work(root, p, ranking, development, fine=False, rules=None):
    phase = "transition_dev" if development else ("transition_fine" if fine else "transition_eval")
    seeds = p["development_seeds"] if development else p["evaluation_seeds"]
    backgrounds = ("fixed", "naive") if development else ("fixed",)
    return [jobs(root, p, phase, key=f"{phase}__{level}__{seed}__{background}", level=level, seed=seed,
                 initial=ranking["fixed"][level], ranking=ranking["ranking"][level],
                 rules=(rules or {}).get(level, {}), horizon=p["transition_horizon"],
                 development=development, ranking_hash=digest(ranking), background=background)
            for level in p["levels"] for seed in seeds for background in backgrounds]


def fit_rules(records):
    grouped, keeps = {}, {}
    for raw in records:
        m = raw["metrics"]
        identity = (m["level"], m["seed"], m["boundary"], m.get("background", "fixed"))
        if m["policy"] == "keep":
            keeps[identity] = m
    for raw in records:
        m = raw["metrics"]
        if m["policy"] == "keep":
            continue
        extra = raw["extra"]
        bank = m["bank"]
        observation = extra["observations"][str(bank)]
        incumbent = extra["snapshot"]["policy_names"][str(bank)]
        key = rule_key(m["regime"], incumbent, observation)
        group = (m["level"], key, m["candidate"])
        keep = keeps[(m["level"], m["seed"], m["boundary"], m.get("background", "fixed"))]
        grouped.setdefault(group, {}).setdefault(m["seed"], []).append(
            (m["awt"]-keep["awt"], m["p90"], keep["p90"], m["lwt"]-keep["lwt"]))
    trials = len(grouped)
    eligible, details = {}, []
    for (level, key, candidate), seeds in sorted(grouped.items()):
        means = np.asarray([np.mean(values, axis=0) for values in seeds.values()])
        differences = means[:, 0]
        n = len(differences)
        mean = float(differences.mean())
        upper = None
        if n >= 3:
            se = float(differences.std(ddof=1)/np.sqrt(n))
            upper = mean+float(stats.t.ppf(1-.05/(2*max(trials, 1)), n-1))*se
        p90ratio = float(means[:, 1].mean()/means[:, 2].mean()) if means[:, 2].mean() else 1.
        lwt = float(means[:, 3].mean())
        passed = n >= 3 and mean <= -.5 and upper < 0 and p90ratio <= 1.05 and lwt <= 1.
        details.append(dict(level=level, key=key, candidate=candidate, seeds=n,
                            mean=mean, simultaneous_upper=upper, p90ratio=p90ratio,
                            lwt_increase_pp=lwt, passed=bool(passed)))
        if passed:
            eligible.setdefault((level, key), []).append((mean, candidate))
    rules = {level: {} for level in LEVELS}
    for (level, key), options in eligible.items():
        rules[level][key] = min(options)[1]
    return dict(rules=rules, trials=trials, details=details,
                note="No supported switch means retain, not a proof of equivalence or optimality.")


def training_record(raw):
    extra = raw["extra"]
    return dict(signature=raw["signature"], metrics=raw["metrics"],
                extra=dict(observations={str(bank): {key: observation[key]
                                                    for key in ("latched", "onboard", "idle")}
                                         for bank, observation in extra["observations"].items()},
                           snapshot=dict(policy_names=extra["snapshot"]["policy_names"])))


def train_transitions(root, workers):
    p, ranking = verify(root), locked(root, "ranking")
    if (root / "switching.json").exists():
        raise FileExistsError("Switching rules already frozen")
    run_jobs(transition_day, transition_work(root, p, ranking, True), workers)
    records = [training_record(read_raw(path)) for path in
               sorted((root / "raw" / "transition_dev").glob("*.json.gz"))]
    if len(records) != 1440:
        raise AssertionError("Incomplete transition development matrix")
    selection = fit_rules(records)
    selection["ranking_hash"] = digest(ranking)
    selection["raw_hash"] = digest([r["signature"] for r in records])
    lock(root, "switching", selection)


def evaluate(root, workers, stage):
    p, ranking, switching = verify(root), locked(root, "ranking"), locked(root, "switching")
    if switching["ranking_hash"] != digest(ranking):
        raise AssertionError("Switching selection references another ranking")
    work = []
    if stage == "conditions":
        for level in p["levels"]:
            for seed in p["evaluation_seeds"]:
                for regime in p["regimes"]:
                    for setting in sorted(p["settings"]):
                        key = f"{level}__{regime}__{seed}__{setting}"
                        work.append(jobs(root, p, "sustained_eval", key=key, kind="sustained",
                                         level=level, seed=seed, regime=regime,
                                         setting=setting, policy=setting, selection=digest(ranking)))
                    for policy, setting in (
                        ("champion", ranking["ranking"][level][regime][0]),
                        ("reference", "pickup_uniform_b0"),
                    ):
                        key = f"{level}__{regime}__{seed}__{policy}"
                        work.append(jobs(root, p, "sustained_fine", key=key, kind="sustained",
                                         level=level, seed=seed, regime=regime,
                                         setting=setting, policy=policy, selection=digest(ranking)))
        run_jobs(execute, work, workers)
    elif stage == "transitions":
        for fine in (False, True):
            run_jobs(transition_day, transition_work(
                root, p, ranking, False, fine, switching["rules"]), workers)
    elif stage == "daily":
        for phase in ("daily_eval", "daily_fine"):
            for level in p["levels"]:
                for seed in p["evaluation_seeds"]:
                    for policy in ("fixed_level", "fixed_global", "naive", "gated"):
                        setting = (ranking["global_fixed"] if policy == "fixed_global"
                                   else ranking["fixed"][level])
                        key = f"{level}__{seed}__{policy}"
                        mapping = {r: names[0] for r, names in ranking["ranking"][level].items()}
                        work.append(jobs(root, p, phase, key=key, kind="daily", level=level,
                                         seed=seed, policy=policy, setting=setting, initial=setting,
                                         mapping=mapping, rules=switching["rules"][level],
                                         selection=digest([ranking, switching])))
        run_jobs(execute, work, workers)
    else:
        raise ValueError("Unknown evaluation stage")


def export_metrics(root):
    rows = []
    for directory in sorted((root / "raw").iterdir()):
        if directory.is_dir():
            rows.extend(phase_rows(root, directory.name))
    keys = sorted({key for row in rows for key in row})
    with (root / "metrics.csv").open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def engineering(root, workers):
    p = protocol()
    engine = ROOT / "research" / "dual_bank" / "engine.py"
    p["sources"] = {str(engine.relative_to(ROOT)): hashlib.sha256(engine.read_bytes()).hexdigest()}
    work = [jobs(root, p, phase, key=f"{level}__{phase}", kind="daily", level=level,
                 seed=8521001, policy="fixed_level", setting="pickup_uniform_b0")
            for level in ("low", "high") for phase in ("pilot", "pilot_fine")]
    results = run_jobs(execute, work, workers)
    save(root / "engineering.json", dict(
        trajectories=4, complete=all(not r["incomplete"] and not r["unserved"] for r in results),
        results=results, engine_sources=p["sources"],
        role="Runtime and conservation only; not candidate selection."))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=("engineering", "freeze", "develop", "train", "conditions",
                                           "transitions", "daily", "metrics"))
    parser.add_argument("--root", type=Path, default=DEFAULT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if not 1 <= args.workers <= 4:
        raise ValueError("Use one to four workers on this shared machine")
    if args.command == "engineering":
        engineering(args.root, args.workers)
    elif args.command == "freeze":
        freeze(args.root)
    elif args.command == "develop":
        develop(args.root, args.workers)
    elif args.command == "train":
        train_transitions(args.root, args.workers)
    elif args.command == "metrics":
        export_metrics(args.root)
    else:
        evaluate(args.root, args.workers, args.command)


if __name__ == "__main__":
    main()
