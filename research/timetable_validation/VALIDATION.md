# Validation status — 2026-11-02

## Current checks

| Check | Coverage |
|---|---|
| 29 research tests | Demand conservation and overlap handling, reproducible inputs, transfer chains, past-only policy decisions, archive/statistics helpers, diagnostic label normalization, persistence equivalence, background-layer isolation and semester calendar/count reconciliation and demand isolation under room additions |
| Five motion tests | Both directions and trip lengths, analytic flight times, speed/acceleration, reachable versus late new calls and seeded multi-passenger motion |
| Original smoke | Single-passenger waiting/travel hand calculation |
| Public archive checks | Recompute aggregate claims, seed intervals, fixed screens, input hashes, reused rows and publication hashes |
| Local detailed checks | Passenger-trace hashes, partition counts and weighted waits, complete window/state groups; detailed inputs remain local |

Run from the repository root:

```sh
python -m unittest discover -s research/timetable_validation/scripts -p "test_*.py"
python -m unittest test_motion
python experiments.py smoke
python research/timetable_validation/scripts/check_current_results.py
```

The current command calls the older milestone and coverage checks, then checks the room-revision study and latest inventory. Numbers printed by an older milestone describe that milestone; the final inventory gives the current total.

The separate semester-coverage command checks the original/revised registry summaries, source hashes and planned run counts. Coverage analysis adds zero simulations. Its original plan is preserved; the 90-run stage is now complete in a separate archive, while the 810-run stage remains unexecuted.

## Archived experiments

The [machine-readable inventory](published_results/archive_inventory_room_revision_v1.json) counts **3,720 unique runs**: 2,145 original-model and 1,575 repaired-model runs. The latter comprise 405 grid checks, 360 S1–S6 runs, 450 original-evaluation runs, 90 D10P3 candidate runs, 180 background-intensity runs and 90 room-revision runs. Reused rows, combined summaries and incomplete archival attempts add none.

The original fine-grid archive includes 128 anomalous runs out of 135. On identical inputs the repaired batch records none. All repaired batches report zero recorded service/chain/lateness/event anomalies. Motion-constraint instrumentation additionally covers repaired S1–S6, evaluation, persistence, background and room-revision batches. The zero counts concern these implemented checks and input sets; the outer discrete loop still affects waiting metrics.

## Provenance and reproduction boundaries

Protocols freeze source and script hashes before execution. Public JSON preserves archived bytes. The [checkout provenance](published_results/motion_repair_v1/source_checkout_provenance.json) reconciles Windows CRLF execution hashes with Git LF source hashes. `background_rate_repaired_v1/publication.json` additionally checks its released aggregates and analysis scripts.

Exact timetable reruns require the authorized derived inputs and detailed archived pairing inputs described in [DATA.md](DATA.md). Public checks recompute the released aggregate evidence; they do not reopen private passenger traces. [The repaired follow-up](REPAIRED_FOLLOWUP.md) gives the local trace-check commands.

Source calibration and model-owner review are the remaining evidence tasks. Numerical checks establish consistency of this implementation; the empirical adequacy of the KKL assumptions needs building and demand data.
