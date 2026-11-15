"""Source-frozen, resumable coverage of original E1-E3 plus active-policy sensitivity."""
import argparse
import bisect
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from functools import lru_cache
import gzip
import hashlib
import json
from pathlib import Path
from statistics import mean
import time
import traceback

import numpy as np
import pandas as pd

import config as C
import experiments as E
from banking import DualBankSim
from controllers import FEATURES, ForecastMapSwitch, LabelSwitch, MapSwitch, Static, features
from metrics import mean_ci, paired_test, summarize
from revalidate_switching import FrozenPolicy, SOURCES, digest, with_error
from strategies import NoParking, make_combos
from traffic import Passenger, Scenario, generate_day, generate_scenario


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "results" / "two_independent_banks" / "complete_original_2p5_v1"
SOURCE_NAMES = (*SOURCES, "complete_experiments.py")
SEED_STARTS = dict(map=936000, development=937000, confirmation=938000,
                   outage=939000, robustness=940000, forecast=941000)
ERRORS = ("rate_low", "rate_high", "entrance_low", "entrance_high", "lag60", "lag120")
ABLATIONS = ("parking_off", "bypass_off", "reassignment_off")


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False),
                         encoding="utf-8")
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def case(input_spec, controller, broken=()):
    return json.loads(json.dumps(dict(input=input_spec, controller=controller, broken=sorted(broken)),
                                 sort_keys=True))


def fixed(combo):
    return dict(kind="fixed", combo=combo)


def day(seed, attendance=.8, thru=.3, duration=E.DAY_LEN):
    return dict(kind="day", seed=seed, attendance=attendance, thru=thru, duration=duration)


@lru_cache(maxsize=128)
def demand(serialized):
    spec = json.loads(serialized)
    if spec["kind"] == "scenario":
        sc = Scenario(**spec["scenario"])
        passengers, _ = generate_scenario(sc, spec["seed"])
        duration = sc.duration
    elif spec["kind"] == "day":
        duration = spec["duration"]
        passengers = generate_day(
            E.load_timetable(E.TIMETABLE), spec["seed"], spec["attendance"], spec["thru"], duration,
            class_multiplier=E.CLASS_DEMAND_MULTIPLIER, lunch_multiplier=E.LUNCH_DEMAND_MULTIPLIER)
    else:
        raise ValueError(f"Unknown input: {spec['kind']}")
    return tuple((p.id, p.t, p.o, p.d, p.group) for p in passengers), duration


@lru_cache(maxsize=4)
def forecast_records(duration, seeds):
    records = []
    for seed in seeds:
        stream, _ = demand(json.dumps(day(seed, duration=duration), sort_keys=True))
        records.extend(stream)
    return tuple(records)


@lru_cache(maxsize=4)
def forecast_window_data(duration, seeds):
    stream = sorted(forecast_records(duration, seeds), key=lambda p: p[1])
    return stream, [p[1] for p in stream]


def expected_features(input_spec, time_point, mode, error, forecast_seeds):
    delay = int(error[3:]) if error.startswith("lag") else 0
    return _expected_features(json.dumps(input_spec, sort_keys=True), time_point, mode, delay,
                              tuple(forecast_seeds))


@lru_cache(maxsize=32768)
def _expected_features(serialized, time_point, mode, delay, forecast_seeds):
    input_spec = json.loads(serialized)
    observed = time_point-delay
    if mode == "forecast":
        stream, times = forecast_window_data(input_spec["duration"], forecast_seeds)
        lo, hi = bisect.bisect_left(times, observed-60), bisect.bisect_right(times, observed+240)
        seconds = 300*len(forecast_seeds)
    else:
        stream, _ = demand(json.dumps(input_spec, sort_keys=True))
        times = [p[1] for p in stream]
        lo = bisect.bisect_left(times, observed-300)
        hi = bisect.bisect_right(times, observed) if delay else bisect.bisect_left(times, time_point)
        seconds = min(300., max(observed, 1.))
    return features([Passenger(*p) for p in stream[lo:hi]], seconds)


class TracedPolicy:
    def __init__(self, tree, error):
        self.policy = FrozenPolicy(tree)
        self.error = error if not error.startswith("lag") else "none"
        self.trace = []
        self.time = 0.

    def predict(self, feat):
        perturbed = with_error(feat, self.error)
        chosen = self.policy.predict(perturbed)
        self.trace.append((self.time, dict(feat), perturbed, chosen, self.policy.predict(feat)))
        return chosen


class DelayedMap(MapSwitch):
    def __init__(self, combos, policy, delay):
        super().__init__(combos, policy)
        self.delay = delay

    def update(self, sim, t):
        times = [p.t for p in sim.pax] if not hasattr(sim, "_arrival_times") else sim._arrival_times
        sim._arrival_times = times
        observed_time = t-self.delay
        lo = bisect.bisect_left(times, observed_time-self.window)
        hi = min(sim.i_next, bisect.bisect_right(times, observed_time))
        return self.combos[self.choose(features(sim.pax[lo:hi], min(self.window, max(observed_time, 1.))))]


class TracedController:
    def __init__(self, controller, policy, forecast_delay=0):
        self.controller, self.policy, self.delay = controller, policy, forecast_delay
        self.interval = controller.interval

    def update(self, sim, t):
        self.policy.time = t
        return self.controller.update(sim, t-self.delay)


def build_controller(spec, combos, timetable, duration, forecast_seeds):
    ablation = spec.get("ablation", "none")
    for combo in combos.values():
        if ablation == "parking_off":
            combo.parking = NoParking()
        elif ablation == "bypass_off":
            combo.bypass = False
        elif ablation == "reassignment_off":
            combo.dispatcher.reassign = False
        elif ablation != "none":
            raise ValueError(f"Unknown ablation: {ablation}")
    if spec["kind"] == "fixed":
        return Static(combos[spec["combo"]]), None
    if spec["kind"] == "label":
        return LabelSwitch(combos, timetable,
                           {"in": "C7", "out": "C6", "mix": "C1", "lunch": "C1", "normal": "C1"}), None
    if spec["kind"] not in ("realtime", "forecast"):
        raise ValueError(f"Unknown controller: {spec['kind']}")
    error = spec.get("error", "none")
    if error not in ("none", *ERRORS):
        raise ValueError(f"Unknown error: {error}")
    delay = int(error[3:]) if error.startswith("lag") else 0
    policy = TracedPolicy(spec["tree"], error)
    if spec["kind"] == "forecast":
        fc = [Passenger(*record) for record in forecast_records(duration, tuple(forecast_seeds))]
        controller = ForecastMapSwitch(combos, policy, fc, len(forecast_seeds))
        return TracedController(controller, policy, delay), policy
    controller = DelayedMap(combos, policy, delay) if delay else MapSwitch(combos, policy)
    return TracedController(controller, policy), policy


