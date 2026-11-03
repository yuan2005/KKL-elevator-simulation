# Public schedule evidence and the next input check

Updated 2026-11-03. The input audit below records the preparation. The 486-run experiment is now complete; see [date-correction results](SCHEDULE_REVISION.md). Prepared audit hashes remain unchanged as provenance.

## AILT9018 uses distinct dates for the paired subclasses

The [official course schedule](https://ailt9018.engg.hku.hk/#/schedule) lists separate tutorial dates for A1/B1, A2/B2 and A3/B3. Each subclass has five listed tutorials; each A/B pair uses the same room and weekly time but has no shared tutorial dates. These are date-specific module schedules, not evidence that both subclasses attend together every week. The course-wide quota on the homepage is not a measured subclass headcount.

The schedule data were independently extracted from the JavaScript asset referenced by the live homepage, `https://ailt9018.engg.hku.hk/assets/index-CKywM8pk.js`, on November 3. This corrected an incomplete browser summary that omitted one A2 date. The snapshot supplies current published scheduling evidence; it does not establish historical attendance.

Reconciliation with the revised SIS input identifies 30 KKL tutorials, all currently excluded as unresolved shared-room sessions. Nine fall within the three studied teaching weeks. An in-memory date filter raises semester selection from 2,957 to 2,987 meetings, removes no previously selected meeting, and introduces no selected meeting outside the six target subclasses. All 30 still lack a matched enrolment count.

## Preserve the pairing when revising inputs

Simply deleting unsupported weekly candidates renumbers 921 retained selected-session identifiers across the semester. These identifiers seed attendance draws and passenger chains. A naive rerun would therefore mix schedule changes with unrelated random changes.

The prepared comparison retains original merged-session identities and the original time windows. All 81 affected date/background/seed input checks passed: original fingerprints reproduce the archive, attendance draws for retained sessions match, and independent background/through movements match. New tutorials can change class-transfer chains, so those changes are part of the schedule intervention. The [aggregate preparation audit](published_results/public_schedule_review_v1/audit.json) records counts, provenance hashes and the prepared status.

The proposed experiment covers the nine affected dates, three existing seeds, three background variants, C1/C7/D10 and both existing time steps: **486 new simulations**, compared with the matching **486 archived baseline rows**. The other six dates' 324 rows can be reused to reconstruct a complete 15-date revised comparison. Freeze the model, D10 thresholds and the 60%-of-capacity missing-headcount assumption. Report changes in D10−C1 and D10−C7, all background/week groups, waiting distributions and service/physical checks. This evaluates a schedule correction; it is not new threshold selection.

The experiment was prepared at the 5,250-run milestone and subsequently completed in a separate batch. Existing results and their source snapshots remain unchanged. The November 23–27 reserve has not been simulated.

## Other review items

- [Civil Engineering's first-semester timetable](https://www.civil.hku.hk/civil_intranet/beng/pdf/26-27/BEng_timetable_2026-27_Sem1.pdf) groups CIVL1117 A–F together in KK102. It is explicitly a draft printed July 31, 2026. This supports common teaching but does not settle the current schedule or whether enrolment figures can be added.
- The [Statistics timetable](https://saasresearch.hku.hk/share/student/u2026/2026-27_timetable_1st_Semester.pdf), dated September 18, lists STAT6009/SDST7609/STAT7609 together in KKLG102. Counts and programme-specific exceptions remain unresolved. Attempts to retrieve the linked MStat/MDASC schedule pages returned proxy errors; those pages were not verified.
- No verified current COMP3355 headcount was obtained in the targeted public search. SIS or course-office aggregate counts remain the next source. Failure to find a public number is not evidence of zero enrolment.

The current input parser labels every non-null count as the old business-school snapshot. New SIS or course-office counts need explicit source/date fields before integration. Do not insert a new number under the old provenance label, add potentially overlapping enrolment counts, or alter the frozen historical files.
