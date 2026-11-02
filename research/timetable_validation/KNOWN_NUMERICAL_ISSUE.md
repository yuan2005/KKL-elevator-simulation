# Missed-stop loop: evidence, repair and regression

The original simulator can leave one passenger onboard indefinitely after missing a reachable adjacent-floor destination. The candidate model `dd06745` repairs motion integration and event timing. Model-owner review is pending in PR #2.

## Minimal case

A single C0 car starts at LG2; one passenger requests LG1, 3.6 m above. Generation ends at 10 seconds and the original drain limit is 900 seconds.

| Model/grid | Recorded outcome |
|---|---|
| Original, dt=0.25 s | Alighting timestamp 12.95 s |
| Original, dt=0.125 s | Alighting timestamp 13.2 s |
| Original, dt=0.0625 s | Still onboard at 910 s; no alighting timestamp |
| Repaired, all three grids | Alighting 13.244733 s; simulation ends at 13.25 s |

At t=8.1875 s in the failing case, speed is 1.9375 m/s and remaining distance is 1.723046875 m. With acceleration 1 m/s², stopping distance is 1.876953125 m. The reachability check rejects LG1 and target selection replaces it. The same rejection recurs while the passenger stays onboard.

The old braking transition and stop-acceptance rule use different numerical allowances. An integration step can therefore make an already chosen short destination unreachable. A separate event-accounting issue permits completion before a scheduled alighting timestamp. Waiting time alone misses the first problem because boarded passengers already have finite waits.

## Repair

`building.py` integrates acceleration, cruise and braking phase boundaries within each tick and uses physical stopping distance for new-call acceptance. `sim.py` propagates the within-tick arrival time into boarding/alighting and waits for scheduled alighting before completion. Arrival generation, dispatch, door and control updates remain on the outer discrete loop.

Five motion tests cover analytic travel times, both directions and distances, speed/acceleration constraints, late versus reachable calls and a seeded workload. The original hand-calculation smoke also passes. Review should focus on phase boundaries, acceptance of new calls and arrival/boarding/alighting/dwell timing.

## Validation

The repaired three-grid study comprises 405 runs. On the same 135 fine-grid inputs, the original 128 anomalous runs fall to zero. S1–S6, frozen-rule evaluation, persistence, background sensitivity, room revision and cross-week comparison extend the check to 2,385 repaired-model runs, with zero recorded service/chain/lateness/event anomalies. The later scenario, evaluation, persistence, background, room-revision and cross-week batches also check motion constraints.

Absolute waiting values and individual contrasts still shift with grid refinement. The current evidence supports removal of the reproduced fault; numerical convergence remains a separate question. See [validation inventory](VALIDATION.md) and [paired repair results](REPAIRED_FOLLOWUP.md).

## Reproduce without a timetable

```sh
python research/timetable_validation/scripts/reproduce_short_trip_grid_issue.py --output research/timetable_validation/local_runs/single_floor_issue.json
```

The diagnostic exits 1 at historical commit `419c1b6` and exits 0 on the repaired branch. Original trace, protocol and failure results remain archived. Use a fresh output file when comparing versions.
