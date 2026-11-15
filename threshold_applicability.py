"""Paired transfer tests of the unchanged C1/C2 candidate threshold; no tuning."""
import argparse
import bisect
from concurrent.futures import ProcessPoolExecutor, as_completed
from functools import lru_cache
import gzip
import json
from pathlib import Path
from statistics import mean
import time
import traceback

import numpy as np
import pandas as pd

import config as C
from banking import DualBankSim
from complete_experiments import (
    OUTPUT as ORIGINAL, ROOT, SOURCE_NAMES, build_controller, digest, fixed,
    intervals, load, paired_summary, save, sha,
)
from controllers import features
from metrics import summarize
from revalidate_switching import FrozenPolicy
from strategies import make_combos
from traffic import Passenger, Scenario, generate_scenario


OUTPUT = ROOT / "results" / "two_independent_banks" / "threshold_applicability_v1"
SEEDS = tuple(range(952101, 952107))
SCENES = {
    "kkl_mixed": dict(split=[.35, .35, .30], weights=C.FLOOR_WEIGHTS_CLASS,
                      entrances=C.ENTRANCE_WEIGHTS, stairs=True, thru=.3),
    "office_dual": dict(split=[.95, 0., .05],
                        weights={floor: float(floor.isdigit()) for floor in C.FLOORS},
                        entrances=C.ENTRANCE_WEIGHTS, stairs=False, thru=0.),
    "office_single": dict(split=[.95, 0., .05],
                          weights={floor: float(floor.isdigit()) for floor in C.FLOORS},
                          entrances={"G": 1.}, stairs=False, thru=0.),
}
PROFILES = {
    "steady200": [(900, 200)],
    "steady300": [(900, 300)],
    "steady450": [(900, 450)],
    "low_high_low": [(300, 100), (900, 450), (300, 100)],
}
SCENE_NAMES = {"kkl_mixed": "KKL混合型", "office_dual": "两入口办公型", "office_single": "单入口办公型"}
PROFILE_NAMES = {"steady200": "持续200", "steady300": "持续300",
                 "steady450": "持续450", "low_high_low": "100→450→100"}


@lru_cache(maxsize=128)
def input_records(serialized):
    specification = json.loads(serialized)
    scene, schedule, seed = specification["scene"], specification["schedule"], specification["seed"]
    raw, start, stairs, before_clip = [], 0., 0, 0
    for phase, (duration, rate) in enumerate(schedule):
        scenario = Scenario(name=f"phase{phase}", lam5=rate, duration=duration,
                            batch_mean=1.4, profile="uniform", **scene)
        passengers, count = generate_scenario(scenario, seed+phase*1000, t0=start)
        stairs += count
        raw.extend((p.t, p.o, p.d, phase*1_000_000+p.group) for p in passengers)
        start += duration
    before_clip = len(raw)
    # As in the daily generator, the experiment horizon defines its arrival population.
    raw = sorted(record for record in raw if 0 <= record[0] < start)
    records = tuple((index, *record) for index, record in enumerate(raw))
    return records, start, dict(stairs=stairs, outside_horizon=before_clip-len(raw))


def make_case(scene_name, profile_name, seed, controller):
    return json.loads(json.dumps(dict(
        input=dict(scene_name=scene_name, scene=SCENES[scene_name], profile_name=profile_name,
                   schedule=PROFILES[profile_name], seed=seed),
        controller=controller), sort_keys=True))


def phase_windows(schedule):
    start, result = 0., []
    for index, (duration, rate) in enumerate(schedule):
        result.append((f"phase{index}", start, start+duration, rate))
        start += duration
    return result


