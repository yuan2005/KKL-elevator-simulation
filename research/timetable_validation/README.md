# S1–S6 reproduction and timetable-informed demand experiments

> **Numerical repair:** the historical simulator missed a requested adjacent-floor stop at dt=0.0625 s. This branch repairs motion integration and event timestamps; see [the issue and repair scope](KNOWN_NUMERICAL_ISSUE.md). Historical results remain records of the old model and do not establish the repaired model's policy benefit. Thresholds stay frozen during repair validation.

2026-11-01 research extension, initially based on simulator commit `f6c9ca4af440cff4a258237e1214c8dc0266fb50`. Historical E0–E3 and research outputs are preserved. The current `building.py` and `sim.py` contain a candidate numerical repair; use historical commit `419c1b6` to reproduce the pre-repair model. This extension was developed with AI assistance; it is a reproducible exploration, not a calibrated model of actual KKL operations.

## What is included

- Portable demand and experiment scripts, with fifteen research tests and five motion tests covering demand conservation, overlap exclusion, reproducibility, transfer chains, past-only switching and physical motion constraints.
- Published **per-run aggregate metrics**, summaries and original protocol hashes for 1,605 completed simulations: 360 S1–S6 reproductions, 120 single-day comparisons, 675 five-day sensitivity runs, and 225 development + 225 evaluation runs for a switching candidate.
- A post-hoc diagnosis of the frozen switching candidate, clearly separated from its original evaluation.

No authenticated SIS workbook, classroom-level derived timetable, individual passenger trace, credentials, or personal progress-log draft is included. The historical protocols refer to locally retained inputs by hash. Public result files named `raw.json` contain per-simulation metrics, **not raw timetable or passenger records**.

## Run checks without timetable access

From the repository root, use Python with the packages in the root `requirements.txt`:

```sh
python -m unittest discover -s research/timetable_validation/scripts -p "test_*.py"
python -m unittest test_motion
python experiments.py smoke
python research/timetable_validation/scripts/check_published_results.py
```

These commands check current behavior and historical aggregate claims separately. To reproduce the original 360 S1–S6 runs, use a separate checkout at `419c1b6` and run `python research/timetable_validation/scripts/reproduce_s1_s6.py`; the current script rejects changed simulator hashes to avoid labelling repaired results as an original-model reproduction. Outputs go to ignored `local_runs/`, never `published_results/`. These checks do not establish real-world calibration.

## Timetable experiments with local authorized inputs

Point `KKL_DATA_DIR` at a local directory containing `semester1_kkl_dated_candidates.json` and `room_registry.json`. The data contract and provenance are in [DATA.md](DATA.md). These files were generated separately from the user's official source downloads; this package does not currently include the workbook extraction pipeline. Without them, public checks work, but exact timetable reruns do not. The earlier timetable runners below are historical recipes: run them at `419c1b6` to reproduce archived results. Running them with the repaired model is a new experiment and requires a fresh, clearly labelled output directory.

PowerShell example:

```powershell
$env:KKL_DATA_DIR = 'C:\path\to\authorized\sis_2026-27'
$env:KKL_OUTPUT_DIR = 'C:\path\to\new\experiment_outputs'
python research/timetable_validation/scripts/run_linked_pilot.py
python research/timetable_validation/scripts/run_multiday.py
python research/timetable_validation/scripts/run_adaptive.py develop
python research/timetable_validation/scripts/run_adaptive.py evaluate
```

Set `KKL_OUTPUT_DIR` to a new directory for each independent repeat. It defaults to this extension's ignored `local_runs/`. Do not point it at published results. Runners use four worker processes. Development writes `frozen_selection.json`; evaluation then loads that selection. These dates have already been analysed, so a rerun is a reproduction, not a new untouched evaluation. Local outputs include classroom and demand details: do not add those outputs to the public repository.

The initial `timetable_demand.py` CLI is retained as the earlier unlinked prototype. The three comparative runners use `linked_demand.py`, whose independently generated through traffic and class-transfer assumptions differ from that prototype. Do not combine their parameters as though they were one model.

### Missing-headcount sensitivity

The follow-up keeps the original selection and controllers unchanged, varying only the estimated enrolment of eligible sessions without known headcounts. It uses 40%, 60% and 80% of room capacity, five explored dates, three new seeds and C1/C7/D10: 135 simulations. Background remains fixed at the mixed setting; this is not another three-background held-out evaluation.

```powershell
# KKL_DATA_DIR must point to the authorized derived inputs described above.
python research/timetable_validation/scripts/run_headcount_sensitivity.py --output research/timetable_validation/local_runs/headcount_sensitivity_v1
python research/timetable_validation/scripts/report_headcount_sensitivity.py research/timetable_validation/local_runs/headcount_sensitivity_v1
```

The runner refuses an existing output directory and saves its protocol before simulation. Detailed coverage, cohort audits and decision logs stay local; publish only the allowlisted aggregate results. The report checks equal inputs across controls, unchanged attendance for known-count classes, background/through-flow counts and demand conservation. With only three seed-level replicates, intervals describe limited Monte Carlo precision, not model or real-world uncertainty.

