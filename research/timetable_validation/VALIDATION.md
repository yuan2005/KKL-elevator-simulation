# Packaging validation (2026-11-01)

Validation performed before publishing this extension:

- Nine existing behavioral unit tests passed after path migration.
- Public archive checker verified 1,605 archived runs, the frozen development selection/hash, 75 matched evaluation inputs and the reported D10−C1 delta of −0.140199 seconds.
- The portable S1–S6 runner actually reran 360 simulations: all 6,120 numerical comparisons matched the original repository output exactly; maximum absolute difference was zero. This rerun is separate from the 1,605 archived runs.
- A synthetic two-class integration run exercised the relocated demand generator, simulator and C1/C7/D10 controllers. Each received the same 683 trips; no unserved, incomplete or chain-conflict trips occurred. This verifies integration, not real-data calibration.
- Original-to-packaged hashes were checked against `port_provenance.json`; existing algorithm files differ only in path configuration where recorded.
- Published JSON was checked for classroom/passenger payload fields. Source workbooks, derived classroom records, detailed runtime logs and personal writing drafts were excluded.

The complete 1,245 archived timetable simulations were not rerun during packaging. Their stored metrics were checked, and the migrated runtime was tested with synthetic data. Full timetable reproduction still requires the authorized local derived inputs described in DATA.md.

New outputs go to ignored `local_runs/` by default. The original top-level simulator source files and original experiment results were not edited.
