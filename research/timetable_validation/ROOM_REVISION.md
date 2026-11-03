# Revised classroom input: completed 90-run check

The new registry adds source-supported KKLG110/111 classes. Five original development dates, three seeds, mixed background, C1/C7/D10 and two grids give 90 new runs; the same model's 90 original-registry rows supply the comparison.

| Grid | Revised C1 wait | Revised D10 wait | D10−C1, 95% seed interval |
|---|---:|---:|---|
| 0.25 s | 8.6130 s | 8.6215 s | +0.0085 [−0.0933, +0.1103] s |
| 0.125 s | 8.5646 s | 8.5831 s | +0.0185 [−0.0724, +0.1093] s |

The original-registry differences were +0.0321/+0.0443 seconds. Their changes after adding the rooms are −0.0236 [−0.2116,+0.1645] and −0.0258 [−0.1884,+0.1367] seconds. All intervals cross zero: this check gives no resolved D10 advantage and no resolved change in its advantage due to the registry revision. Keep the current thresholds.

Mean selected meetings/day increase from 44.2 to 49.8; mean lift requests from 4,347.5 to 4,416.3. Independent background/through movements and existing class attendance draws match across versions. Added classes change transfer chains and some classroom movements, so between-version comparisons pair dates/seeds. Within each version, all controllers/grids use the same trips. All six service/chain/lateness/event/motion audit totals are zero.

The [full report](published_results/room_revision_repaired_v1/结果说明.md) and `summary.json` retain both registries, C7 and secondary metrics. Intervals use three seed-level five-day means. The previous 0.131/0.152-second evaluation benefit concerns the original registry across low/mixed/high backgrounds, a different experiment scope.

## Reproduce

```sh
python research/timetable_validation/scripts/run_room_revision.py --original-grid path/to/timestep_sensitivity_v1 --baselines path/to/motion_repair_v1 path/to/motion_repair_extra_seeds_v1 --data-dir path/to/sis_2026-27_reviewed_2026-11-02 --output path/to/new_room_revision
python research/timetable_validation/scripts/report_room_revision.py path/to/new_room_revision
python research/timetable_validation/scripts/check_current_results.py
```

The subsequent [810-run early/middle/late-week comparison](CROSSWEEK.md) is now complete. [High-floor source research](HIGH_FLOOR_EVIDENCE.md) supports mixed office/learning/seminar use but does not supply occupancy or lift-use rates. No new high-floor parameter was inserted into this batch.