The follow-up is complete: **135 additional simulations** (1,740 archived runs in total). At 40%/60%/80%, equal-date/seed mean waiting for C1 is 7.813/8.439/9.344 seconds; D10 is 7.884/8.458/9.395, with all D10−C1 Monte Carlo intervals crossing zero. C7 has higher overall waiting but lower G-origin waiting in each condition. No unserved, incomplete, chain-conflict or late-transfer trips were recorded. This mixed-background finding does not replace the earlier three-background evaluation. See [full result tables](published_results/headcount_sensitivity_v1/结果说明.md), [interpretation and next steps](published_results/headcount_sensitivity_v1/interpretation_zh.md) and [figure](published_results/headcount_sensitivity_v1/headcount_sensitivity.png). Eleven behavioral tests pass, including two new missing-count/selection checks.

### Paired timestep check

The timestep runner compares 0.25 and 0.125 seconds using five explored dates, low/mixed/high backgrounds, three original development seeds, and fixed C1/C7/D10 (270 simulations). It retains identical passenger inputs and decision intervals in physical seconds, checks identical D10 decisions/switch times across grids, and reproduces the 0.25-second results against the development archive. Two grids alone do not prove convergence or establish a real-world accuracy bound.

```powershell
python research/timetable_validation/scripts/run_timestep_sensitivity.py --output research/timetable_validation/local_runs/timestep_sensitivity_v1
python research/timetable_validation/scripts/report_timestep_sensitivity.py research/timetable_validation/local_runs/timestep_sensitivity_v1
```

This runner temporarily changes `config.DT` inside each worker process and restores it even after failure; it does not edit the original simulator or config file. Simulation snapshots, selected classrooms and detailed decision logs remain local. The paired fine-minus-coarse change in a strategy contrast is reported separately from its absolute waiting-time shift. Do not interpret changes in this development-date experiment as a re-evaluation of the prior held-out 0.140-second result.

Completed results: 270 further simulations, bringing the published archive to **2,010 runs**. Overall development-date mean waiting changes from C1/C7/D10 = 8.259/8.715/8.099 seconds at 0.25 seconds to 8.405/8.792/8.268 at 0.125 seconds. D10−C1 changes from −0.161 to −0.137 seconds; its fine-grid three-seed Monte Carlo interval is [−0.277, +0.004]. The paired change in this contrast is +0.0238 seconds with interval [−0.0123, +0.0600], so crossing zero in one interval does not itself demonstrate a significant grid effect. D10 was selected on these development conditions; these are numerical diagnostics, not fresh independent validation.

All 2,970 compared coarse-grid numeric fields match the prior archive exactly; paired demand and D10 decision/switch logs are identical across grids; no service anomalies occurred. Thirteen behavioral tests pass. Absolute waits do shift and small D10−C1 comparisons can reverse sign; convergence is not established. A third grid should be checked before retuning the rule. See [tables](published_results/timestep_sensitivity_v1/结果说明.md), [interpretation](published_results/timestep_sensitivity_v1/interpretation_zh.md) and [figure](published_results/timestep_sensitivity_v1/timestep_sensitivity.png).

### Third-grid extension

`run_third_grid.py` adds only the 0.0625-second runs. It loads the same selected classes and time windows from the **local** two-grid archive, verifies unchanged source/script hashes, and checks the new passenger inputs, demand audits and D10 decision logs against both previous grids. It requires `coverage_private.json` and the local `runs/` logs; aggregate public data alone cannot reconstruct these private inputs.

```powershell
python research/timetable_validation/scripts/run_third_grid.py --prior research/timetable_validation/local_runs/timestep_sensitivity_v1 --output research/timetable_validation/local_runs/timestep_third_grid_v2
python research/timetable_validation/scripts/report_third_grid.py research/timetable_validation/local_runs/timestep_third_grid_v2
```

The report compares both signed aggregate increments and the mean absolute per-input increments, to avoid hiding instability through cancellation. It does not assume a convergence order, perform Richardson extrapolation, or treat the smallest step as ground truth. Prior protocols/results remain unchanged. Three-grid comparisons on already explored development dates are numerical diagnostics, not independent policy validation.

The third grid is complete: **135 new archived runs**, for 2,145 total archived runs, including failure cases. At dt=0.0625, **128/135** runs have service/chain anomalies. Aggregate recorded waiting for C1/C7/D10 is 17.552/18.689/17.576 seconds, but these are failure diagnostics and must not be interpreted as valid policy effect estimates. Inputs, demand audits and D10 decisions match the previous grids. The single-floor reproduction demonstrates a missed-stop loop independently of D10 and timetable demand. Repair the simulator and revalidate before further threshold work.

