# Why the switching gain is small, and what to test next

This is a post-hoc research diagnosis, not a newly validated controller. The original D10 rule, success screen, and evaluation results remain frozen.

**Update after numerical investigation:** a missed-stop loop was confirmed and a candidate motion repair added. Twenty tests and 135 fixed-input repaired runs pass their checks, but this does not revalidate the old effect size or prove convergence. See [repair evidence](published_results/motion_repair_v1/结果说明.md). The immediate priority is model-owner review, then separately archived S1–S6/baseline and original evaluation reruns on the corrected model. The policy-development ideas below remain proposals; do not tune thresholds to the diagnostic outputs.

## New inspection of saved decision logs

`published_results/adaptive_v1/switching_diagnosis.json` is recomputed from 75 saved evaluation runs, using `scripts/diagnose_switching.py`. No simulations or thresholds were changed for this diagnosis.

| Background | D10 minus C1 (s) | D10 minus C7 (s) | Mean switches/run | C7 at logged decisions |
|---|---:|---:|---:|---:|
| Low | −0.464 | +0.672 | 50.44 | 40.35% |
| Mixed | +0.018 | −0.835 | 9.20 | 5.87% |
| High | +0.025 | −1.769 | 4.48 | 2.82% |

The overall −0.140 seconds is the equal-weight average of those three D10−C1 differences. Low-floor demand provides the gain; mixed/high demand provides little mean improvement. The latter small positive differences have intervals crossing zero in the original evaluation and should not be called proven harms.

In low-floor runs, the high-endpoint fraction never exceeds 10%, so changing that threshold alone cannot explain the missed fixed-C7 benefit. At 62.71% of logged decisions, the entrance-origin share is below the 70% entry threshold; at 13.50%, the count is below ten. These failures overlap and cannot be added. Entry conditions pass while the chosen state remains C1 at 10.52% of decisions, consistent with the persistence/dwell gates sometimes delaying entry.

These observations suggest overly intermittent C7 use in this particular low-floor setting. They **do not prove** that each switch is harmful or that each C1 interval should have been C7: elevator states carry over, and the fixed-C7 comparison concerns a different whole-day trajectory. Decision percentages exclude warmup and drain and are not passenger percentages or independent samples.

## Priorities

1. **Check whether the effect survives model uncertainty.** Freeze the existing controls and compare smaller time steps (0.25, 0.125 seconds), capacity/door-time assumptions, missing-headcount occupancy, and background demand. Define ranges before running them and label unsourced ranges as stress tests. A mean improvement below one simulation step is not automatically invalid, but it deserves numerical-convergence checking. More random seeds alone will not fix wrong physical or demand assumptions.
2. **Diagnose time-local opportunity with paired trajectories.** For each identical passenger input, retain per-passenger waits under C1, C7 and D10 and compare common, predeclared windows, G versus other origins, and long waits. Define windows using timetable times or input arrivals, not whichever windows improve the result. Keep whole-day AWT as the original primary metric. Existing aggregate logs cannot establish the causal cost of switching or the best switch time.
3. **Test one controlled rule revision at a time.** On development data, compare the current rule with a longer evidence window or stronger persistence, and separately with timetable-based advance signals. A timetable forecast may use information available beforehand, but must not read future realised arrivals or evaluation-day outcomes. Do not let the controller read the generator's latent `high_share` setting. Reducing switching may improve stability, but may also react too late; its effect must be measured.
4. **Broaden fixed controls before claiming adaptive superiority.** Add C2/C4 under explicitly higher-load conditions. The current C1/C7 comparison does not establish the best fixed policy across the full strategy family. A simple fixed or slowly changing choice may be sufficient.
5. **Use genuinely unused evaluation conditions for revised rules.** November 2–6 has been inspected and is no longer untouched. Choose separate eligible dates and predeclared conditions before evaluating revisions; inspect calendar exceptions and remaining overlap. Retain the original 0.25-second screen for comparisons with this study. A different research objective may be explored separately, but cannot retrospectively turn this result into a pass.

It is acceptable for the final conclusion to be conditional: entrance parking benefits certain low-floor traffic, while this particular adaptive rule provides only a small overall gain. That answers a useful research question without claiming that switching must always win or that real KKL queues have been solved.

## Recompute the diagnosis locally

```sh
python research/timetable_validation/scripts/diagnose_switching.py --runs /path/to/adaptive_v1/evaluate/runs --output /path/to/new_switching_diagnosis.json
```

The input logs remain local. The published diagnosis contains only scenario-level averages.
