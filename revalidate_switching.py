"""Paired, staged C1/C7 research with development/confirmation separation."""
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

import numpy as np
from sklearn.model_selection import KFold
from sklearn.tree import DecisionTreeClassifier

import config as C
import experiments as E
from banking import DualBankSim
from controllers import FEATURES, MapSwitch, Static, features
from metrics import mean_ci, summarize
from strategies import NoParking, make_combos
from traffic import Passenger, Scenario, generate_scenario


ROOT = Path(__file__).resolve().parent
OUTPUT = ROOT / "results" / "two_independent_banks" / "switching_revalidation_2p5_v2"
DEV_SEEDS = tuple(range(925101, 925107))
BOUNDARY_SEEDS = tuple(range(925201, 925205))
CONFIRM_SEEDS = tuple(range(925301, 925316))
ROBUST_SEEDS = tuple(range(925401, 925407))
CONDITIONS = ((.64, .3), (.8, .3), (.96, .3), (.8, .1), (.8, .5))
SOURCES = ("revalidate_switching.py", "banking.py", "building.py", "sim.py",
           "traffic.py", "strategies.py", "controllers.py", "metrics.py",
           "experiments.py", "config.py")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def fixed(combo):
    return dict(kind="fixed", combo=combo)


def day(seed, attendance=.8, thru=.3):
    return dict(kind="day", seed=seed, attendance=attendance, thru=thru)


def with_error(feat, error):
    """Perturb rate or OD estimates without modifying actual passengers."""
    result = dict(feat)
    if error in ("rate_low", "rate_high"):
        result["lam5"] *= .8 if error == "rate_low" else 1.2
    elif error in ("entrance_low", "entrance_high"):
        factor = .8 if error == "entrance_low" else 1.2
        inside, outside, thru = feat["s_in"]*factor, feat["s_out"], feat["s_thru"]*factor
        inter = max(0., 1-feat["s_in"]-feat["s_out"]-feat["s_thru"])
        total = inside+outside+thru+inter
        if total:
            result.update(s_in=inside/total, s_out=outside/total, s_thru=thru/total,
                          s_from_ent=(inside+thru)/total, s_to_ent=(outside+thru)/total)
    elif error != "none":
        raise ValueError(f"Unknown estimation error: {error}")
    return result


class FrozenPolicy:
    def __init__(self, spec):
        self.spec = spec

    def predict(self, feat):
        feat = with_error(feat, self.spec.get("error", "none"))
        index = 0
        while self.spec["nodes"][index]["f"] >= 0:
            node = self.spec["nodes"][index]
            name = self.spec["features"][node["f"]]
            index = node["l"] if feat[name] <= node["thr"] else node["r"]
        return self.spec["nodes"][index]["label"]


def canonical(spec):
    spec = dict(spec)
    if spec["kind"] == "tree":
        labels = {node["label"] for node in spec["nodes"] if node["f"] < 0}
        if len(labels) == 1:
            spec = dict(kind="fixed", combo=next(iter(labels)),
                        ablation=spec.get("ablation", "none"))
    if spec["kind"] == "fixed":
        spec.pop("error", None)
    if spec.get("ablation") == "none":
        spec.pop("ablation")
    if spec.get("error") == "none":
        spec.pop("error")
    return spec


@lru_cache(maxsize=256)
def input_records(serialized):
    specification = json.loads(serialized)
    if specification["kind"] == "day":
        passengers = E.generate_day(
            E.load_timetable(E.TIMETABLE), specification["seed"],
            attendance=specification["attendance"], thru_share=specification["thru"])
        end = E.DAY_LEN
    elif specification["kind"] == "scenario":
        sc = Scenario(**specification["scenario"])
        passengers, _ = generate_scenario(sc, specification["seed"])
        end = sc.duration
    else:
        raise ValueError("Unknown demand kind")
    return tuple((p.id, p.t, p.o, p.d, p.group) for p in passengers), end


def job(specification, controller):
    return json.loads(json.dumps(dict(input=specification, controller=canonical(controller)), sort_keys=True))


