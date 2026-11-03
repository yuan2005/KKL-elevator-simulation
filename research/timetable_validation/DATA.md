# Local input contract and source provenance

The SIS timetable was obtained by the user after university authentication. It and classroom-level derivatives remain local. This extension publishes model code and aggregate simulation results only.

## Sources used in the archived runs

- HKU SIS: `2026-27 class_timetable_20260902.xlsx`, page labelled updated 20260902 14:30. Source page: https://intraweb.hku.hk/reserved_1/sis_student/sis/SIS-class-timetable.html
  - Archived workbook SHA-256: `c2836f8e0b7711c234aa823e9904667ccff947c119d506fdbfd09dd525ec885a`.
  - Semester 1 extraction: 912 KKL schedule rules; 3,792 date-expanded candidates; 335 class numbers; 23 rooms. Candidates are not confirmed actual meetings.
- HKU Business School enrolment snapshot, October 31, 2026; archived file labelled 13:00 while its page displayed 09:00. Source page: https://ug.hkubs.hku.hk/course-enrolment
  - 87 SIS KKL class sections matched snapshot headcounts; 248 lacked a match. A later snapshot does not establish historical daily attendance.
- HKU classroom information, used for documented room floor/capacity mappings: https://www.exam.hku.hk/c_rooms.php

These are recorded source locations and versions from the original study, not claims that the pages were refreshed during packaging. Official source data do not make the behavioural or elevator parameters official.

## Required JSON files

`semester1_kkl_dated_candidates.json`: list of event objects, with:

- `date`: ISO date string; `start_time` and `end_time`: `HH:MM`.
- `class_number`, `course_code`, `class_section`, `room`: class/session identifiers, not student identities.
- `event_id`, `source_rows`: source traceability.
- `flags`: list of review flags. Flags containing `reading_week`, `after_general_teaching_end` or `cross_source_schedule_difference` are excluded by this version.
- `business_headcount_snapshot`: integer count or null.

`room_registry.json`: list of objects with `room`, `floor` (string or null), and `capacity` (integer or null), together with source notes where available. Valid model floor labels are LG2, LG1, G, and 1–16. A missing floor is excluded rather than inferred automatically from its room code.

The selection stage merges adjacent blocks of the same class/room with gaps up to 10 minutes, identifies overlapping sessions before excluding anything, and uses known headcounts or the explicit capacity assumption. The conservation tests check model consistency; they cannot validate the underlying enrolment/room assumptions.

Exact reruns require the same archived derived inputs identified in the published protocols. An independently re-extracted or updated timetable can produce different results. Missing classes remain a coverage gap; the workbook extraction pipeline and authorized source records are retained locally.


## Revised input version: 2026-11-02

Historical simulations use the original `sis_2026-27` registry. The separate local directory `sis_2026-27_reviewed_2026-11-02` changes only the floor/provenance fields for KKLG110 and KKLG111, using [HKU ITS room evidence](room_evidence_2026-11-02.json). Both floors are LG1; capacities remain 45 and 46. Timetable candidates and rules retain their original hashes. The revision manifest and [coverage comparison](published_results/semester_coverage_v1/comparison.json) link the versions. The first revised-input batch is [room_revision_repaired_v1](ROOM_REVISION.md), with 90 runs, followed by the [810-run cross-week comparison](CROSSWEEK.md). The [mixed headcount sensitivity](HEADCOUNT_SENSITIVITY.md) and [low-background check](LOW_HEADCOUNT.md) also use the revised registry and changes missing-headcount assumptions at generation time. Earlier archives continue to use the original registry.

Coverage auditing additionally requires `semester1_kkl_rules.json`, containing the source-row career and offering-department fields. These support aggregate coverage checks but do not uniquely identify each class's programme-specific calendar. The [calendar review](semester_calendar_review.json) records general dates and exceptions; the audit preserves source sessions and labels the ordinary-weekday subset separately.
