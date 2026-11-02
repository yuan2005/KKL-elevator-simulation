# S1–S6 and timetable-informed demand experiments

Current research status, 2026-11-02. This extension builds on the group's simulator and the user's authorized HKU timetable download, with AI-assisted implementation and analysis.

The repaired-model evaluation gives D10 a **0.1309/0.1522-second mean saving versus C1**, below the original 0.25-second benefit screen. Longer persistence reduces switching without a demonstrated waiting benefit. A background-intensity check also gives no evidence for advancing a revised rule. Keep the original D10 as the research reference while the model owner reviews the motion repair.

Start with [report handoff](REPORT_HANDOFF.md), [next work](NEXT_STEPS.md), or [validation and archive inventory](VALIDATION.md).

[Semester coverage review](published_results/semester_coverage_v1/结果说明.md) is now complete. Official floor evidence for KKLG110/111 increases selected class meetings from 2,621 to 2,957 in a separately versioned input set. The [90-run revised-input check](ROOM_REVISION.md) is complete: D10−C1 is +0.0085/+0.0185 seconds with both intervals crossing zero. The planned 810-run multiweek study remains unexecuted. [High-floor evidence](HIGH_FLOOR_EVIDENCE.md) documents mixed uses and the remaining parameter gaps.

## Evidence map

| Study | Unique runs | Evidence |
|---|---:|---|
| Original-model reproduction, demand experiments and numerical diagnosis | 2,145 | Historical `published_results` batches; [inventory](published_results/archive_inventory_room_revision_v1.json) |
| Repaired motion, three grids and three development seeds | 405 | [Combined grid report](published_results/motion_repair_three_seeds_v1/结果说明.md) |
| Repaired S1–S6, six fixed controls | 360 | [Scenario report](published_results/s1_s6_repaired_v2/结果说明.md) |
| Frozen D10 under original evaluation conditions | 450 | [Evaluation report](published_results/adaptive_repaired_evaluation_v1/结果说明.md) |
| Three-confirmation candidate D10P3 | 90 | [Diagnosis and candidate](WINDOW_DIAGNOSIS_AND_PERSISTENCE.md) |
| Background intensity 6/12/24 intents per five minutes | 180 new + 90 reused | [Sensitivity report](BACKGROUND_SENSITIVITY.md) |
| Revised KKLG110/111 classroom input | 90 new + 90 reused | [Room revision](ROOM_REVISION.md) |

Total: **3,720 unique archived runs**, comprising 2,145 original-model and 1,575 repaired-model runs. Diagnostic summaries and reused rows add no runs. Historical failed runs remain in their labelled batches.

## Model and interpretation

The candidate motion model is `dd06745a66eb2c21f3cd2ae6ead75220eb4dbfcc`; later experiments leave its source unchanged. [The repair note](KNOWN_NUMERICAL_ISSUE.md) explains the missed-stop loop and event-time correction. Historical pre-repair research can be reproduced at `419c1b6`.

Original S1–S6 uses LG3–16 placeholder settings. Timetable experiments use six lifts, LG2–16, and partly source-informed demand. Building speed, capacity, floor height, attendance, transfer behaviour and background demand remain assumptions requiring calibration. The timetable spans a semester; completed demand simulations use selected dates and eligible classes. Keep these two experiment families separate.

C1 uses ETA dispatch with distributed parking; C7 uses ETA dispatch with entrance parking and no zoning. D10 switches between them using past request observations. It uses a 300-second history, 60-second decisions, h=0.10, entrance shares 0.70/0.65, a minimum of ten requests, two confirmations and 600-second minimum dwell. Destination availability is an information assumption of this controller. D10P3 changes only the number of confirmations to three and has not been adopted.

## Verify without timetable access

From the repository root, with `requirements.txt` installed:

```sh
python -m unittest discover -s research/timetable_validation/scripts -p "test_*.py"
python -m unittest test_motion
python experiments.py smoke
python research/timetable_validation/scripts/check_current_results.py
```

The current suite has 29 research tests and five motion tests. The public archive check recalculates aggregate comparisons, intervals, input pairing and experiment decisions. The coverage check reconciles session counts and revised-input provenance. Local passenger-trace checks are documented separately in [VALIDATION.md](VALIDATION.md).

## Reproduce experiments

Set `KKL_DATA_DIR` to the authorized derived-input directory described in [DATA.md](DATA.md). Exact paired timetable reruns also require the archived detailed run inputs. The workbook extraction pipeline currently remains local; the public package starts from its JSON data contract.

- Repaired grids, S1–S6 and evaluation: [commands and figures](REPAIRED_FOLLOWUP.md).
- Window diagnosis and persistence candidate: [commands](WINDOW_DIAGNOSIS_AND_PERSISTENCE.md).
- Background intensity: [commands](BACKGROUND_SENSITIVITY.md).
- Minimal motion regression: [command](KNOWN_NUMERICAL_ISSUE.md).

Use a fresh output directory for each new experiment. Published `raw.json` files contain per-simulation aggregates. The SIS workbook, classroom-level derivatives and passenger traces stay local; [DATA.md](DATA.md) records source versions and hashes.