def run_job(arguments):
    case, output, fingerprint = arguments
    case_id = digest(case)
    path = output / "cases" / f"{case_id}.json"
    if path.exists():
        cached = json.loads(path.read_text(encoding="utf-8"))
        if cached["case"] != case or cached["fingerprint"] != fingerprint or cached["status"] != "completed":
            raise RuntimeError(f"Invalid cached case: {path}")
        return cached
    C.DT = .25
    records, end = input_records(json.dumps(case["input"], sort_keys=True))
    passengers = [Passenger(*record) for record in records]
    timetable = E.load_timetable(E.TIMETABLE)
    forecast = [(c["end"], c["floor"], c["size"]*.8*E.CLASS_DEMAND_MULTIPLIER) for c in timetable]
    combos = make_combos(forecast=forecast)
    spec = case["controller"]
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
    controller = Static(combos[spec["combo"]]) if spec["kind"] == "fixed" else MapSwitch(combos, FrozenPolicy(spec))
    sim = DualBankSim(passengers, controller, end).run()
    incomplete = int(sum(p.t_alight is None or p.t_alight > sim.t+1e-8 for p in passengers))
    bad_times = int(sum(p.t_board is not None and
                       (p.t_board < p.t or (p.t_alight is not None and p.t_alight <= p.t_board))
                       for p in passengers))
    result = dict(case_id=case_id, case=case, fingerprint=fingerprint,
                  input_hash=digest(records), input_n=len(records),
                  incomplete=incomplete, bad_times=bad_times,
                  status="completed", end_time=sim.t)
    if incomplete or bad_times or sim.calls or sim.waiting or sim._sessions:
        result.update(status="invalid_or_undrained", residual_calls=len(sim.calls))
        save(path, result)
        raise RuntimeError(f"Case did not drain with valid chronology: {case_id}")
    result["metrics"] = summarize(sim)
    result["features"] = features(passengers, end)
    result["switches"] = max(0, len(sim.combo_log)-1)
    if case["input"]["kind"] == "day":
        result["lunch"] = summarize(sim, t_from=4*3600, t_to=6*3600)
        windows = [(c["start"]-600, c["start"]+120) for c in timetable]
        windows += [(c["end"], c["end"]+360) for c in timetable]
        result["peak"] = summarize(sim, keep=lambda p: any(a <= p.t < b for a, b in windows))
    with gzip.open(output / "passengers" / f"{case_id}.json.gz", "wt", encoding="utf-8") as stream:
        json.dump([(p.id, p.t, p.o, p.d, p.group, p.t_board, p.t_alight, p.car) for p in passengers],
                  stream, separators=(",", ":"))
    result["passenger_sha256"] = hashlib.sha256(
        (output / "passengers" / f"{case_id}.json.gz").read_bytes()).hexdigest()
    save(path, result)
    return result


