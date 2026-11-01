# Reproducible missed-stop loop at the 0.0625-second grid

Status: confirmed on the historical simulator, then repaired on this branch. The original third-grid experiment below used unchanged source; its evidence remains intact. This is a simulator numerical/target-selection defect, not a demonstrated defect in real KKL dispatch or a reason to adjust the D10 threshold.

## Candidate repair and bounded validation

**Follow-up completed 2026-11-02:** the same candidate now has 405 three-grid development runs, 360 original-setting S1–S6 runs and 450 frozen-rule evaluation runs. All recorded completion/event checks pass; the latter two batches also instrument motion constraints. The matched fine-grid anomaly count is now 128/135 original versus 0/135 repaired. D10's re-evaluated saving remains small and fails the original benefit screen. See [follow-up evidence](REPAIRED_FOLLOWUP.md). The earlier 135-run account below remains the first repair milestone, not the latest total; review and real calibration are still pending.

`building.py` now integrates acceleration, cruise and braking phase boundaries within each tick, using the same physical stopping-distance constraint as new-call acceptance. It does not force acceptance of genuinely unreachable new calls. `sim.py` carries the within-tick arrival time into boarding/alighting timestamps and prevents early completion before scheduled alighting. The outer arrival/dispatch/door/control loop remains discretized.

Five new motion tests cover short and long journeys in both directions, analytic flight times, acceleration/speed constraints, reachable versus late new calls, and a seeded multi-passenger workload. The fifteen research tests and original hand-calculation smoke test also pass.

The unchanged one-passenger diagnostic now completes at all three grids: alighting at 13.244733 s, simulation ending at 13.25 s, no target drops. A separate, predeclared 135-run check uses five explored dates, three backgrounds, original seed 4000, three grids and C1/C7/D10. Passenger inputs, demand audits and D10 logs match the original archive. On the identical 45 fine-grid runs, service/chain anomalies fall from 42 runs to zero; all 135 repaired runs have zero recorded service, transfer-chain, lateness or event-time anomalies.

See [repair results](published_results/motion_repair_v1/结果说明.md). This is a one-seed repair check, not an independent policy evaluation or proof of convergence. Per-input waiting and contrast shifts remain; do not reuse the old 0.140-second effect as a validated repaired-model result.

## Minimal reproduction (no timetable data)

```powershell
python research/timetable_validation/scripts/reproduce_short_trip_grid_issue.py --output research/timetable_validation/local_runs/single_floor_issue.json
```

On historical commit `419c1b6`, this diagnostic exits **1**, indicating an incomplete trip. On this repaired branch it exits **0**. It is separate from unittest discovery and records source hashes in new outputs. The table below describes the preserved original-model evidence.

One car starts at LG2, with C0 nearest-car dispatch and no parking; one passenger arrives at time zero and wants LG1, one 3.6 m floor above. The simulation has a 10-second generation horizon and the original 900-second drain limit.

| dt (s) | Boarding time (s) | Recorded alighting time (s) | Result |
|---|---:|---:|---|
| 0.25 | 3.2 | 12.95 | Alighting scheduled |
| 0.125 | 3.2 | 13.2 | Alighting scheduled |
| 0.0625 | 3.2 | missing | Still onboard when simulation reaches 910 s |

The base model records alighting timestamps ahead of the simulation clock when a stop is entered, so the coarse runs can terminate before those timestamps. That is a separate existing event-accounting simplification; the fine-grid failure is stronger: the passenger never gets an alighting timestamp at all and remains onboard.

## Directly observed mechanism

At t=8.1875 s in the fine-grid case, the car is travelling upward toward LG1:

- Position 1.876953125 m; speed 1.9375 m/s.
- Remaining distance 1.723046875 m.
- Stopping distance using the model's acceleration 1 m/s² is 1.876953125 m.
- `Car.can_stop_at()` returns false; `Sim.next_target()` drops the requested floor and returns no requested target.
- The fallback selects another stoppable floor. The same requested-floor rejection recurs at t=13.625, 19.0625, 24.5, 29.9375, etc., while the passenger stays onboard.

The trace also reproduced this behaviour in a 15-minute synthetic background-only case with C7. That case had 43 trips; one passenger from 3/F to LG1 boarded at 609.7625 s and remained onboard at the 1800 s stop limit. C1 and D10 completed all trips in that small diagnostic. The one-car C0 example shows the problem does not require parking, policy switching, class transfers or private timetable data.

## Code-based explanation to verify when repairing

`building.py` tests whether a floor can still be reached under the stopping-distance rule with a one-step allowance. `sim.py` reselects the target on every moving tick, but `move()` uses a different half-step anticipation for the braking transition. These two numerical decisions can disagree: after an acceleration step, an already selected short-distance destination can become classified as unreachable. Repeated target replacement then produces the observed loop.

This explanation is supported by the concrete rejection trace and the code paths; a repair still needs validation. Do not simply suppress the reachability test or force all targets to remain reachable, since genuinely late new calls may be physically impossible to serve safely within the model's acceleration constraint.

## Consequences and next work

- The smallest grid cannot be used as numerical ground truth. Neither three-grid agreement in a few means nor a single long-distance smoke test establishes convergence.
- Keep all predefined experiment rows, including service anomalies; do not remove failed runs to improve policy comparisons.
- Waiting-time metrics alone can hide the issue because a passenger who boards but never alights already has a finite waiting time.
- Preserve the historical results as reproducible outputs of the original simulator. They should not be presented as numerically validated effect sizes; re-evaluate relevant policy claims after fixing and checking the simulator.
- First add a repair regression for completed single-floor journeys, both directions and multiple floor distances/grids, with appropriate physical travel-time checks. Check consistent treatment of a committed destination versus a new request, braking constraints and event times. Then rerun paired fixed/dynamic comparisons on the corrected model in a separate archive.

The historical third-grid protocol, input demands and thresholds remain frozen. Review the candidate repair and broaden baseline revalidation before threshold tuning.
