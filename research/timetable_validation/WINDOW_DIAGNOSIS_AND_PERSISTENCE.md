# Window diagnosis and one-factor persistence experiment

The motion model remains `dd06745`. The original demand generator, D10 controller and all earlier archives are unchanged. This follow-up first diagnoses existing repaired evaluation outputs, then tests one proposed rule revision on already used development conditions. Evaluation dates used in this diagnosis are now part of development evidence.

## Existing-output diagnosis: no new simulations

All 450 local passenger traces from `adaptive_repaired_evaluation_v1` are hash-checked and paired across C1/C7/D10. The analysis retains every predefined 15-minute arrival window and adds explicitly post-hoc groups for D10 state at request arrival and time since the last switch. Each partition reconstructs total passenger counts and whole-day waiting differences.

At dt=0.125 s, the independent background-traffic layer contributes +0.0667 seconds to the whole-population D10−C1 mean, classroom traffic −0.1622 seconds and through traffic −0.0568 seconds, summing to the −0.1522-second overall result. These additive contributions differ from conditional within-layer mean differences. The background-traffic layer is also distinct from the low/mixed/high background-floor scenario variable.

Most of the background layer's extra waiting is associated with requests arriving while D10 selects entrance parking (C7). This is not a causal switch-cost estimate: demand determines the selected state, a journey may span later switches, and car positions/loads carry over. Requests within 300 seconds after a switch still have a net mean benefit overall. Consequently, the diagnosis does not establish that switching too frequently caused the small benefit.

In low-background scenarios the rule switches 50.44 times per day on average and spends 40.10% of generation time in C7; at 10.52% of decision timestamps the entry conditions pass but the rule remains in C1. Request counts, decision fractions and time shares are different denominators. Gains occur across multiple clock periods, not a single selected favorable window.

See [diagnosis report](published_results/repaired_window_diagnosis_v2/结果说明.md), [all-window figure](published_results/repaired_window_diagnosis_v2/window_contributions.png) and [aggregate summary](published_results/repaired_window_diagnosis_v2/summary.json). The first analysis compared verbose combination names with bare `C7` and incorrectly reported a zero C7 time share. Its failure note is retained; v2 normalizes labels and cross-checks every decision timestamp. Passenger differences and simulation outputs were unaffected.

## Candidate: three confirmations instead of two

The separate `D10P3` controller requires three consecutive target-state decisions before either entering or leaving C7. It otherwise retains h=0.10, entrance thresholds 0.70/0.65, 300-second history, 60-second decisions, minimum count ten, 600-second dwell and initial C1. The original D10 implementation is not edited. A control-setting regression checks that the new gate with two confirmations matches the original gate state for 3,030 sustained/random decision steps.

The protocol is frozen before running **90 new simulations**: five original development dates × three backgrounds × three original seeds × two fixed grids. The 270 C1/C7/D10 baseline rows are reused only after model/source/input pairing checks. Demand audits and all past-observation features match the original D10; the changed gate is allowed to choose different states.

| dt | D10P3−D10 mean wait | 95% three-seed interval | Mean switches D10 → D10P3 |
|---|---:|---|---|
| 0.25 s | −0.0070 s | [−0.1563, +0.1424] s | 20.40 → 17.33 |
| 0.125 s | −0.0005 s | [−0.1045, +0.1034] s | 20.40 → 17.33 |

All six recorded service/chain/lateness/event/motion anomaly totals are zero. Switching falls by about 15%, but neither grid demonstrates a reliable added waiting benefit. **Keep the original D10; D10P3 is not adopted.** It fails the predeclared development screen requiring a negative mean and seed-CI upper bound at both grids, acceptable per-background changes and zero anomalies. It also fails the separately retained original 0.25-second saving screen versus C1. A nonsignificant result with three seeds does not prove equivalence or rule out other persistence designs.

See [candidate report](published_results/persistence_three_develop_v1/结果说明.md), [figure](published_results/persistence_three_develop_v1/persistence_comparison.png), protocol and raw per-run aggregate metrics. The candidate was motivated after inspecting already used evaluation results; it has no independent validation.

## Reproduce and verify

```powershell
python research/timetable_validation/scripts/diagnose_repaired_windows.py --prior path/to/adaptive_repaired_evaluation_v1 --output path/to/repaired_window_diagnosis_v2
python research/timetable_validation/scripts/report_diagnosis_candidate.py diagnosis path/to/repaired_window_diagnosis_v2
python research/timetable_validation/scripts/run_persistence_candidate.py --original-grid path/to/timestep_sensitivity_v1 --baselines path/to/motion_repair_v1 path/to/motion_repair_extra_seeds_v1 --output path/to/persistence_three_develop_v1
python research/timetable_validation/scripts/report_diagnosis_candidate.py candidate path/to/persistence_three_develop_v1
python research/timetable_validation/scripts/check_diagnosis_candidate.py
```

Exact reruns need authorized local detailed archives. Public files contain only aggregate results and hashes; per-passenger records, selected classrooms and per-input detailed diagnostic groups remain local. New output directories are required. The public check independently recalculates paired intervals and the frozen decision from aggregate rows; it does not claim to reopen private traces.

This study added 90 simulations; reused baseline rows and trajectory diagnostics add none. The next [background-intensity check](BACKGROUND_SENSITIVITY.md) is now complete. See [the current handoff](REPORT_HANDOFF.md) for the combined conclusions and archive count.
