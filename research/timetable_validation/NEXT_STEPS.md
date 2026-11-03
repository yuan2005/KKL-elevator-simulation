# Next work after the repaired-model experiments

Updated 2026-11-03. Three-grid validation, S1–S6 fixed controls, frozen D10 re-evaluation, all-window diagnosis, a persistence candidate and background-intensity sensitivity are complete. Their results are indexed in [the handoff](REPORT_HANDOFF.md).

## 1. Finish the model review and Interim Log

The [486-run tutorial-date correction](SCHEDULE_REVISION.md) is now complete. Savings versus C1 remain below target at 0.1348/0.1223 seconds. Prioritize source-dated headcounts and model review; the preparation below is retained to explain this completed intervention.

The model owner should review the phase integration in `building.py` and arrival/boarding/alighting/dwell timing in `sim.py`, using the short-trip regression and five motion tests. If review changes model behaviour, record a new version and repeat the affected validation and paired comparisons. Otherwise, retain the current results.

For the November 4 Interim Log, use the repaired-model figures and explain the contribution: timetable-based demand construction, reproducibility checks, numerical fault diagnosis, and evaluation of fixed/dynamic policies. The present conclusion is that the tested dynamic rule's mean benefit is below the original target.

## 2. Resolve the inputs that would change the experiment

The [public schedule audit](PUBLIC_SCHEDULE_REVIEW.md) now supplies a specific next intervention: AILT9018's paired subclasses use disjoint tutorial dates. Thirty currently excluded KKL meetings can be recovered under the current capacity assumption; nine affect the existing cross-week dates. Prepare the fixed-rule schedule correction comparison described there, preserving original session identities to avoid unrelated changes in random draws. The 486 simulations are complete; see SCHEDULE_REVISION.md for results.

Prepare an evidence table for the six lifts' served floors, capacity, speed and door timing; for headcount coverage; and for background/through-traffic rates. Each row needs a source date, units and an explicit link to the parameter it supports. Seek available official building/lift specifications, published measurements or enrolment records first. A timetable constrains class times; it does not identify lift use or unrelated background traffic.

[Semester coverage](published_results/semester_coverage_v1/结果说明.md) is complete. The original registry selects 2,621 meetings; two official floor corrections raise this to 2,957. The revised input is separately versioned. Remaining gaps include overlapping classes, unsupported high-floor rooms, missing headcounts and programme calendar exceptions. The current source-informed class layer covers LG1/1/2 only.

## 3. Use the completed cross-week evidence

The 90-run revised-input check and [810-run cross-week comparison](CROSSWEEK.md) are complete. Keep the original thresholds and report both fixed-control comparisons, including each background/week. Most class signatures repeat; this extends cross-date coverage under the model assumptions.

The [40/60/80% missing-headcount check](HEADCOUNT_SENSITIVITY.md) is now complete on the repaired model and revised registry: 180 new runs plus 90 reused baselines. It holds the October 26–30 mixed development conditions fixed. All changes in D10−C1 relative to 60% have intervals crossing zero; keep thresholds unchanged.

The [low-background headcount check](LOW_HEADCOUNT.md) is now complete: 540 new runs and 270 reused baselines across the three previously studied weeks. It preserves all model and controller settings. High-background and cross-week mixed-background headcount interactions are not covered by this batch.

Finish the Interim Log and model review, then resolve the headcount/shared-class items. Do not continue adding synthetic parameter combinations by default: identify which unresolved empirical quantity would change the report's claim, obtain a credible value or range, and design the corresponding check. Existing assumptions and the model owner's review remain the next dependencies.

November 23–27 remains unused for a later, specifically motivated evaluation. Model review and the Interim Log take priority before another parameter sweep.

## 4. Physical assumptions and future controller work

The background check held the floor distribution at mixed and found no resolved D10 advantage at any tested intensity. Additional random seeds would sharpen the uncertainty within those assumptions; they would not identify the actual KKL rate. Prioritize a physical-parameter check when a credible capacity/speed/door-time value or range becomes available. Freeze the supported range, outcome measures and paired inputs before running it.

If the research question becomes robustness across a semester, use the completed unchanged C1/C7/D10 calendar-checked comparison. If it becomes comparison with the best fixed strategy under timetable demand, add the relevant fixed controls to that same setting; the existing S1–S6 ranking uses a different building/input configuration.

A further controller revision needs a specific mechanism and a development screen. Dates November 2–6 have already informed diagnosis; a revised rule needs separate, unused evaluation conditions. Original D10 and its 0.25-second screen remain the reference.

## Decision from the latest checks

- Low-background headcount sensitivity retains fixed C7's advantage over D10 in all 18 week/fraction/grid intervals. Overall D10 beats C1 by 0.4166–0.5064 seconds but loses to C7 by 0.7057–0.8156 seconds. This input has no 4/F-or-higher requests and cannot identify h; do not retune that threshold from these runs.
- Repaired missing-headcount sensitivity changes absolute waiting but gives no resolved change in D10−C1 relative to 60% in mixed development conditions. A 0.0128-second fine-grid advantage at 40% does not persist at the coarser grid.
- The cross-week comparison gives D10 an overall 0.1376/0.1251-second saving versus C1, below the target. In low backgrounds fixed C7 is better than D10; mixed/high D10−C1 intervals cross zero when averaged across weeks. Prioritize the empirical background distribution and missing-headcount checks over threshold retuning.
- D10P3 reduced mean switches from 20.40 to 17.33; its waiting-time intervals crossed zero at both grids. Keep D10 unchanged.
- Background rates 6/12/24 produced D10−C1 differences from −0.0132 to +0.0443 seconds, all with seed intervals crossing zero. Use the observed intensity dependence of absolute waits to motivate calibration.
- Reporting and source verification are now more useful than another unconstrained threshold search.

[High-floor source review](HIGH_FLOOR_EVIDENCE.md) confirms office, lab and teaching/seminar uses. Office prevalence, room capacity and traffic intensity are distinct evidence gaps; the source review has not changed high_share or actual demand rates.
