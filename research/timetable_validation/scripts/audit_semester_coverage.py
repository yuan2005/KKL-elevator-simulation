"""Audit timetable coverage without modifying demand selection or running simulations."""
import argparse
from collections import Counter, defaultdict
from datetime import date, timedelta
import hashlib
import json
from pathlib import Path

from timetable_demand import sessions_for_day, select_sessions
from study_paths import ROOT


def load(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def save(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8', newline='\n')


def calendar_flags(day, calendar):
    d = date.fromisoformat(day)
    flags = []
    if day < calendar['general_teaching_start']: flags.append('before_general_teaching')
    if day > calendar['general_teaching_end']: flags.append('after_general_teaching')
    if calendar['general_reading_start'] <= day <= calendar['general_reading_end']: flags.append('general_reading_week')
    if d.weekday() >= 5: flags.append('weekend')
    if day in calendar['public_holidays_in_source_period'] or d.weekday() == 6: flags.append('general_holiday')
    return flags


def merge_counters(rows, field):
    result = Counter()
    for row in rows: result.update(row[field])
    return dict(sorted(result.items()))


NUMERIC = ['candidate_events', 'merged_sessions', 'selected_sessions', 'excluded_sessions',
           'selected_known_headcount', 'selected_assumed_headcount', 'selected_headcount_sum',
           'assumed_headcount_sum', 'headcount_above_capacity', 'ordinary_selected_sessions',
           'ordinary_selected_known', 'ordinary_selected_assumed']


def analyze(events, rooms, rules, calendar):
    byday = defaultdict(list)
    for e in events: byday[e['date']].append(e)
    rulemap = {r['source_row']: r for r in rules}
    assert len(rulemap) == len(rules)
    daily = []; private = []; selected_all = []
    first, last = min(byday), max(byday)
    d = date.fromisoformat(first)
    while d.isoformat() <= last:
        day = d.isoformat(); es = byday[day]
        sessions = sessions_for_day(es, day)
        selected, excluded = select_sessions(sessions, rooms, 'capacity_assumption')
        assert len(selected)+len(excluded) == len(sessions)
        assert sum(len(s['source_events']) for s in sessions) == len(es)
        info = {}
        for s in sessions:
            careers = sorted({rulemap[n]['career'] for n in s['source_rows']})
            info[s['session_id']] = dict(careers=careers,
                departments=sorted({rulemap[n]['offering_department'] for n in s['source_rows']}))
        flags = calendar_flags(day, calendar)
        known = [s for s in selected if s['headcount_snapshot'] is not None]
        assumed = [s for s in selected if s['headcount_snapshot'] is None]
        row = dict(date=day, week_start=(d-timedelta(days=d.weekday())).isoformat(), weekday=d.weekday(),
            calendar_flags=flags, candidate_events=len(es), merged_sessions=len(sessions),
            selected_sessions=len(selected), excluded_sessions=len(excluded),
            selected_known_headcount=len(known), selected_assumed_headcount=len(assumed),
            selected_headcount_sum=sum(s['headcount'] for s in selected),
            assumed_headcount_sum=sum(s['headcount'] for s in assumed),
            headcount_above_capacity=sum(s['headcount_exceeds_capacity'] for s in selected),
            ordinary_selected_sessions=0 if flags else len(selected),
            ordinary_selected_known=0 if flags else len(known), ordinary_selected_assumed=0 if flags else len(assumed),
            exclusion_reasons=dict(Counter(r for e in excluded for r in e['reasons'])),
            exclusion_combinations=dict(Counter('|'.join(sorted(e['reasons'])) for e in excluded)),
            selected_careers=dict(Counter('+'.join(info[s['session_id']]['careers']) for s in selected)))
        daily.append(row)
        private.append(dict(date=day, calendar_flags=flags, sessions=sessions, selected=selected, excluded=excluded, metadata=info))
        selected_all.extend(selected); d += timedelta(days=1)
    weekly=[]
    for week in sorted({r['week_start'] for r in daily}):
        part=[r for r in daily if r['week_start']==week]
        weekly.append(dict(week_start=week, **{k:sum(r[k] for r in part) for k in NUMERIC},
            candidate_days=sum(r['candidate_events']>0 for r in part),
            ordinary_covered_days=sum(r['ordinary_selected_sessions']>0 for r in part),
            calendar_flag_days=dict(Counter(f for r in part for f in r['calendar_flags'])),
            exclusion_reasons=merge_counters(part,'exclusion_reasons'),
            exclusion_combinations=merge_counters(part,'exclusion_combinations'),
            selected_careers=merge_counters(part,'selected_careers')))
    totals={k:sum(r[k] for r in daily) for k in NUMERIC}
    totals.update(first_candidate_date=first,last_candidate_date=last,candidate_days=sum(r['candidate_events']>0 for r in daily),
        distinct_source_classes=len({e['class_number'] for e in events}),
        distinct_selected_classes=len({s['class_number'] for s in selected_all}),
        exclusion_reasons=merge_counters(daily,'exclusion_reasons'),
        exclusion_combinations=merge_counters(daily,'exclusion_combinations'),selected_careers=merge_counters(daily,'selected_careers'))
    assert totals['candidate_events']==len(events)
    assert sum(totals['exclusion_combinations'].values())==totals['excluded_sessions']
    assert totals['selected_known_headcount']+totals['selected_assumed_headcount']==totals['selected_sessions']
    for k in NUMERIC: assert sum(r[k] for r in weekly)==totals[k]
    reviews=[]
    for room in sorted(rooms):
        sessions=[s for day in private for s in day['sessions'] if s['room']==room]
        exclusions=[s for day in private for s in day['excluded'] if s['room']==room]
        reviews.append(dict(room=room, floor=rooms[room]['floor'],capacity=rooms[room]['capacity'],
            merged_sessions=len(sessions),excluded_sessions=len(exclusions),
            exclusion_reasons=dict(Counter(r for e in exclusions for r in e['reasons']))))
    classes=[]
    for number in sorted({s['class_number'] for s in selected_all}):
        ss=[s for s in selected_all if s['class_number']==number]
        if ss[0]['headcount_snapshot'] is None:
            classes.append(dict(class_number=number,course_code=ss[0]['course_code'],class_section=ss[0]['class_section'],
                rooms=sorted({s['room'] for s in ss}),selected_sessions=len(ss),
                assumed_headcount_sum=sum(s['headcount'] for s in ss)))
    classes.sort(key=lambda r:(-r['assumed_headcount_sum'],r['class_number']))
    return dict(totals=totals,weekly=weekly,daily=daily),dict(days=private,room_review=reviews,missing_headcount_priority=classes)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data-dir',required=True,type=Path)
    ap.add_argument('--calendar',type=Path,default=ROOT/'semester_calendar_review.json')
    ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
    files=['semester1_kkl_dated_candidates.json','room_registry.json','semester1_kkl_rules.json']
    hashes={n:sha(a.data_dir/n) for n in files}
    a.output.mkdir(parents=True,exist_ok=False)
    events,roomrows,rules=[load(a.data_dir/n) for n in files]
    public,private=analyze(events,{r['room']:r for r in roomrows},rules,load(a.calendar))
    public['provenance']=dict(source_hashes=hashes,calendar_sha256=sha(a.calendar),
        scripts={n:sha(Path(__file__).with_name(n)) for n in ['audit_semester_coverage.py','timetable_demand.py']},
        simulation_runs_added=0,selector='unchanged capacity_assumption',
        ordinary_screen='Weekdays within general teaching range, excluding general reading week and gazetted holidays; programme dates still need confirmation',
        denominators='Merged sessions are class meetings, not unique people. Headcount sums count a class again each time it meets. Exclusion reasons overlap; combinations partition excluded sessions.')
    save(a.output/'summary.json',public);save(a.output/'private_review.json',private)
    assert hashes=={n:sha(a.data_dir/n) for n in files}
    print(json.dumps(public['totals'],ensure_ascii=False,indent=2))


if __name__=='__main__':main()