def run_case(arguments):
    specification, output, fingerprint = arguments
    identity = digest(specification)
    path, raw_path = output / "cases" / f"{identity}.json", output / "passengers" / f"{identity}.json.gz"
    if path.exists():
        row = load(path)
        if (row["status"] != "completed" or row["case"] != specification
                or row["fingerprint"] != fingerprint or row["raw_sha256"] != sha(raw_path)):
            raise RuntimeError(f"Invalid cached case: {path}")
        return row
    started = time.monotonic()
    C.DT, C.DRAIN_TIME = .25, 21600
    records, duration, generation = input_records(json.dumps(specification["input"], sort_keys=True))
    passengers = [Passenger(*record) for record in records]
    controller, policy = build_controller(specification["controller"], make_combos(), [], duration, ())
    sim = DualBankSim(passengers, controller, duration).run()
    incomplete = int(sum(p.t_board is None or p.t_alight is None or p.t_alight > sim.t+1e-8
                         for p in passengers))
    bad_times = int(sum(p.t_board is not None and
                        (p.t_board < p.t or (p.t_alight is not None and p.t_alight <= p.t_board))
                        for p in passengers))
    row = dict(status="completed", case_id=identity, case=specification, fingerprint=fingerprint,
               input_hash=digest(records), input_n=len(records), end_time=sim.t,
               incomplete=incomplete, bad_times=bad_times, generation=generation)
    if incomplete or bad_times or sim.calls or sim.waiting or sim._sessions:
        row.update(status="invalid_or_undrained", residual_calls=len(sim.calls))
        save(path, row)
        raise RuntimeError(f"Invalid/undrained applicability case: {identity}")
    row["metrics"] = summarize(sim)
    row["features"] = features(passengers, duration)
    row["phases"] = {
        name: dict(nominal_rate=rate, start=start, end=end,
                   features=features([p for p in passengers if start <= p.t < end], end-start),
                   metrics=summarize(sim, t_from=start, t_to=end))
        for name, start, end, rate in phase_windows(specification["input"]["schedule"])}
    row["intervals"] = [list(item) for item in intervals(sim.combo_log, duration)]
    row["switches"] = max(0, len(row["intervals"])-1)
    row["c2_duty"] = sum(end-start for start, end, name in row["intervals"] if name == "C2")/duration
    row["empty_stops"], row["reopenings"] = sim.empty_stops, sim.reopenings
    payload = dict(passengers=[(*record, p.t_board, p.t_alight, p.car)
                               for record, p in zip(records, passengers)],
                   decisions=policy.trace if policy else [])
    temporary = raw_path.with_suffix(".gz.tmp")
    with gzip.open(temporary, "wt", encoding="utf-8") as stream:
        json.dump(payload, stream, separators=(",", ":"), allow_nan=False)
    temporary.replace(raw_path)
    row["raw_sha256"], row["elapsed_seconds"] = sha(raw_path), time.monotonic()-started
    save(path, row)
    return row


def holm(p_values):
    values = np.asarray(p_values, dtype=float)
    if values.ndim != 1 or not np.isfinite(values).all() or np.any((values < 0) | (values > 1)):
        raise ValueError("Holm correction requires finite probabilities")
    ordered = np.argsort(values, kind="stable")
    result = np.empty(len(values))
    adjusted = 0.
    for rank, index in enumerate(ordered):
        adjusted = max(adjusted, min(1., (len(values)-rank)*values[index]))
        result[index] = adjusted
    return result