See [third-grid tables](published_results/timestep_third_grid_v2/结果说明.md), [interpretation](published_results/timestep_third_grid_v2/interpretation_zh.md), [failure-labelled figure](published_results/timestep_third_grid_v2/three_grid_check.png), and [minimal reproduction evidence](published_results/timestep_third_grid_v2/minimal_reproducer.json). Fifteen routine tests pass; the separate diagnostic script intentionally fails on the unmodified simulator. The first third-grid attempt was aborted because native tuple logs and archived JSON lists were compared directly; regression tests reproduced this bookkeeping error and verified the format-only fix. The completed v2 experiment uses unchanged simulator algorithms and separately frozen provenance.

## Results and interpretation

### Current motion-repair check

The candidate repair passed five new motion tests, fifteen existing research tests and the original hand-calculation smoke test. A predeclared 135-run check uses the original seed 4000 on five development dates, three backgrounds, three grids and C1/C7/D10. All input fingerprints, demand audits and D10 decisions match the old archive. The matched fine-grid subset goes from 42/45 anomalous runs to 0/45; all 135 repaired runs have zero recorded service, chain, lateness or event-time anomalies.

```powershell
python research/timetable_validation/scripts/run_repair_validation.py --prior path/to/local/timestep_sensitivity_v1 --output path/to/new/motion_repair_v1
python research/timetable_validation/scripts/reproduce_short_trip_grid_issue.py --output path/to/new/motion_repair_v1/minimal_reproducer.json
python research/timetable_validation/scripts/report_repair_validation.py path/to/new/motion_repair_v1
```

The runner verifies unchanged demand/control code and saves model/script snapshots locally. Only aggregate outputs are published. See [the report](published_results/motion_repair_v1/结果说明.md): D10−C1 is −0.1802/−0.1365/−0.1383 seconds across the three grids. These one-seed development means have no Monte Carlo confidence interval and are not a re-evaluation of the historical result below. Per-input shifts persist, so convergence is not established. The repository now contains 2,145 historical runs plus 135 repaired-model validation runs, kept separate.

### Historical evaluation on the original simulator

| Evaluation background | Fixed C1 | Fixed C7 | Frozen D10 | D10 minus C1 |
|---|---:|---:|---:|---:|
| Low floors | 7.600 | 6.463 | 7.135 | −0.464 |
| Mixed floors | 8.176 | 9.029 | 8.194 | +0.018 |
| High floors | 8.624 | 10.418 | 8.649 | +0.025 |
| Equal-weight overall | 8.133 | 8.637 | 7.993 | −0.140 |

Units: seconds of mean waiting, averaged equally over the fixed dates, scenario variants and seeds. The equal weighting is experimental, not a measured frequency of KKL traffic conditions. C1 uses ETA dispatch and distributed parking; C7 uses ETA dispatch and entrance parking. C0 is a code baseline, not verified actual KKL dispatch.

D10 reduced waiting by about 1.72% against development-selected C1, with paired Monte Carlo 95% interval [−0.217, −0.063] seconds. This interval represents random-seed variability under the model, not real-data uncertainty. The candidate did **not** meet the predeclared 0.25-second improvement screen, which was a research convention, not a course or industry standard. The screen is retained after seeing the results. All evaluation service-completion and chain checks passed; the preceding 675-run study recorded five late transfer trips under C0, so not every earlier experiment was free of lateness.

The six original scenarios reproduced 6,120 compared numerical outputs exactly. That is evidence of reproducibility, not evidence that either simulator matches real KKL traffic.

## Scope and limitations

- The original S1–S6 reproduction retains LG3–16 placeholder settings. The timetable studies use six identical cars serving LG2–16, with capacity 16 and rated speed 2.5 m/s. Highest floor, capacity and speed remain assumptions.
- Source data are the September 2 SIS timetable and October 31 business-school enrolment snapshot. The pipeline has a semester schedule skeleton, but experiments use only eligible classes on selected dates. No whole-semester simulation has been completed.
- Missing counts use 60% of room capacity, attendance defaults to 80%, and unresolved overlaps/floors are excluded. These choices can alter the policy ranking. Capacity is not enrolment; enrolment is not observed attendance.
- Transfer links are synthetic cohort assumptions, not inferred student identities or real course-enrolment links. Independent background and through traffic are assumed, not measured.
- D10 observes past request origins and destinations. This information assumption is stronger than traditional up/down landing calls; no deployment feasibility is established.
- November 2–6 are simulated future timetable dates at the time of the November 1 study. Adjacent weeks share about 85–94% of selected class signatures, so the date split gives limited independence.
- Original E2's forecast/map controller and this extension's D10 past-request rule are different methods with different inputs. Their percentage improvements and selected-window metrics are not directly interchangeable.

See [NEXT_STEPS.md](NEXT_STEPS.md) for the decision-log diagnosis and follow-up priorities. Original archived protocol/script hashes are preserved; [port_provenance.json](port_provenance.json) records the path-only changes used to package the scripts. Historical `heldout_not_evaluated_yet` in the frozen selection records its creation-time state; evaluation is now complete.
