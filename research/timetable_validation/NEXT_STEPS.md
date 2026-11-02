# Next work after the repaired-model experiments

Updated 2026-11-02. Three-grid validation, S1–S6 fixed controls, frozen D10 re-evaluation, all-window diagnosis, a persistence candidate and background-intensity sensitivity are complete. Their results are indexed in [the handoff](REPORT_HANDOFF.md).

## 1. Finish the model review and Interim Log

The model owner should review the phase integration in `building.py` and arrival/boarding/alighting/dwell timing in `sim.py`, using the short-trip regression and five motion tests. If review changes model behaviour, record a new version and repeat the affected validation and paired comparisons. Otherwise, retain the current results.

For the November 4 Interim Log, use the repaired-model figures and explain the contribution: timetable-based demand construction, reproducibility checks, numerical fault diagnosis, and evaluation of fixed/dynamic policies. The present conclusion is that the tested dynamic rule's mean benefit is below the original target.

## 2. Resolve the inputs that would change the experiment

Prepare an evidence table for the six lifts' served floors, capacity, speed and door timing; for headcount coverage; and for background/through-traffic rates. Each row needs a source date, units and an explicit link to the parameter it supports. Seek available official building/lift specifications, published measurements or enrolment records first. A timetable constrains class times; it does not identify lift use or unrelated background traffic.

[Semester coverage](published_results/semester_coverage_v1/结果说明.md) is complete. The original registry selects 2,621 meetings; two official floor corrections raise this to 2,957. The revised input is separately versioned. Remaining gaps include overlapping classes, unsupported high-floor rooms, missing headcounts and programme calendar exceptions. The current source-informed class layer covers LG1/1/2 only.

## 3. Check the revised input, then extend across weeks

First run the planned 90 mixed-background comparisons on October 26–30, seeds 4000–4002, C1/C7/D10 and both original grids. Compare revised-registry policy contrasts with the 90 archived original-registry rows, keeping physical and controller settings fixed. Source-supported classroom additions also alter generated transfer chains, so pair by date/seed rather than treating all passenger records as unchanged.

Next consider September 21–25, October 5–9 and November 16–20: 810 planned runs across three backgrounds, three seeds, three controls and two grids. These dates avoid the general calendar exceptions; programme-specific confirmation remains separate. Most session signatures repeat those in already studied weeks, so this tests cross-date robustness. Reserve November 23–27 for later work. Both stages are prepared, not run; freeze execution protocols before starting. Details are in [the proposed plan](published_results/semester_coverage_v1/next_experiment_plan.json).

## 4. Physical assumptions and future controller work

The background check held the floor distribution at mixed and found no resolved D10 advantage at any tested intensity. Additional random seeds would sharpen the uncertainty within those assumptions; they would not identify the actual KKL rate. Prioritize a physical-parameter check when a credible capacity/speed/door-time value or range becomes available. Freeze the supported range, outcome measures and paired inputs before running it.

If the research question becomes robustness across a semester, run unchanged C1/C7/D10 on calendar-checked weeks with fixed assumptions. If it becomes comparison with the best fixed strategy under timetable demand, add the relevant fixed controls to that same setting; the existing S1–S6 ranking uses a different building/input configuration.

A further controller revision needs a specific mechanism and a development screen. Dates November 2–6 have already informed diagnosis; a revised rule needs separate, unused evaluation conditions. Original D10 and its 0.25-second screen remain the reference.

## Decision from the latest checks

- D10P3 reduced mean switches from 20.40 to 17.33; its waiting-time intervals crossed zero at both grids. Keep D10 unchanged.
- Background rates 6/12/24 produced D10−C1 differences from −0.0132 to +0.0443 seconds, all with seed intervals crossing zero. Use the observed intensity dependence of absolute waits to motivate calibration.
- Reporting and source verification are now more useful than another unconstrained threshold search.