class Study:
    def __init__(self, output, workers):
        if not 1 <= workers <= 4:
            raise ValueError("Use 1-4 workers")
        self.output, self.workers = output, workers
        e0 = OUTPUT / "e0_verified"
        e0_protocol = json.loads((e0 / "protocol.json").read_text())
        with (e0 / "e0_rtt_validation.csv").open(newline="", encoding="utf-8") as stream:
            rtt_errors = [abs(float(row["err_pct"])) for row in csv.DictReader(stream)]
        if (e0_protocol["status"] != "completed" or len(rtt_errors) != 10
                or not np.isfinite(rtt_errors).all() or mean(rtt_errors) > 15):
            raise RuntimeError("Completed E0 formula benchmark is required")
        gate = ROOT / "results" / "two_independent_banks" / "step_gate_2p5_chronological_v2"
        gate_report = json.loads((gate / "screen.json").read_text())
        gate_protocol = json.loads((gate / "protocol.json").read_text())
        if not gate_report["passed"] or gate_protocol["completed_cases"] != 162:
            raise RuntimeError("Chronological numerical gate must pass first")
        for name in ("banking.py", "building.py", "traffic.py", "config.py", "sim.py", "strategies.py"):
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != gate_protocol["sources"][name]:
                raise RuntimeError(f"Physical/input model changed since numerical gate: {name}")
        self.sources = {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in SOURCES}
        timetable_hash = hashlib.sha256(Path(E.TIMETABLE).read_bytes()).hexdigest()
        legacy = json.loads((ROOT / "results" / "e1_tree.json").read_text())
        self.legacy = dict(kind="tree", features=legacy["features"], nodes=legacy["nodes"])
        self.fingerprint = digest(dict(sources=self.sources, timetable=timetable_hash, legacy=self.legacy))
        output.mkdir(exist_ok=True, parents=True)
        protocol_path = output / "protocol.json"
        if protocol_path.exists():
            existing = json.loads(protocol_path.read_text())
            if existing["fingerprint"] != self.fingerprint:
                raise RuntimeError("Frozen research protocol does not match current source/input model")
        else:
            for name in ("cases", "passengers", "sources"):
                (output / name).mkdir()
            for name in SOURCES:
                (output / "sources" / name).write_bytes((ROOT / name).read_bytes())
            (output / "sources" / "timetable_synthetic.csv").write_bytes(Path(E.TIMETABLE).read_bytes())
            save(output / "legacy_rule.json", self.legacy)
            save(protocol_path, dict(
                status="running", fingerprint=self.fingerprint, sources=self.sources,
                timetable_sha256=timetable_hash, dev_seeds=DEV_SEEDS, boundary_seeds=BOUNDARY_SEEDS,
                confirmation_seeds=CONFIRM_SEEDS, robust_seeds=ROBUST_SEEDS,
                confirmation_conditions=CONDITIONS, fixed_candidates=E.MAP_COMBOS,
                practical_gain_seconds=.25, dt_integration_cap=.25,
                e0_protocol_sha256=hashlib.sha256((e0 / "protocol.json").read_bytes()).hexdigest(),
                e0_mean_absolute_rtt_error_pct=mean(rtt_errors),
                selection="best fixed by development-day mean AWT; adopt candidate only with >=0.25s gain and paired 95% CI upper<0",
                boundary="24 coarse points then at most 12 adjacent-rate midpoints; 4 paired seeds per point; C1/C7 only",
                boundary_range=dict(lam5=[30, 90, 225, 400], split_in=[.1, .35, .65],
                                    inter=.3, thru=.3, office_mix=[0, 1], duration=900, arrival_profile="uniform"),
                candidate="depth 1/2/3 C1/C7 tree, min leaf 4; five-fold point-level CV against actual C1/C7 minimum; simplest within 0.25s of minimum CV regret",
                confirmation="15 untouched full days; conditions cycle through the five predefined attendance/through pairs; no tuning",
                robustness="6 new nominal days; rate estimates +/-20%, entrance OD weights +/-20%, then parking/bypass/reassignment-off ablations",
                measurement="AWT arrival-to-boarding; ATT arrival-to-alighting; score whole arrival cohort; report per-day mean and paired Student-t 95% CI",
                limitation="Synthetic model-only inference; C1/C7 share ETA and bypass and differ in parking. C6 uses forecast information and is excluded from this six-candidate fixed-control family."))

    def verify_sources(self):
        for name, sha in self.sources.items():
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha:
                raise RuntimeError(f"Research source changed during execution: {name}")

    def execute(self, jobs, phase):
        self.verify_sources()
        unique = {digest(case): case for case in jobs}
        results = {}
        with ProcessPoolExecutor(max_workers=self.workers) as pool:
            for index, result in enumerate(pool.map(
                    run_job, ((case, self.output, self.fingerprint) for case in unique.values())), 1):
                results[result["case_id"]] = result
                if index % 6 == 0 or index == len(unique):
                    print(f"{phase}: {index}/{len(unique)} unique trajectories", flush=True)
        self.verify_sources()
        return [results[digest(case)] for case in jobs]

    def phase_exists(self, name):
        path = self.output / f"{name}.json"
        if path.exists():
            result = json.loads(path.read_text(encoding="utf-8"))
            if result.get("status") != "completed":
                raise RuntimeError(f"Research stage incomplete: {name}")
            return result
        return None

    def baseline(self):
        previous = self.phase_exists("baseline")
        if previous is not None:
            return previous
        rows = self.execute([job(day(seed), fixed(combo)) for combo in E.MAP_COMBOS for seed in DEV_SEEDS],
                            "fixed baseline development")
        means = {combo: mean(row["metrics"]["awt"] for row in rows
                             if row["case"]["controller"]["combo"] == combo) for combo in E.MAP_COMBOS}
        selected = min(E.MAP_COMBOS, key=lambda combo: (means[combo], E.MAP_COMBOS.index(combo)))
        result = dict(status="completed", selected=selected, mean_awt=means,
                      seeds=DEV_SEEDS, cases=[r["case_id"] for r in rows])
        save(self.output / "baseline.json", result)
        return result

    def point(self, lam5, incoming, alpha):
        scenario = replace(E.SCENARIOS["S3"], name="C1_C7_boundary", lam5=lam5,
                           split=(incoming, .7-incoming, .3), weights=E.mix_weights(alpha), profile="uniform")
        return dict(lam5=lam5, incoming=incoming, alpha=alpha, scenario=asdict(scenario))

    def evaluate_points(self, points, phase):
        jobs = [job(dict(kind="scenario", seed=seed, scenario=point["scenario"]), fixed(combo))
                for point in points for combo in ("C1", "C7") for seed in BOUNDARY_SEEDS]
        rows = self.execute(jobs, phase)
        lookup = {row["case_id"]: row for row in rows}
        evaluated = []
        for point in points:
            entry = {key: value for key, value in point.items() if key != "scenario"}
            group = []
            for combo in ("C1", "C7"):
                results = [lookup[digest(job(dict(kind="scenario", seed=seed, scenario=point["scenario"]),
                                             fixed(combo)))] for seed in BOUNDARY_SEEDS]
                entry[f"awt_{combo}"] = mean(row["metrics"]["awt"] for row in results)
                group.extend(results)
            entry["delta_C7_C1"] = entry["awt_C7"]-entry["awt_C1"]
            entry["features"] = {name: mean(row["features"][name] for row in group) for name in FEATURES}
            entry["cases"] = [row["case_id"] for row in group]
            evaluated.append(entry)
        return evaluated

    def boundary(self):
        previous = self.phase_exists("boundary")
        if previous is not None:
            return previous
        coarse = [self.point(lam, incoming, alpha) for lam in (30, 90, 225, 400)
                  for incoming in (.1, .35, .65) for alpha in (0., 1.)]
        points = self.evaluate_points(coarse, "coarse C1/C7 boundary")
        candidates = []
        for incoming in (.1, .35, .65):
            for alpha in (0., 1.):
                group = sorted((p for p in points if p["incoming"] == incoming and p["alpha"] == alpha),
                               key=lambda p: p["lam5"])
                for left, right in zip(group, group[1:]):
                    crossing = left["delta_C7_C1"]*right["delta_C7_C1"] <= 0
                    distance = min(abs(left["delta_C7_C1"]), abs(right["delta_C7_C1"]))
                    candidates.append((not crossing, distance,
                                       self.point((left["lam5"]+right["lam5"])/2, incoming, alpha)))
        candidates.sort(key=lambda item: (item[0], item[1], item[2]["lam5"], item[2]["incoming"], item[2]["alpha"]))
        refined = [point for _, _, point in candidates[:12]]
        points += self.evaluate_points(refined, "refined C1/C7 boundary")
        X = np.array([[point["features"][name] for name in FEATURES] for point in points])
        costs = np.array([[point["awt_C1"], point["awt_C7"]] for point in points])
        y = np.where(costs[:, 1] < costs[:, 0]-.25, "C7", "C1")
        cv = []
        for depth in (1, 2, 3):
            losses = []
            for train, test in KFold(5, shuffle=True, random_state=9252).split(X):
                model = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=4, random_state=9252)
                model.fit(X[train], y[train])
                pred = model.predict(X[test])
                chosen = np.where(pred == "C7", costs[test, 1], costs[test, 0])
                losses.extend(chosen-costs[test].min(axis=1))
            cv.append(dict(depth=depth, regret=mean(losses)))
        minimum = min(row["regret"] for row in cv)
        depth = min(row["depth"] for row in cv if row["regret"] <= minimum+.25)
        model = DecisionTreeClassifier(max_depth=depth, min_samples_leaf=4, random_state=9252).fit(X, y)
        candidate = canonical(dict(kind="tree", features=FEATURES, nodes=E.tree_nodes(model)))
        result = dict(status="completed", points=points, coarse_points=24, refined_points=len(refined),
                      cv=cv, selected_depth=depth, candidate=candidate)
        save(self.output / "boundary.json", result)
        return result

    def freeze(self, baseline, boundary):
        previous = self.phase_exists("frozen_rule")
        if previous is not None:
            return previous
        candidate = boundary["candidate"]
        rows = self.execute([job(day(seed), spec) for seed in DEV_SEEDS
                             for spec in (candidate, fixed(baseline["selected"]))], "daily development rule selection")
        lookup = {row["case_id"]: row for row in rows}
        diffs = []
        for seed in DEV_SEEDS:
            selected = lookup[digest(job(day(seed), candidate))]
            comparator = lookup[digest(job(day(seed), fixed(baseline["selected"])))]
            self.check_pair(selected, comparator)
            diffs.append(selected["metrics"]["awt"]-comparator["metrics"]["awt"])
        center, half = mean_ci(diffs)
        adopt = center <= -.25 and center+half < 0
        result = dict(
            status="completed", controller=candidate if adopt else fixed(baseline["selected"]),
            best_fixed=baseline["selected"], candidate=candidate, candidate_adopted=bool(adopt),
            development_delta=float(center), development_ci95=[float(center-half), float(center+half)],
            development_seed_differences=diffs, confirmation_started=False,
            boundary_sha256=hashlib.sha256((self.output / "boundary.json").read_bytes()).hexdigest(),
            baseline_sha256=hashlib.sha256((self.output / "baseline.json").read_bytes()).hexdigest(),
            cases=[row["case_id"] for row in rows],
            rule_status="switching" if adopt and candidate["kind"] == "tree" else "constant_no_switch")
        save(self.output / "frozen_rule.json", result)
        return result

    @staticmethod
    def check_pair(a, b):
        if a["input_hash"] != b["input_hash"] or a["input_n"] != b["input_n"]:
            raise ValueError("Paired controllers did not receive identical passenger inputs")

    def comparisons(self, inputs, named_specs, rows, reference):
        lookup = {row["case_id"]: row for row in rows}
        output = {}
        for name, spec in named_specs.items():
            summary = {}
            for cohort in ("metrics", "lunch", "peak"):
                differences, absolute = [], []
                for specification in inputs:
                    row = lookup[digest(job(specification, spec))]
                    ref = lookup[digest(job(specification, named_specs[reference]))]
                    self.check_pair(row, ref)
                    differences.append(row[cohort]["awt"]-ref[cohort]["awt"])
                    absolute.append(row[cohort]["awt"])
                center, half = mean_ci(differences)
                summary[cohort] = dict(mean_awt=mean(absolute), delta_vs_reference=float(center),
                                       ci95=[float(center-half), float(center+half)],
                                       paired_seed_differences=differences)
            output[name] = summary
        return output

    def confirm(self, frozen):
        previous = self.phase_exists("confirmation")
        if previous is not None:
            return previous
        freeze_hash = hashlib.sha256((self.output / "frozen_rule.json").read_bytes()).hexdigest()
        inputs = [day(seed, *CONDITIONS[index % len(CONDITIONS)])
                  for index, seed in enumerate(CONFIRM_SEEDS)]
        specs = dict(frozen=frozen["controller"], best_fixed=fixed(frozen["best_fixed"]),
                     C1=fixed("C1"), C7=fixed("C7"), historical_map=self.legacy)
        rows = self.execute([job(specification, spec) for specification in inputs for spec in specs.values()],
                            "untouched daily confirmation")
        if hashlib.sha256((self.output / "frozen_rule.json").read_bytes()).hexdigest() != freeze_hash:
            raise RuntimeError("Frozen rule changed during confirmation")
        result = dict(status="completed", frozen_sha256=freeze_hash, inputs=inputs, specs=specs,
                      reference="best_fixed", comparisons=self.comparisons(inputs, specs, rows, "best_fixed"),
                      cases=sorted({row["case_id"] for row in rows}), used_for_selection=False)
        save(self.output / "confirmation.json", result)
        return result

    def robustness(self, frozen):
        previous = self.phase_exists("robustness")
        if previous is not None:
            return previous
        inputs = [day(seed) for seed in ROBUST_SEEDS]
        specs = dict(ideal=frozen["controller"])
        for error in ("rate_low", "rate_high", "entrance_low", "entrance_high"):
            specs[error] = canonical(dict(frozen["controller"], error=error))
        for ablation in ("parking_off", "bypass_off", "reassignment_off"):
            specs[ablation] = canonical(dict(frozen["controller"], ablation=ablation))
        rows = self.execute([job(specification, spec) for specification in inputs for spec in specs.values()],
                            "estimation errors and component ablations")
        result = dict(status="completed", inputs=inputs, specs=specs,
                      constant_rule_errors_are_noops=frozen["controller"]["kind"] == "fixed",
                      comparisons=self.comparisons(inputs, specs, rows, "ideal"),
                      cases=sorted({row["case_id"] for row in rows}), used_for_selection=False)
        save(self.output / "robustness.json", result)
        return result

    def run(self):
        baseline = self.baseline()
        boundary = self.boundary()
        frozen = self.freeze(baseline, boundary)
        confirmation = self.confirm(frozen)
        robustness = self.robustness(frozen)
        self.verify_sources()
        save(self.output / "summary.json", dict(
            status="completed", best_fixed=baseline["selected"], rule_status=frozen["rule_status"],
            candidate_adopted=frozen["candidate_adopted"],
            confirmation=confirmation["comparisons"], robustness=robustness["comparisons"],
            warning="All results are model-only; no confirmation or robustness cases used to tune the rule."))
        protocol = json.loads((self.output / "protocol.json").read_text())
        protocol["status"] = "completed"
        save(self.output / "protocol.json", protocol)
        print(f"COMPLETED all five research stages: {self.output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    Study(args.output.resolve(), args.workers).run()