def run_case(arguments):
    specification, output, fingerprint, settings = arguments
    identity = digest(specification)
    path = output / "cases" / f"{identity}.json"
    raw_path = output / "passengers" / f"{identity}.json.gz"
    if path.exists():
        cached = load(path)
        if (cached["case"] != specification or cached["fingerprint"] != fingerprint
                or cached["status"] != "completed" or sha(raw_path) != cached["raw_sha256"]):
            raise RuntimeError(f"Invalid cached case: {path}")
        return cached
    started = time.monotonic()
    C.DT, C.DRAIN_TIME = settings["dt"], settings["drain_seconds"]
    records, duration = demand(json.dumps(specification["input"], sort_keys=True))
    passengers = [Passenger(*record) for record in records]
    timetable = E.load_timetable(E.TIMETABLE)
    combos = make_combos([(c["end"], c["floor"], c["size"]*.8*E.CLASS_DEMAND_MULTIPLIER)
                          for c in timetable])
    controller, policy = build_controller(
        specification["controller"], combos, timetable, duration, settings["forecast_seeds"])
    sim = DualBankSim(passengers, controller, duration, broken_cars=specification["broken"]).run()
    incomplete = int(sum(p.t_board is None or p.t_alight is None or p.t_alight > sim.t+1e-8 for p in passengers))
    bad_times = int(sum(p.t_board is not None and
                    (p.t_board < p.t or (p.t_alight is not None and p.t_alight <= p.t_board))
                    for p in passengers))
    result = dict(case_id=identity, case=specification, fingerprint=fingerprint,
                  input_hash=digest(records), input_n=len(records), end_time=sim.t,
                  incomplete=incomplete, bad_times=bad_times, status="completed")
    if incomplete or bad_times or sim.calls or sim.waiting or sim._sessions:
        result.update(status="invalid_or_undrained", residual_calls=len(sim.calls))
        save(path, result)
        raise RuntimeError(f"Undrained/invalid trajectory {identity}: {incomplete} incomplete, {bad_times} bad times")
    result["metrics"] = summarize(sim)
    result["features"] = features(passengers, duration)
    result["intervals"] = [list(row) for row in intervals(sim.combo_log, duration)]
    result["switches"] = max(0, len(result["intervals"])-1)
    result["lunch"] = summarize(sim, t_from=4*3600, t_to=min(6*3600, duration))
    windows = [(c["start"]-600, c["start"]+120) for c in timetable]
    windows += [(c["end"], c["end"]+360) for c in timetable]
    result["peak"] = summarize(sim, keep=lambda p: any(a <= p.t < b for a, b in windows))
    result["out_of_training_rate_decisions"] = sum(row[1]["lam5"] > settings["training_rate_max"]
                                                  for row in policy.trace) if policy else 0
    result["error_changed_decisions"] = sum(row[3] != row[4] for row in policy.trace) if policy else 0
    result["decision_count"] = len(policy.trace) if policy else 0
    payload = dict(passengers=[(*record, p.t_board, p.t_alight, p.car)
                               for record, p in zip(records, passengers)],
                   decisions=policy.trace if policy else [])
    temporary = raw_path.with_suffix(".gz.tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
    temporary.replace(raw_path)
    result["raw_sha256"] = sha(raw_path)
    result["elapsed_seconds"] = time.monotonic()-started
    save(path, result)
    return result


def intervals(log, duration):
    result = []
    for index, (start, name) in enumerate(log):
        end = min(log[index+1][0] if index+1 < len(log) else duration, duration)
        if start < end:
            result.append((start, end, name.split()[0]))
    return result


def paired_summary(values, reference):
    if len(values) != len(reference) or len(values) < 2:
        raise ValueError("Paired inference requires at least two aligned days")
    differences = np.array(values)-reference
    if np.all(differences == differences[0]):
        delta, p = float(differences[0]), 1. if differences[0] == 0 else 0.
    else:
        delta, p = paired_test(values, reference)
    _, half = mean_ci(differences)
    return dict(awt=mean(values), delta=float(delta), ci_low=float(delta-half), ci_high=float(delta+half),
                change_pct=float((mean(values)/mean(reference)-1)*100) if mean(reference) else None,
                p=float(p), days=len(values))


def switch_cohort(row, reference, output):
    if row["input_hash"] != reference["input_hash"]:
        raise ValueError("Switch-period passengers are not paired")
    with gzip.open(output / "passengers" / f"{row['case_id']}.json.gz", "rt", encoding="utf-8") as stream:
        selected = json.load(stream)["passengers"]
    with gzip.open(output / "passengers" / f"{reference['case_id']}.json.gz", "rt", encoding="utf-8") as stream:
        base = json.load(stream)["passengers"]
    if [p[:5] for p in selected] != [p[:5] for p in base]:
        raise ValueError("Switch-period raw passenger order differs")
    times = np.array([p[1] for p in selected])
    waits = np.array([p[5]-p[1] for p in selected])
    base_waits = np.array([p[5]-p[1] for p in base])
    output_rows = []
    for combo in sorted({item[2] for item in row["intervals"]}-{"C1"}):
        mask = np.zeros(len(times), dtype=bool)
        for start, end, chosen in row["intervals"]:
            if chosen == combo:
                mask |= (times >= start) & (times < end)
        if mask.any():
            output_rows.append(dict(combo=combo, n=int(mask.sum()), awt=float(waits[mask].mean()),
                                    awt_C1=float(base_waits[mask].mean())))
    return output_rows


class CompleteStudy:
    def __init__(self, output, workers=4, smoke=False):
        if not 1 <= workers <= 4:
            raise ValueError("Use 1-4 simulation workers")
        self.output, self.workers = Path(output).resolve(), workers
        self.settings = dict(points=5 if smoke else 500, seeds=2 if smoke else 10,
                             duration=120 if smoke else E.DAY_LEN, scenario_cap=45 if smoke else None,
                             dt=.25, drain_seconds=21600, training_rate_max=800,
                             extra_high_points=0 if smoke else 48,
                             forecast_seeds=list(range(SEED_STARTS["forecast"], SEED_STARTS["forecast"]+5)),
                             smoke=smoke)
        self.sources = {name: sha(ROOT / name) for name in SOURCE_NAMES}
        timetable_sha = sha(Path(E.TIMETABLE))
        self.fingerprint = digest(dict(sources=self.sources, timetable=timetable_sha, settings=self.settings))
        output = self.output
        output.mkdir(parents=True, exist_ok=True)
        protocol_path = output / "protocol.json"
        if protocol_path.exists():
            self.protocol = load(protocol_path)
            if self.protocol["fingerprint"] != self.fingerprint:
                raise RuntimeError("Existing study uses different sources/settings; use a new output directory")
        else:
            if any(output.iterdir()):
                raise RuntimeError("Refusing to mix an unrecognized existing output directory")
            for name in ("cases", "passengers", "sources", "figures"):
                (output / name).mkdir()
            for name in SOURCE_NAMES:
                (output / "sources" / name).write_bytes((ROOT / name).read_bytes())
            (output / "sources" / "timetable_synthetic.csv").write_bytes(Path(E.TIMETABLE).read_bytes())
            prerequisite = ROOT / "results" / "two_independent_banks"
            gates = {}
            for name, relative in (
                ("numerical", "step_gate_2p5_chronological_v2"),
                ("ordered_study", "switching_revalidation_2p5_v2")):
                path = prerequisite / relative / "validation.json"
                if not smoke and (not path.exists() or load(path)["status"] != "passed"):
                    raise RuntimeError(f"Required prior validation missing: {path}")
                if path.exists():
                    gates[name] = dict(path=str(path), sha256=sha(path))
            e0 = prerequisite / "switching_revalidation_2p5_v2" / "e0_verified"
            if not smoke:
                if load(e0 / "protocol.json")["status"] != "completed":
                    raise RuntimeError("Final E0 must already be completed")
                gates["e0"] = dict(path=str(e0), protocol_sha256=sha(e0 / "protocol.json"))
            self.protocol = dict(
                status="registered", fingerprint=self.fingerprint, settings=self.settings,
                sources=self.sources, timetable_sha256=timetable_sha, seed_starts=SEED_STARTS,
                prerequisites=gates, map_candidates=E.MAP_COMBOS, eligible_map_labels=E.SWITCHABLE,
                conditions=E.CONDITIONS, outage_conditions=[[.8, .3], [1.6, .3]],
                robustness_conditions=[[.8, .3], [1.6, .3], [.8, .5]],
                errors=ERRORS, ablations=ABLATIONS,
                selection="Development only; >=0.25s gain and 97.5% paired CI upper<0 for either of two candidates",
                map_design="Original 500 LHS points (rate 3-320) + six references + 48 high-rate points (320-800)",
                forecast="Five disjoint nominal days at attendance0.8/thru0.3; never actual future passengers",
                inference="Day-paired Student-t; supplemental intervals/p values unadjusted, exploratory",
                drain="Same six-hour post-arrival drain for every controller; incomplete cases cause explicit failure",
                old_holdouts_not_reused=True)
            save(protocol_path, self.protocol)

    def verify_sources(self):
        if any(sha(ROOT / name) != checksum for name, checksum in self.sources.items()):
            raise RuntimeError("Study sources changed during execution")
        if sha(Path(E.TIMETABLE)) != self.protocol["timetable_sha256"]:
            raise RuntimeError("Timetable changed during execution")

    def checkpoint(self, phase, **extra):
        save(self.output / "progress.json", dict(phase=phase, timestamp=time.time(), **extra))

    def execute(self, jobs, phase):
        self.verify_sources()
        unique = {digest(item): item for item in jobs}
        results, pending = {}, []
        for identity, specification in unique.items():
            path = self.output / "cases" / f"{identity}.json"
            if path.exists():
                results[identity] = run_case((specification, self.output, self.fingerprint, self.settings))
            else:
                pending.append(specification)
        total, initial, started = len(unique), len(results), time.monotonic()
        self.checkpoint(phase, completed=initial, total=total, cached=initial)
        print(f"{phase}: {initial}/{total} cached, {len(pending)} to run", flush=True)
        with ProcessPoolExecutor(max_workers=self.workers) as pool:
            futures = {pool.submit(run_case, (item, self.output, self.fingerprint, self.settings)):
                       digest(item) for item in pending}
            try:
                for future in as_completed(futures):
                    result = future.result()
                    results[result["case_id"]] = result
                    completed = len(results)
                    if completed % 10 == 0 or completed == total:
                        elapsed = time.monotonic()-started
                        self.checkpoint(phase, completed=completed, total=total, cached=initial,
                                        elapsed_seconds=elapsed, remaining_estimate_seconds=
                                        elapsed*(total-completed)/max(1, completed-initial))
                        print(f"{phase}: {completed}/{total}, {elapsed:.1f}s since resume", flush=True)
            except Exception:
                for future in futures:
                    future.cancel()
                self.checkpoint(phase, failed=True, completed=len(results), total=total)
                raise
        self.verify_sources()
        return [results[digest(item)] for item in jobs]

    def seeds(self, phase):
        return range(SEED_STARTS[phase], SEED_STARTS[phase]+self.settings["seeds"])

    def stage(self, name):
        path = self.output / f"{name}.json"
        if not path.exists():
            return None
        result = load(path)
        if result["status"] != "completed":
            raise RuntimeError(f"Invalid stage: {name}")
        return result

    def e1(self):
        previous = self.stage("e1")
        if previous is not None:
            return previous
        points = E.e1_points(self.settings["points"], seed=93642)
        high = E.e1_points(self.settings["extra_high_points"], seed=93643) if self.settings["extra_high_points"] else []
        for index, item in enumerate(high[:self.settings["extra_high_points"]]):
            item["point"] = f"H{index:03d}"
            item["sc"] = replace(item["sc"], lam5=320+(item["sc"].lam5-3)/317*480, duration=900)
            points.append(item)
        if self.settings["scenario_cap"]:
            for item in points:
                item["sc"] = replace(item["sc"], duration=self.settings["scenario_cap"])
        manifest = [dict(point=p["point"], alpha=p["alpha"], scenario=asdict(p["sc"])) for p in points]
        save(self.output / "e1_design.json", manifest)
        jobs = [case(dict(kind="scenario", scenario=item["scenario"], seed=seed), fixed(combo))
                for item in manifest for combo in E.MAP_COMBOS for seed in self.seeds("map")]
        results = self.execute(jobs, "E1 full six-strategy parameter map")
        raw = []
        index = 0
        for item in manifest:
            for combo in E.MAP_COMBOS:
                for seed in self.seeds("map"):
                    row = results[index]
                    raw.append(dict(point=item["point"], alpha=item["alpha"], combo=combo, seed=seed,
                                    case_id=row["case_id"], **row["metrics"], **row["features"]))
                    index += 1
        raw = pd.DataFrame(raw)
        raw.to_csv(self.output / "e1_raw.csv", index=False)
        table = pd.DataFrame([dict(point=point, alpha=g.alpha.iloc[0], **E.label_point(g))
                              for point, g in raw.groupby("point", sort=False)])
        table.to_csv(self.output / "e1_points.csv", index=False)
        train = table[table.point.str.startswith(("P", "H"))].reset_index(drop=True)
        old_path = E.TREE_PATH
        E.TREE_PATH = str(self.output / "e1_tree.json")
        try:
            cv, depth, text = E.fit_map(train, rng_seed=93644)
        finally:
            E.TREE_PATH = old_path
        cv.to_csv(self.output / "e1_tree_cv.csv", index=False)
        (self.output / "e1_rules.txt").write_text(text, encoding="utf-8")
        tree = dict(kind="tree", **load(self.output / "e1_tree.json"))
        policy = FrozenPolicy(tree)
        refs = table[~table.point.str.startswith(("P", "H"))].copy()
        refs["map_choice"] = [policy.predict(row) for _, row in refs.iterrows()]
        refs.to_csv(self.output / "e1_reference_points.csv", index=False)
        result = dict(status="completed", points=len(table), training_points=len(train), selected_depth=depth,
                      tree=tree, tree_sha256=sha(self.output / "e1_tree.json"),
                      labels={str(k): int(v) for k, v in train.label.value_counts().items()},
                      mean_winners={str(k): int(v) for k, v in train.best_by_mean.value_counts().items()},
                      cv=cv.to_dict("records"), cases=sorted({row["case_id"] for row in results}))
        save(self.output / "e1.json", result)
        return result

    def inputs(self, phase, conditions):
        return [day(seed, attendance, thru, self.settings["duration"])
                for attendance, thru in conditions for seed in self.seeds(phase)]

    def lookup(self, rows):
        return {row["case_id"]: row for row in rows}

    def compare(self, inputs, named, rows, reference, broken=()):
        records, summaries = self.lookup(rows), []
        for attendance, thru in dict.fromkeys((item["attendance"], item["thru"]) for item in inputs):
            group = [item for item in inputs if item["attendance"] == attendance and item["thru"] == thru]
            for name, spec in named.items():
                selected = [records[digest(case(item, spec, broken))] for item in group]
                base = [records[digest(case(item, named[reference], broken))] for item in group]
                for row, ref in zip(selected, base):
                    if row["input_hash"] != ref["input_hash"]:
                        raise ValueError("Paired controllers received different passengers")
                for cohort in ("metrics", "lunch", "peak"):
                    if any(row[cohort]["n"] == 0 for row in (*selected, *base)):
                        continue
                    summaries.append(dict(
                        controller=name, attendance=attendance, thru=thru, cohort=cohort, reference=reference,
                        **paired_summary([row[cohort]["awt"] for row in selected],
                                         [row[cohort]["awt"] for row in base]),
                        p90=mean(row[cohort]["p90"] for row in selected),
                        lwt=mean(row[cohort]["lwt"] for row in selected),
                        att=mean(row[cohort]["att"] for row in selected),
                        passengers=mean(row[cohort]["n"] for row in selected),
                        switches=mean(row["switches"] for row in selected),
                        decisions=mean(row["decision_count"] for row in selected),
                        error_changed_decisions=mean(row["error_changed_decisions"] for row in selected),
                        outside_rate_training=mean(row["out_of_training_rate_decisions"] for row in selected)))
        return summaries

    def development(self, e1):
        previous = self.stage("frozen_rule")
        if previous is not None:
            return previous
        inputs = self.inputs("development", [(.8, .3)])
        specs = {key: fixed(key) for key in E.MAP_COMBOS}
        specs.update(realtime=dict(kind="realtime", tree=e1["tree"]),
                     forecast=dict(kind="forecast", tree=e1["tree"]))
        rows = self.execute([case(item, spec) for item in inputs for spec in specs.values()],
                            "Independent daily development and freeze")
        summary = self.compare(inputs, specs, rows, "C1")
        means = {row["controller"]: row["awt"] for row in summary if row["cohort"] == "metrics"}
        best_fixed = min(E.MAP_COMBOS, key=lambda key: (means[key], E.MAP_COMBOS.index(key)))
        records = self.lookup(rows)
        tests = {}
        for name in ("realtime", "forecast"):
            deltas = [records[digest(case(item, specs[name]))]["metrics"]["awt"]-
                      records[digest(case(item, specs[best_fixed]))]["metrics"]["awt"] for item in inputs]
            center, half = mean_ci(deltas, level=.975)
            tests[name] = dict(delta=float(center), familywise_ci=[float(center-half), float(center+half)],
                               eligible=bool(center <= -.25 and center+half < 0))
        eligible = [name for name in tests if tests[name]["eligible"]]
        chosen = min(eligible, key=lambda name: (means[name], name)) if eligible else best_fixed
        frozen = dict(status="completed", controller=specs[chosen], chosen=chosen, best_fixed=best_fixed,
                      candidate_adopted=bool(eligible), selection_tests=tests, development_means=means,
                      specs=specs, inputs=inputs, cases=sorted(records),
                      e1_tree_sha256=e1["tree_sha256"], confirmation_started=False,
                      holdout_seed_start=SEED_STARTS["confirmation"], used_holdout_for_selection=False)
        pd.DataFrame(summary).to_csv(self.output / "development_summary.csv", index=False)
        save(self.output / "frozen_rule.json", frozen)
        return frozen

    def controls(self, frozen):
        return dict(**{key: fixed(key) for key in E.MAP_COMBOS},
                    label=dict(kind="label"), realtime=frozen["specs"]["realtime"],
                    forecast=frozen["specs"]["forecast"], frozen=frozen["controller"])

    def daily_stage(self, name, inputs, specs, frozen, broken=()):
        previous = self.stage(name)
        if previous is not None:
            if previous["frozen_sha256"] != sha(self.output / "frozen_rule.json"):
                raise RuntimeError("Freeze changed after holdout")
            return previous
        frozen_sha = sha(self.output / "frozen_rule.json")
        rows = self.execute([case(item, spec, broken) for item in inputs for spec in specs.values()], name)
        if frozen_sha != sha(self.output / "frozen_rule.json"):
            raise RuntimeError("Rule changed during holdout")
        summaries = []
        for reference in dict.fromkeys(("C1", frozen["best_fixed"])):
            summaries.extend(self.compare(inputs, specs, rows, reference, broken))
        pd.DataFrame(summaries).to_csv(self.output / f"{name}_summary.csv", index=False)
        pd.DataFrame([dict(case_id=row["case_id"], **row["case"]["input"], **row["metrics"],
                           controller=digest(row["case"]["controller"]), switches=row["switches"])
                      for row in rows]).to_csv(self.output / f"{name}_raw.csv", index=False)
        records, switch_rows = self.lookup(rows), []
        for item in inputs:
            base = records[digest(case(item, specs["C1"], broken))]
            for key, spec in specs.items():
                if spec["kind"] in ("realtime", "forecast", "label"):
                    row = records[digest(case(item, spec, broken))]
                    for cohort in switch_cohort(row, base, self.output):
                        switch_rows.append(dict(controller=key, **item, **cohort))
        pd.DataFrame(switch_rows, columns=["controller", "kind", "seed", "attendance", "thru", "duration",
                                          "combo", "n", "awt", "awt_C1"]).to_csv(
                                              self.output / f"{name}_switch_periods_raw.csv", index=False)
        grouped = []
        if switch_rows:
            for keys, group in pd.DataFrame(switch_rows).groupby(["controller", "attendance", "thru", "combo"]):
                if len(group) < 2:
                    continue
                grouped.append(dict(zip(("controller", "attendance", "thru", "combo"), keys),
                                    **paired_summary(group.awt.tolist(), group.awt_C1.tolist()),
                                    passengers=group.n.mean()))
        pd.DataFrame(grouped).to_csv(self.output / f"{name}_switch_periods.csv", index=False)
        result = dict(status="completed", inputs=inputs, specs=specs, broken=list(broken),
                      cases=sorted({row["case_id"] for row in rows}), frozen_sha256=frozen_sha,
                      summaries=summaries, used_for_selection=False)
        save(self.output / f"{name}.json", result)
        return result

    def robustness(self, frozen):
        previous = self.stage("robustness")
        if previous is not None and "decision_audit" in previous:
            return previous
        inputs = self.inputs("robustness", [(.8, .3), (1.6, .3), (.8, .5)])
        specs = self.controls(frozen)
        specs = {name: specs[name] for name in ("C1", frozen["best_fixed"], "realtime", "forecast", "frozen")}
        for name in ("realtime", "forecast"):
            for error in ERRORS:
                specs[f"{name}_{error}"] = dict(specs[name], error=error)
        for name in ("C1", frozen["best_fixed"], "realtime", "forecast", "frozen"):
            for ablation in ABLATIONS:
                specs[f"{name}_{ablation}"] = dict(specs[name], ablation=ablation)
        specs["switching_off"] = fixed(frozen["best_fixed"])
        specs["forecast_off"] = frozen["specs"]["realtime"]
        result = self.daily_stage("robustness", inputs, specs, frozen)
        rows = [load(self.output / "cases" / f"{identity}.json") for identity in result["cases"]]
        sensitivity = []
        for base in dict.fromkeys(("C1", frozen["best_fixed"], "realtime", "forecast", "frozen")):
            names = {name: spec for name, spec in specs.items() if name == base or name.startswith(base+"_")}
            if base == "frozen":
                names["switching_off"] = specs["switching_off"]
            if base == "forecast":
                names["forecast_off"] = specs["forecast_off"]
            sensitivity.extend(self.compare(inputs, names, rows, base))
        pd.DataFrame(sensitivity).to_csv(self.output / "robustness_sensitivity.csv", index=False)
        audit = []
        for item in inputs:
            for mode in ("realtime", "forecast"):
                nominal = case(item, specs[mode])
                with gzip.open(self.output / "passengers" / f"{digest(nominal)}.json.gz", "rt", encoding="utf-8") as stream:
                    clean = json.load(stream)["decisions"]
                clean_choices = {row[0]: row[3] for row in clean}
                for error in ERRORS:
                    perturbed = case(item, specs[f"{mode}_{error}"])
                    with gzip.open(self.output / "passengers" / f"{digest(perturbed)}.json.gz", "rt", encoding="utf-8") as stream:
                        trace = json.load(stream)["decisions"]
                    overlap = [row for row in trace if row[0] in clean_choices and row[0] < item["duration"]]
                    audit.append(dict(seed=item["seed"], attendance=item["attendance"], thru=item["thru"],
                                      controller=mode, error=error, decisions=len(overlap),
                                      changed=sum(row[3] != clean_choices[row[0]] for row in overlap),
                                      tree_constant=all(node["f"] < 0 for node in specs[mode]["tree"]["nodes"])))
        pd.DataFrame(audit).to_csv(self.output / "estimation_decision_audit.csv", index=False)
        result.update(sensitivity=sensitivity, decision_audit=audit,
                      warning="Constant maps/unused feature perturbations may produce exact no-ops; inspect decision audit.")
        save(self.output / "robustness.json", result)
        return result

    def validate(self):
        self.verify_sources()
        records = {}
        loaded = [load(path) for path in (self.output / "cases").glob("*.json")]
        loaded.sort(key=lambda row: digest(row["case"]["input"]))
        for row in loaded:
            if row["status"] != "completed" or row["fingerprint"] != self.fingerprint:
                raise AssertionError(f"Invalid case: {row['case_id']}")
            if digest(row["case"]) != row["case_id"]:
                raise AssertionError("Wrong case identity")
            raw_path = self.output / "passengers" / f"{row['case_id']}.json.gz"
            if sha(raw_path) != row["raw_sha256"]:
                raise AssertionError("Raw passenger/decision hash changed")
            with gzip.open(raw_path, "rt", encoding="utf-8") as stream:
                raw = json.load(stream)
            passengers = raw["passengers"]
            original, duration = demand(json.dumps(row["case"]["input"], sort_keys=True))
            if digest(original) != row["input_hash"] or digest([p[:5] for p in passengers]) != row["input_hash"]:
                raise AssertionError("Seed cannot reproduce original passengers")
            if len(passengers) != row["input_n"] or any(
                    p[5] is None or p[6] is None or not p[1] <= p[5] < p[6] <= row["end_time"]+1e-8
                    or p[7] in row["case"]["broken"] for p in passengers):
                raise AssertionError("Invalid passenger chronology/car/outage")
            timetable = E.load_timetable(E.TIMETABLE)
            windows = [(c["start"]-600, c["start"]+120) for c in timetable]
            windows += [(c["end"], c["end"]+360) for c in timetable]
            for cohort, predicate in (
                ("metrics", lambda p: 0 <= p[1] < duration),
                ("lunch", lambda p: 4*3600 <= p[1] < min(6*3600, duration)),
                ("peak", lambda p: 0 <= p[1] < duration and any(a <= p[1] < b for a, b in windows))):
                people = [p for p in passengers if predicate(p)]
                if len(people) != row[cohort]["n"]:
                    raise AssertionError("Wrong cohort size")
                if not people:
                    continue
                waits = np.array([p[5]-p[1] for p in people])
                expected = dict(awt=float(waits.mean()), p90=float(np.percentile(waits, 90)),
                                att=mean(p[6]-p[1] for p in people), lwt=float((waits > C.LONG_WAIT).mean()*100))
                if any(abs(value-row[cohort][key]) > 1e-9 for key, value in expected.items()):
                    raise AssertionError("Cannot recompute passenger metrics")
            if row["features"] != features([Passenger(*p[:5]) for p in passengers], duration):
                raise AssertionError("Input features differ")
            for timestamp, feat, perturbed, chosen, clean in raw["decisions"]:
                spec = row["case"]["controller"]
                error = spec.get("error", "none")
                measured = expected_features(row["case"]["input"], timestamp, spec["kind"], error,
                                             self.settings["forecast_seeds"])
                if feat != measured:
                    raise AssertionError("Decision used incorrect/future observed or forecast passengers")
                expected = with_error(feat, "none" if error.startswith("lag") else error)
                policy = FrozenPolicy(spec["tree"])
                if perturbed != expected or chosen != policy.predict(perturbed) or clean != policy.predict(feat):
                    raise AssertionError("Decision trace differs from frozen map/error")
            records[row["case_id"]] = row
        e1, frozen = self.stage("e1"), self.stage("frozen_rule")
        expected_points = self.settings["points"]+6+self.settings["extra_high_points"]
        if (e1["points"] != expected_points
                or len(e1["cases"]) != expected_points*len(E.MAP_COMBOS)*self.settings["seeds"]):
            raise AssertionError("E1 does not cover every preregistered point/strategy/seed")
        table = pd.read_csv(self.output / "e1_points.csv")
        raw = pd.read_csv(self.output / "e1_raw.csv")
        for point, group in raw.groupby("point", sort=False):
            values = E.label_point(group)
            reference = table[table.point == point].iloc[0]
            if values["label"] != reference["label"]:
                raise AssertionError("E1 significance label differs")
            for combo in E.MAP_COMBOS:
                if abs(values[f"awt_{combo}"]-reference[f"awt_{combo}"]) > 1e-9:
                    raise AssertionError("E1 point costs differ")
            for _, item in group.iterrows():
                if abs(records[item.case_id]["metrics"]["awt"]-item.awt) > 1e-9:
                    raise AssertionError("E1 raw CSV differs from passenger evidence")
        if e1["tree_sha256"] != sha(self.output / "e1_tree.json") or frozen["e1_tree_sha256"] != e1["tree_sha256"]:
            raise AssertionError("Map changed after development")
        dev_rows = [records[key] for key in frozen["cases"]]
        dev = self.compare(frozen["inputs"], frozen["specs"], dev_rows, "C1")
        means = {row["controller"]: row["awt"] for row in dev if row["cohort"] == "metrics"}
        if means != frozen["development_means"]:
            raise AssertionError("Development baseline cannot be recomputed")
        best = min(E.MAP_COMBOS, key=lambda key: (means[key], E.MAP_COMBOS.index(key)))
        if best != frozen["best_fixed"]:
            raise AssertionError("Wrong fixed baseline")
        tests = {}
        for name in ("realtime", "forecast"):
            deltas = [records[digest(case(item, frozen["specs"][name]))]["metrics"]["awt"]-
                      records[digest(case(item, fixed(best)))]["metrics"]["awt"] for item in frozen["inputs"]]
            center, half = mean_ci(deltas, level=.975)
            tests[name] = dict(delta=float(center), familywise_ci=[float(center-half), float(center+half)],
                               eligible=bool(center <= -.25 and center+half < 0))
        eligible = [name for name in tests if tests[name]["eligible"]]
        chosen = min(eligible, key=lambda name: (means[name], name)) if eligible else best
        if (tests != frozen["selection_tests"] or chosen != frozen["chosen"]
                or frozen["controller"] != frozen["specs"][chosen]
                or frozen["candidate_adopted"] != bool(eligible)):
            raise AssertionError("Frozen adoption criteria cannot be recomputed")
        groups = [set(range(start, start+self.settings["seeds"])) for name, start in SEED_STARTS.items()
                  if name != "forecast"]
        groups.append(set(self.settings["forecast_seeds"]))
        if any(left & right for index, left in enumerate(groups) for right in groups[index+1:]):
            raise AssertionError("Seed groups overlap")
        covered = set(e1["cases"]) | set(frozen["cases"])
        for name in ("e2", "e3", "robustness"):
            stage = self.stage(name)
            if stage["frozen_sha256"] != sha(self.output / "frozen_rule.json"):
                raise AssertionError("Holdout map/freeze changed")
            phase, conditions = {
                "e2": ("confirmation", E.CONDITIONS),
                "e3": ("outage", [(.8, .3), (1.6, .3)]),
                "robustness": ("robustness", [(.8, .3), (1.6, .3), (.8, .5)]),
            }[name]
            if stage["inputs"] != self.inputs(phase, conditions):
                raise AssertionError("Missing or altered original conditions/seeds")
            if name in ("e2", "e3") and stage["specs"] != self.controls(frozen):
                raise AssertionError("Original control families are not all covered")
            if stage["broken"] != ([0] if name == "e3" else []):
                raise AssertionError("Wrong outage treatment")
            covered.update(stage["cases"])
            rows = [records[key] for key in stage["cases"]]
            recomputed = []
            for reference in dict.fromkeys(("C1", best)):
                recomputed.extend(self.compare(stage["inputs"], stage["specs"], rows, reference, stage["broken"]))
            if recomputed != stage["summaries"]:
                raise AssertionError(f"Holdout statistics differ: {name}")
            if name == "robustness":
                sensitivity = []
                for base in dict.fromkeys(("C1", best, "realtime", "forecast", "frozen")):
                    named = {key: spec for key, spec in stage["specs"].items()
                             if key == base or key.startswith(base+"_")}
                    if base == "frozen":
                        named["switching_off"] = stage["specs"]["switching_off"]
                    if base == "forecast":
                        named["forecast_off"] = stage["specs"]["forecast_off"]
                    sensitivity.extend(self.compare(stage["inputs"], named, rows, base))
                if sensitivity != stage["sensitivity"]:
                    raise AssertionError("Direct error/ablation statistics differ")
        if covered != set(records):
            raise AssertionError("Missing or orphan study trajectories")
        for name, checksum in self.sources.items():
            if sha(self.output / "sources" / name) != checksum:
                raise AssertionError("Frozen snapshot changed")
        result = dict(status="passed", unique_cases=len(records), raw_metrics_recomputed=True,
                      seed_streams_regenerated=True, decision_traces_recomputed=True,
                      observed_and_forecast_windows_recomputed=True, frozen_adoption_recomputed=True,
                      holdout_summaries_recomputed=True, sensitivity_statistics_recomputed=True, disjoint_seeds=True,
                      smoke=self.settings["smoke"])
        save(self.output / "validation.json", result)
        return result

    def report(self, frozen, validation):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        points = pd.read_csv(self.output / "e1_points.csv")
        fig, ax = plt.subplots(figsize=(8, 5))
        colors = {key: index for index, key in enumerate(E.SWITCHABLE)}
        for key in E.SWITCHABLE:
            group = points[points.label == key]
            if not group.empty:
                ax.scatter(group.lam5, group.s_from_ent, label=key, alpha=.7, s=22,
                           color=plt.get_cmap("tab10")(colors[key]))
        ax.set(xlabel="Observed passengers / 5 min", ylabel="Share originating at entrances")
        ax.legend()
        fig.tight_layout()
        fig.savefig(self.output / "figures" / "e1_strategy_map.png", dpi=160)
        plt.close(fig)
        for name in ("e2", "e3"):
            table = pd.read_csv(self.output / f"{name}_summary.csv")
            table = table[(table.cohort == "metrics") & (table.reference == frozen["best_fixed"])]
            labels = list(dict.fromkeys(table.controller))
            fig, ax = plt.subplots(figsize=(11, 5))
            for key in labels:
                group = table[table.controller == key]
                ax.plot(range(len(group)), group.awt, marker="o", label=key)
            conditions = list(dict.fromkeys(zip(table.attendance, table.thru)))
            ax.set_xticks(range(len(conditions)), [f"{a:g}/{t:g}" for a, t in conditions])
            ax.set(xlabel="Attendance / through share", ylabel="Whole-day AWT (s)")
            ax.legend(ncol=3, fontsize=8)
            fig.tight_layout()
            fig.savefig(self.output / "figures" / f"{name}_full_day.png", dpi=160)
            plt.close(fig)
        lines = ["# 新人流与独立双梯组：原实验完整覆盖及扩展", "",
                 "**小规模流程自检，不是研究结果。**" if self.settings["smoke"] else
                 "**本目录为独立新增研究，不覆盖或改写上一轮冻结C4结果。**", "",
                 "## 范围与口径", "",
                 "E0沿用同一源码已通过的最终校验；E1覆盖原500点、六代表场景、六策略、每点10种子，"
                 "另加48个320–800名义人/5分钟高负荷点。E2覆盖原七条件和全部五类原控制，"
                 "另纳入全部六固定策略及开发阶段冻结方案。E3覆盖出席率0.8/1.6、一台车0全程停运；"
                 "保持两独立梯组为2+3台，不重新训练地图。",
                 "每组人流使用相同真实乘客。课表及午餐人数按已确定的2.5基准，"
                 "预测始终按出席0.8/过路0.3，五个预测种子独立于所有真实日。",
                 "六小时额外排空上限对所有策略相同；任何未排空轨迹显式失败，不把删失当完整AWT。",
                 "AWT为到达至上梯，ATT为到达至下梯，>60秒为长等；按到达半开区间统计。", "",
                 "## 开发与冻结", "",
                 f"- 最佳开发固定策略：**{frozen['best_fixed']}**。",
                 f"- 冻结控制：**{frozen['chosen']}**；地图候选通过采用标准：**{frozen['candidate_adopted']}**。",
                 "- E1沿用原C1锚定显著性标签；CV后悔以五个可切换候选的实际最小AWT为基准，"
                 "C0仅作参照。500原范围点和48高范围点均属开发样本，六代表点不参加训练。",
                 "- 实时/预测两个候选只在新开发日比较，采用标准为至少改善0.25秒且97.5%配对区间上界<0；"
                 "此区间对两个候选作Bonferroni保守控制。未通过则冻结最佳固定策略。",
                 "- E2、E3、误差/消融均不用于改选、调阈值或重新训练。", "",
                 "## E2 / E3 全天结果", "",
                 "下表差值为控制器−开发最佳固定策略；95%区间按同种子的日差计算。"
                 "各条件及多个次要比较未作多重校正，属于补充性分析；不能仅凭最小均值宣布赢家。", ""]
        for name in ("e2", "e3"):
            stage = self.stage(name)
            lines += [f"### {name.upper()}", "",
                      "| 出席/过路 | 控制 | AWT秒 | 相对固定基线差值秒 | 95%区间 |", "|---|---|---:|---:|---|"]
            for row in stage["summaries"]:
                if row["cohort"] == "metrics" and row["reference"] == frozen["best_fixed"]:
                    lines.append(f"| {row['attendance']:g}/{row['thru']:g} | {row['controller']} | "
                                 f"{row['awt']:.3f} | {row['delta']:+.3f} | "
                                 f"[{row['ci_low']:+.3f}, {row['ci_high']:+.3f}] |")
            lines += ["", f"![{name}全天对照](figures/{name}_full_day.png)", ""]
        robust = self.stage("robustness")
        audit = robust["decision_audit"]
        lines += ["## 估计误差与消融", "",
                  "误差作用于真实使用人流特征的实时、预测地图，而非只测固定策略："
                  "人数率±20%、入口出发OD权重±20%后重新归一化、信息延迟60/120秒。"
                  "误差只改估计，不改实际乘客；保留逐次干净/扰动特征与决策。",
                  f"在{len(audit)}个日×模式×误差检查中，"
                  f"有{sum(row['changed'] > 0 for row in audit)}个发生了实际决策改变。"
                  "若地图退化或未使用该特征，零差值明确属于无作用情形，不能证明主动切换稳健。",
                  "在名义、出席1.6、过路0.5三个条件下，分别关闭停车、满载直驶、改派；"
                  "检查C1、最佳固定、实时、预测和冻结控制。另比较关闭切换与关闭预测。",
                  "直接组件/误差效应见[配对敏感性表](robustness_sensitivity.csv)，"
                  "是否真的改变决策见[决策审计](estimation_decision_audit.csv)。", "",
                  "## 输出与验证", "",
                  f"- 唯一已排空研究轨迹：{validation['unique_cases']}。",
                  "- [原始逐案例指标](e1_raw.csv)、[逐点策略比较](e1_points.csv)、"
                  "[树深度CV](e1_tree_cv.csv)、[规则](e1_rules.txt)、[冻结选择](frozen_rule.json)。",
                  "- E2/E3分别提供全天/午餐/上下课窗口指标、相对C1及强固定基线配对差值、"
                  "非C1时段的同乘客对照。切换窗口是受所测策略决定的诊断，不取代全天确认。",
                  "- 原始乘客和控制决策在passengers目录；每个案例附输入、源码指纹和压缩文件哈希。",
                  "- [独立重算结果](validation.json)覆盖逐乘客指标、种子再生、故障车排除、"
                  "逐次决策、开发基线和全天配对汇总。",
                  "- 超出训练人数范围的决策数也保存在汇总；树外推不等于验证了该人流处的最优性。",
                  "- 模型内结论，不涉及现实校准；新结果与旧结果之间同时改变了需求、梯组和计时，"
                  "不能把差异全部归因于人流倍增。", "",
                  "![策略地图](figures/e1_strategy_map.png)", "",
                  "```powershell", "python complete_experiments.py --workers 4",
                  "python complete_experiments.py --validate-only", "```"]
        (self.output / "report_zh.md").write_text("\n".join(lines)+"\n", encoding="utf-8")

    def run(self):
        self.protocol["status"] = "running"
        save(self.output / "protocol.json", self.protocol)
        try:
            e1 = self.e1()
            frozen = self.development(e1)
            specs = self.controls(frozen)
            self.daily_stage("e2", self.inputs("confirmation", E.CONDITIONS), specs, frozen)
            self.daily_stage("e3", self.inputs("outage", [(.8, .3), (1.6, .3)]), specs, frozen, broken=(0,))
            self.robustness(frozen)
            validation = self.validate()
            self.report(frozen, validation)
        except Exception as error:
            self.protocol.update(status="failed", failure=str(error), traceback=traceback.format_exc())
            save(self.output / "protocol.json", self.protocol)
            self.checkpoint("failed", error=str(error))
            raise
        self.protocol.update(status="completed", unique_cases=validation["unique_cases"])
        self.protocol.pop("failure", None)
        self.protocol.pop("traceback", None)
        save(self.output / "protocol.json", self.protocol)
        self.checkpoint("completed", unique_cases=validation["unique_cases"])
        print(f"COMPLETED AND VERIFIED: {self.output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--smoke", action="store_true", help="Separate small pipeline check; not research evidence")
    parser.add_argument("--validate-only", action="store_true")
    arguments = parser.parse_args()
    if arguments.smoke and arguments.output == OUTPUT:
        parser.error("--smoke requires a separate --output")
    study = CompleteStudy(arguments.output, arguments.workers, arguments.smoke)
    if arguments.validate_only:
        print(study.validate())
    else:
        study.run()
