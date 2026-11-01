# S1–S6 reproduction and timetable-informed demand experiments

2026-11-01 research extension, based on simulator commit `f6c9ca4af440cff4a258237e1214c8dc0266fb50`. The original simulator and its E0–E3 results are unchanged. This extension was developed with AI assistance; it is a reproducible exploration, not a calibrated model of actual KKL operations.

## What is included

- Portable demand and experiment scripts, with nine tests covering demand conservation, overlap exclusion, reproducibility, transfer chains and past-only switching.
- Published **per-run aggregate metrics**, summaries and original protocol hashes for 1,605 completed simulations: 360 S1–S6 reproductions, 120 single-day comparisons, 675 five-day sensitivity runs, and 225 development + 225 evaluation runs for a switching candidate.
- A post-hoc diagnosis of the frozen switching candidate, clearly separated from its original evaluation.

No authenticated SIS workbook, classroom-level derived timetable, individual passenger trace, credentials, or personal progress-log draft is included. The historical protocols refer to locally retained inputs by hash. Public result files named `raw.json` contain per-simulation metrics, **not raw timetable or passenger records**.

## Run checks without timetable access

From the repository root, use Python with the packages in the root `requirements.txt`:

```sh
python -m unittest discover -s research/timetable_validation/scripts -p "test_*.py"
python research/timetable_validation/scripts/check_published_results.py
python research/timetable_validation/scripts/reproduce_s1_s6.py
```

The first two commands check nine behavioral tests and archived result claims. The third actually reruns 360 S1–S6 simulations against the existing `results/e1_raw.csv`; it writes into ignored `local_runs/`, never into `published_results/`. A completed output is protected against accidental overwrite. These checks do not establish real-world calibration.

## Timetable experiments with local authorized inputs

Point `KKL_DATA_DIR` at a local directory containing `semester1_kkl_dated_candidates.json` and `room_registry.json`. The data contract and provenance are in [DATA.md](DATA.md). These files were generated separately from the user's official source downloads; this package does not currently include the workbook extraction pipeline. Without them, tests and S1–S6 reproduction work, but exact timetable reruns do not.

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

## Results and interpretation

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