def comparisons(records, controls):
    whole, phases = [], []
    for scene in SCENES:
        for profile in PROFILES:
            rows = {
                name: [records[digest(make_case(scene, profile, seed, spec))] for seed in SEEDS]
                for name, spec in controls.items()}
            for name in controls:
                for reference in ("C1", "C2", "C4"):
                    for a, b in zip(rows[name], rows[reference]):
                        if a["input_hash"] != b["input_hash"]:
                            raise AssertionError("Controller inputs are not paired")
                    record = dict(
                        scene=scene, profile=profile, controller=name, reference=reference,
                        **paired_summary([row["metrics"]["awt"] for row in rows[name]],
                                         [row["metrics"]["awt"] for row in rows[reference]]),
                        actual_rate=mean(row["features"]["lam5"] for row in rows[name]),
                        passengers=mean(row["input_n"] for row in rows[name]),
                        p90=mean(row["metrics"]["p90"] for row in rows[name]),
                        lwt=mean(row["metrics"]["lwt"] for row in rows[name]),
                        att=mean(row["metrics"]["att"] for row in rows[name]),
                        switches=mean(row["switches"] for row in rows[name]),
                        c2_duty=mean(row["c2_duty"] for row in rows[name]))
                    whole.append(record)
                    for phase in rows[name][0]["phases"]:
                        phases.append(dict(
                            scene=scene, profile=profile, phase=phase, controller=name, reference=reference,
                            **paired_summary([row["phases"][phase]["metrics"]["awt"] for row in rows[name]],
                                             [row["phases"][phase]["metrics"]["awt"] for row in rows[reference]]),
                            actual_rate=mean(row["phases"][phase]["features"]["lam5"] for row in rows[name])))
    primary = [row for row in whole if row["controller"] == "map"]
    for row, p in zip(primary, holm([row["p"] for row in primary])):
        row["p_holm36"] = float(p)
        row["map_better_after_holm"] = bool(row["delta"] <= -.25 and p < .05)
    conclusions = []
    for scene in SCENES:
        for profile in PROFILES:
            group = [row for row in primary if row["scene"] == scene and row["profile"] == profile]
            fixed_means = {name: next(row["awt"] for row in whole
                                     if row["scene"] == scene and row["profile"] == profile
                                     and row["controller"] == name and row["reference"] == "C4")
                           for name in ("C1", "C2", "C4")}
            conclusions.append(dict(
                scene=scene, profile=profile, fixed_mean_awt=fixed_means,
                best_fixed_by_mean=min(fixed_means, key=fixed_means.get),
                map_mean_awt=group[0]["awt"],
                map_beats_all_three_fixed=all(row["map_better_after_holm"] for row in group),
                map_beats_C1=next(row["map_better_after_holm"] for row in group if row["reference"] == "C1"),
                map_beats_C2=next(row["map_better_after_holm"] for row in group if row["reference"] == "C2"),
                map_beats_C4=next(row["map_better_after_holm"] for row in group if row["reference"] == "C4")))
    return whole, phases, conclusions


