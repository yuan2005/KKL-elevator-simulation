# Follow-up experiments on the candidate motion repair

All runs use the candidate model at `dd06745a66eb2c21f3cd2ae6ead75220eb4dbfcc`. No root simulator, demand generator or D10 controller algorithm was changed during this follow-up. These experiments extend validation while model review is pending; they do not substitute for review or real KKL calibration.

## Three original development seeds

The first repair check used seed 4000. Adding seeds 4001 and 4002 produces **270 new runs** and a **405-run combined analysis**: five dates, three backgrounds, three seeds, three time steps, and C1/C7/D10. All 405 runs have zero recorded unserved/incomplete trips, chain conflicts, late transfers and event-time violations. The original fine-grid 128/135 anomalous runs become 0/135 on the same inputs under the candidate repair.

Equal-date/background/seed D10−C1 mean waits are −0.1132, −0.1160 and −0.1204 seconds at 0.25, 0.125 and 0.0625 seconds. The corresponding three-seed intervals are [−0.3085, +0.0820], [−0.2415, +0.0095] and [−0.2380, −0.0028]. A zero crossing in one interval does not prove a difference between grids. The paired contrast changes are −0.0028 [−0.1039, +0.0983] and −0.0044 [−0.0129, +0.0041] seconds.

Absolute waits and individual input comparisons still change with grid refinement. This is a bounded repair check on already explored development inputs, not proof of numerical convergence or independent policy validation. See [full grid report](published_results/motion_repair_three_seeds_v1/结果说明.md).

## Repaired S1–S6 under original settings

The next **360 runs** retain the original scenario definitions, seeds 0–9, controllers C0/C1/C2/C3/C4/C7, LG3–16 placeholder building settings and 0.25-second time step. All input fingerprints match the original local scenario audit. Full-population completion, event-time, speed, acceleration, displacement and floor-bound checks record zero anomalies.

C7 has the lowest mean wait in S1; C1 in S2/S3/S4/S6. In S5, C4 is only 0.0041 seconds below C1 with a paired interval [−0.1524, +0.1443], which does not demonstrate superiority. Means and paired intervals are in [the scenario report](published_results/s1_s6_repaired_v2/结果说明.md).

This is a new-model baseline comparison, not a claim that repaired outputs match the old results. It does not refit the E1 policy map or revalidate old E2/E3 conclusions. These original scenarios use different building settings from the LG2–16 timetable experiments and must not be pooled with them.

The first archival attempt (`s1_s6_repaired_v1`) failed before saving a result because the new JSON helper did not convert a NumPy integer. Its protocol and failure record remain. The format-only correction is regression-tested; the complete `v2` run uses unchanged model code. Unretained work from the aborted attempt is not counted as completed validated simulations.

## Frozen D10 re-evaluation and predefined strata

The final **450 new runs** use the previously evaluated November 2–6 dates, low/mixed/high backgrounds, original seeds 5000–5004, C1/C7/D10 and predeclared time steps 0.25/0.125 seconds. All six audit totals are zero: unserved, incomplete, chain conflicts, late transfers, event-time violations and physical-constraint violations. Demand fingerprints, demand audits and D10 decisions match the original evaluation logs.

| Time step | C1 mean wait | D10 mean wait | D10−C1, 95% seed interval | Original 0.25 s benefit screen |
|---|---:|---:|---|---|
| 0.25 s | 8.2393 s | 8.1084 s | −0.1309 [−0.2092, −0.0526] s | Not met |
| 0.125 s | 8.1736 s | 8.0214 s | −0.1522 [−0.2114, −0.0931] s | Not met |

Both conditional Monte Carlo intervals exclude zero, but the mean saving remains below the original minimum. The rule is not reclassified as successful. The benefit is concentrated in the low-floor background scenario; mixed/high scenario mean differences are small positive values with intervals crossing zero. Fixed C7 still has lower mean waiting than D10 in low-floor scenarios, so improvement over C1 does not establish adaptive superiority over the best fixed control in each scenario.

Predeclared strata show a different, complementary distinction: across all background scenarios, classroom and through-traffic passengers have lower mean waits under D10, while the independently generated background-traffic layer has a roughly 0.19-second higher mean wait at both grids. This layer is not the same as the low/mixed/high background scenario variable. These subgroup findings are exploratory, not independent causal effects or evidence about measured real passengers.

See [evaluation report](published_results/adaptive_repaired_evaluation_v1/结果说明.md), [paired summary](published_results/adaptive_repaired_evaluation_v1/summary.json) and [predefined stratum/window summary](published_results/adaptive_repaired_evaluation_v1/strata_summary.json). All fixed 15-minute windows are retained, not selected for favorable effects. Local partition checks reconstruct each run's passenger count and weighted mean wait, and verify the hashes of 450 local compressed passenger traces. Subgroup/window intervals are not corrected for multiple comparisons.

Across this follow-up there are **1,080 new completed runs** (270 + 360 + 450). With the previous 135 repaired runs there are 1,215 unique repaired-model runs; adding the separate 2,145 original-model historical runs gives 3,360 archived runs. The 405-row combined grid analysis adds no runs; failed archival attempts are not counted.

## Reproduction and outputs

Use a fresh output path for every experiment. Authorized derived timetable inputs and local historical run logs are needed for exact timetable pairing; the public aggregates do not reconstruct classroom records.

```powershell
python research/timetable_validation/scripts/run_repair_validation.py --prior path/to/timestep_sensitivity_v1 --output path/to/motion_repair_extra_seeds_v1 --seeds 4001 4002
python research/timetable_validation/scripts/report_repaired_studies.py grids path/to/motion_repair_v1 path/to/motion_repair_extra_seeds_v1 --output path/to/motion_repair_three_seeds_v1
python research/timetable_validation/scripts/run_repaired_scenarios.py --prior path/to/s1_s6_original_2026-11-01 --output path/to/s1_s6_repaired_v2
python research/timetable_validation/scripts/report_repaired_studies.py scenarios path/to/s1_s6_repaired_v2

$env:KKL_DATA_DIR = 'C:\path\to\authorized\sis_2026-27'
python research/timetable_validation/scripts/run_repaired_evaluation.py --prior path/to/adaptive_v1/evaluate --output path/to/adaptive_repaired_evaluation_v1
python research/timetable_validation/scripts/report_repaired_studies.py evaluation path/to/adaptive_repaired_evaluation_v1
```

`plot_repaired_studies.py` accepts `grids`, `scenarios` or `evaluation` followed by the corresponding summary directory. Figures label model, sample size and uncertainty. Public `raw.json` files contain per-run aggregates. Detailed classroom coverage, per-input decision logs, full window strata and individual passenger traces stay in local output directories.

Protocols freeze model/script hashes before execution. Historical root sources were executed with Windows CRLF while Git stores LF; [the repair checkout provenance](published_results/motion_repair_v1/source_checkout_provenance.json) records both hashes. Original pairing runners use byte hashes, so exact replay of those old local archives requires matching checkout line endings; new result analyses do not treat newline conversion as a model change.

The old 0.140-second effect remains an original-model historical result. All new claims must cite the repaired archive and model version, and state that the timetable demand and physical parameters remain uncalibrated.