class Study:
    def __init__(self, output=OUTPUT, workers=4):
        if not 1 <= workers <= 4:
            raise ValueError("Use 1-4 workers")
        self.output, self.workers = Path(output).resolve(), workers
        self.tree_path = ORIGINAL / "e1_tree.json"
        if load(ORIGINAL / "validation.json")["status"] != "passed":
            raise RuntimeError("Original formal study must be verified")
        self.tree = dict(kind="tree", **load(self.tree_path))
        self.controls = dict(C1=fixed("C1"), C2=fixed("C2"), C4=fixed("C4"),
                             map=dict(kind="realtime", tree=self.tree))
        self.sources = {name: sha(ROOT / name) for name in (*SOURCE_NAMES, "threshold_applicability.py")}
        design = dict(scenes=SCENES, profiles=PROFILES, seeds=SEEDS, controls=self.controls,
                      dt=.25, drain_seconds=21600, phase_seed_offset=1000)
        self.fingerprint = digest(dict(sources=self.sources, design=design, tree_sha256=sha(self.tree_path)))
        self.output.mkdir(parents=True, exist_ok=True)
        path = self.output / "protocol.json"
        if path.exists():
            self.protocol = load(path)
            if self.protocol["fingerprint"] != self.fingerprint:
                raise RuntimeError("Changed sources/design; use a new experiment directory")
        else:
            if any(self.output.iterdir()):
                raise RuntimeError("Refusing to overwrite an unrecognized experiment directory")
            for folder in ("cases", "passengers", "sources", "figures"):
                (self.output / folder).mkdir()
            for name in self.sources:
                (self.output / "sources" / name).write_bytes((ROOT / name).read_bytes())
            (self.output / "candidate_tree.json").write_bytes(self.tree_path.read_bytes())
            self.protocol = dict(
                status="registered", fingerprint=self.fingerprint, sources=self.sources,
                design=design, tree_sha256=sha(self.tree_path), expected_cases=288,
                original_validation_sha256=sha(ORIGINAL / "validation.json"),
                primary="Whole arrival-population AWT, map vs C1/C2/C4 across12 scenarios; Holm36",
                meaningful_gain_seconds=.25, seed_reuse_from_original=False,
                tuning=False, adopted_policy_still_C4=True,
                office_definition="Changed demand only: 95% incoming, 5% interfloor, no through, floors1-16 uniform, no stairs",
                migration_limit="Original parking and zoning remain unchanged, including LG2 parking for G-only demand",
                generalization_limit="Same KKL geometry and two independent banks; office-like model demand, not real office validation",
                phase_limit="5min low +15min high +5min low; retain all queues/car states across transitions",
                inference="95%paired intervals are nominal; use Holm36 p-values for primary conclusions; phases exploratory")
            save(path, self.protocol)

    def verify_sources(self):
        if any(sha(ROOT / name) != value or sha(self.output / "sources" / name) != value
               for name, value in self.sources.items()):
            raise RuntimeError("Experiment sources changed")
        if sha(self.tree_path) != self.protocol["tree_sha256"] or sha(self.output / "candidate_tree.json") != sha(self.tree_path):
            raise RuntimeError("Candidate threshold changed")

    def jobs(self):
        return [make_case(scene, profile, seed, controller)
                for scene in SCENES for profile in PROFILES
                for controller in self.controls.values() for seed in SEEDS]

    def execute(self):
        self.verify_sources()
        jobs, rows = self.jobs(), {}
        started = time.monotonic()
        save(self.output / "progress.json", dict(phase="simulations", completed=0, total=len(jobs)))
        with ProcessPoolExecutor(max_workers=self.workers) as pool:
            futures = [pool.submit(run_case, (job, self.output, self.fingerprint)) for job in jobs]
            try:
                for future in as_completed(futures):
                    row = future.result()
                    rows[row["case_id"]] = row
                    if len(rows) % 12 == 0 or len(rows) == len(jobs):
                        elapsed = time.monotonic()-started
                        save(self.output / "progress.json", dict(
                            phase="simulations", completed=len(rows), total=len(jobs), elapsed_seconds=elapsed))
                        print(f"Applicability: {len(rows)}/{len(jobs)}, {elapsed:.1f}s", flush=True)
            except Exception:
                for future in futures:
                    future.cancel()
                raise
        self.verify_sources()
        return rows

    def validate(self, records):
        self.verify_sources()
        if set(records) != {digest(job) for job in self.jobs()} or len(records) != 288:
            raise AssertionError("Missing or extra experimental trajectories")
        for row in sorted(records.values(), key=lambda item: digest(item["case"]["input"])):
            if row["status"] != "completed" or row["fingerprint"] != self.fingerprint:
                raise AssertionError("Incomplete or mixed-revision case")
            if digest(row["case"]) != row["case_id"]:
                raise AssertionError("Wrong case identity")
            path = self.output / "passengers" / f"{row['case_id']}.json.gz"
            if sha(path) != row["raw_sha256"]:
                raise AssertionError("Raw hash changed")
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                raw = json.load(stream)
            people = raw["passengers"]
            generated, duration, generation = input_records(json.dumps(row["case"]["input"], sort_keys=True))
            if (digest(generated) != row["input_hash"] or digest([p[:5] for p in people]) != row["input_hash"]
                    or generation != row["generation"] or len(people) != row["input_n"]):
                raise AssertionError("Seed/input regeneration failed")
            if any(p[5] is None or p[6] is None or not p[1] <= p[5] < p[6] <= row["end_time"]+1e-8 for p in people):
                raise AssertionError("Invalid passenger chronology")
            cohorts = [("all", 0., duration)]
            cohorts += [(name, start, end) for name, start, end, _ in phase_windows(row["case"]["input"]["schedule"])]
            for name, start, end in cohorts:
                group = [p for p in people if start <= p[1] < end]
                measured = row["metrics"] if name == "all" else row["phases"][name]["metrics"]
                waits = np.array([p[5]-p[1] for p in group])
                if len(group) != measured["n"]:
                    raise AssertionError("Wrong phase cohort")
                if group:
                    expected = dict(awt=float(waits.mean()), p90=float(np.percentile(waits, 90)),
                                    lwt=float((waits > C.LONG_WAIT).mean()*100), att=mean(p[6]-p[1] for p in group))
                    if any(abs(value-measured[key]) > 1e-9 for key, value in expected.items()):
                        raise AssertionError("Raw metrics cannot be recomputed")
                feature = features([Passenger(*p[:5]) for p in group], end-start)
                stored = row["features"] if name == "all" else row["phases"][name]["features"]
                if feature != stored:
                    raise AssertionError("Actual observed rate differs")
            times = [p[1] for p in people]
            for timestamp, feat, perturbed, chosen, clean in raw["decisions"]:
                lo, hi = bisect.bisect_left(times, timestamp-300), bisect.bisect_left(times, timestamp)
                expected = features([Passenger(*p[:5]) for p in people[lo:hi]], min(300., max(timestamp, 1.)))
                if feat != expected or perturbed != feat or chosen != clean or chosen != FrozenPolicy(self.tree).predict(feat):
                    raise AssertionError("Observed window or frozen decision differs")
        summary = load(self.output / "summary.json")
        whole, phases, conclusions = comparisons(records, self.controls)
        if whole != summary["comparisons"] or phases != summary["phase_comparisons"] or conclusions != summary["conclusions"]:
            raise AssertionError("Paired statistics or Holm conclusions differ")
        result = dict(status="passed", cases=288, seed_regeneration=True, raw_metrics=True,
                      phase_metrics=True, rolling_windows=True, unchanged_threshold=True,
                      paired_statistics_and_holm36=True, no_tuning=True)
        save(self.output / "validation.json", result)
        return result

    def figures(self, records):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
        plt.rcParams["axes.unicode_minus"] = False
        summary = pd.DataFrame(load(self.output / "summary.json")["comparisons"])
        fig, axes = plt.subplots(1, 3, figsize=(15, 5), sharey=True)
        for axis, scene in zip(axes, SCENES):
            for name, color in (("C1", "#1f77b4"), ("C2", "#d62728"), ("C4", "#2ca02c"), ("map", "#9467bd")):
                group = summary[(summary.scene == scene) & (summary.controller == name) & (summary.reference == "C4")]
                axis.plot(range(4), group.awt, marker="o", label="301阈值地图" if name == "map" else name, color=color)
            axis.set_title(SCENE_NAMES[scene])
            axis.set_xticks(range(4), [PROFILE_NAMES[name] for name in PROFILES], rotation=25)
            axis.set_xlabel("名义人流／5分钟；最后一项为分段过程")
            axis.grid(alpha=.2)
        axes[0].set_ylabel("完整到达群体平均等待／秒")
        axes[-1].legend()
        fig.suptitle("固定候选阈值的适用场景测试：6个新配对种子，不重调策略或阈值")
        fig.tight_layout()
        fig.savefig(self.output / "figures" / "applicability_awt_zh.png", dpi=160)
        plt.close(fig)
        fig, axes = plt.subplots(3, 2, figsize=(13, 11), sharex=True)
        for index, scene in enumerate(SCENES):
            left, right = axes[index]
            edges = np.arange(0, 1501, 60)
            center = (edges[:-1]+edges[1:])/120
            flows = []
            for seed in SEEDS:
                row = records[digest(make_case(scene, "low_high_low", seed, self.controls["C4"]))]
                with gzip.open(self.output / "passengers" / f"{row['case_id']}.json.gz", "rt", encoding="utf-8") as stream:
                    people = json.load(stream)["passengers"]
                flows.append(np.histogram([p[1] for p in people], edges)[0]*5)
            left.plot(center, np.mean(flows, axis=0), color="#1f77b4")
            left.axhline(self.tree["nodes"][0]["thr"], ls="--", color="#d62728", lw=1)
            left.set_ylabel(f"{SCENE_NAMES[scene]}\n一分钟到达数×5")
            for name, color in (("C1", "#1f77b4"), ("C2", "#d62728"), ("C4", "#2ca02c"), ("map", "#9467bd")):
                curves = []
                for seed in SEEDS:
                    row = records[digest(make_case(scene, "low_high_low", seed, self.controls[name]))]
                    with gzip.open(self.output / "passengers" / f"{row['case_id']}.json.gz", "rt", encoding="utf-8") as stream:
                        people = json.load(stream)["passengers"]
                    bins = np.searchsorted(edges, [p[1] for p in people], side="right")-1
                    counts = np.bincount(bins, minlength=len(edges)-1)
                    sums = np.bincount(bins, weights=[p[5]-p[1] for p in people], minlength=len(edges)-1)
                    curves.append(np.divide(sums, counts, out=np.full(len(counts), np.nan), where=counts > 0))
                means = [float(np.mean(values[np.isfinite(values)])) if np.isfinite(values).any() else np.nan
                         for values in np.asarray(curves).T]
                right.plot(center, means, label="阈值地图" if name == "map" else name, color=color)
            right.set_ylabel("按到达分钟分组的AWT／秒")
            for axis in (left, right):
                axis.axvspan(5, 20, color="#ffd166", alpha=.15)
                axis.grid(alpha=.2)
                axis.set_xlim(0, 25)
        axes[0, 0].set_title("实际生成的人流；不是控制器滚动估计")
        axes[0, 1].set_title("保留队列和车辆状态的低→高→低过程")
        axes[0, 1].legend(fontsize=9)
        axes[-1, 0].set_xlabel("从开始起／分钟")
        axes[-1, 1].set_xlabel("从开始起／分钟")
        fig.tight_layout()
        fig.savefig(self.output / "figures" / "transient_flow_wait_zh.png", dpi=160)
        plt.close(fig)

    def run(self):
        self.protocol["status"] = "running"
        save(self.output / "protocol.json", self.protocol)
        try:
            records = self.execute()
            whole, phases, conclusions = comparisons(records, self.controls)
            save(self.output / "summary.json", dict(status="completed", comparisons=whole,
                 phase_comparisons=phases, conclusions=conclusions, used_for_tuning=False))
            pd.DataFrame(whole).to_csv(self.output / "comparisons.csv", index=False)
            pd.DataFrame(phases).to_csv(self.output / "phase_comparisons.csv", index=False)
            pd.DataFrame([dict(
                scene=row["case"]["input"]["scene_name"], profile=row["case"]["input"]["profile_name"],
                seed=row["case"]["input"]["seed"], controller=digest(row["case"]["controller"]),
                **row["metrics"], actual_rate=row["features"]["lam5"],
                switches=row["switches"], c2_duty=row["c2_duty"], case_id=row["case_id"])
                for row in records.values()]).to_csv(self.output / "raw.csv", index=False)
            save(self.output / "progress.json", dict(phase="validation", completed=288, total=288))
            self.validate(records)
            self.figures(records)
        except Exception as error:
            self.protocol.update(status="failed", failure=str(error), traceback=traceback.format_exc())
            save(self.output / "protocol.json", self.protocol)
            raise
        self.protocol["status"] = "completed"
        self.protocol.pop("failure", None)
        self.protocol.pop("traceback", None)
        save(self.output / "protocol.json", self.protocol)
        save(self.output / "progress.json", dict(phase="completed", completed=288, total=288))
        print(f"COMPLETED AND VERIFIED: {self.output}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()
    study = Study(args.output, args.workers)
    if args.validate_only:
        rows = {row["case_id"]: row for row in (load(path) for path in (study.output / "cases").glob("*.json"))}
        print(study.validate(rows))
    else:
        study.run()
