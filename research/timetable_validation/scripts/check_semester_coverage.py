"""Check public coverage aggregates; detailed timetable reconstruction remains local."""
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
import json
from audit_semester_coverage import NUMERIC, calendar_flags, load, sha
from study_paths import ROOT


def check(summary):
    daily=summary['daily'];weekly=summary['weekly'];totals=summary['totals']
    assert len({r['date'] for r in daily})==len(daily)
    for row in daily:
        d=date.fromisoformat(row['date'])
        assert row['week_start']==(d-timedelta(days=d.weekday())).isoformat()
        assert row['calendar_flags']==calendar_flags(row['date'],load(ROOT/'semester_calendar_review.json'))
        assert row['merged_sessions']==row['selected_sessions']+row['excluded_sessions']
        assert row['selected_sessions']==row['selected_known_headcount']+row['selected_assumed_headcount']
        assert sum(row['exclusion_combinations'].values())==row['excluded_sessions']
        reasons=Counter()
        for combination,count in row['exclusion_combinations'].items():
            for reason in combination.split('|'):reasons[reason]+=count
        assert dict(reasons)==row['exclusion_reasons']
        assert row['ordinary_selected_sessions']==(0 if row['calendar_flags'] else row['selected_sessions'])
    for w in weekly:
        rows=[r for r in daily if r['week_start']==w['week_start']]
        for k in NUMERIC:assert sum(r[k] for r in rows)==w[k]
    for k in NUMERIC:assert sum(r[k] for r in weekly)==totals[k]
    for field in ['exclusion_reasons','exclusion_combinations','selected_careers']:
        c=Counter()
        for r in daily:c.update(r[field])
        assert dict(c)==totals[field]
    assert summary['provenance']['simulation_runs_added']==0


def main():
    folder=ROOT/'published_results/semester_coverage_v1'
    base=load(folder/'original_summary.json');new=load(folder/'revised_summary.json')
    for s in [base,new]:
        check(s)
        assert s['provenance']['calendar_sha256']==sha(ROOT/'semester_calendar_review.json')
        for name,digest in s['provenance']['scripts'].items():assert sha(Path(__file__).with_name(name))==digest
    c=load(folder/'comparison.json');r=c['revision']
    assert c['added_sessions']==new['totals']['selected_sessions']-base['totals']['selected_sessions']==336
    assert c['added_known']+c['added_assumed']==c['added_sessions']
    assert c['added_known']==new['totals']['selected_known_headcount']-base['totals']['selected_known_headcount']
    assert c['added_ordinary']==new['totals']['ordinary_selected_sessions']-base['totals']['ordinary_selected_sessions']
    assert r['parent_hashes']==base['provenance']['source_hashes'] and r['new_hashes']==new['provenance']['source_hashes']
    assert r['evidence_hash']==sha(ROOT/'room_evidence_2026-11-02.json')
    for n in ['semester1_kkl_dated_candidates.json','semester1_kkl_rules.json']:assert r['parent_hashes'][n]==r['new_hashes'][n]
    p=load(folder/'next_experiment_plan.json');a=p['first_stage'];b=p['second_stage']
    assert p['status']=='prepared_not_run' and p['simulations_added']==0
    assert len(a['dates'])*len(a['seeds'])*len(a['variants'])*len(a['steps'])*len(a['controllers'])==a['planned_new_runs']==90
    dates=[d for w in b['proposed_weeks'] if w['role']!='reserve' for d in w['dates']]
    assert len(dates)==len(set(dates))==15
    assert len(dates)*len(b['seeds'])*len(b['variants'])*len(b['steps'])*len(b['controllers'])==b['planned_new_runs']==810
    daily={r['date']:r for r in new['daily']}
    for w in b['proposed_weeks']:
        assert w['selected_sessions']==sum(daily[d]['selected_sessions'] for d in w['dates'])
        assert w['assumed_headcount_sessions']==sum(daily[d]['selected_assumed_headcount'] for d in w['dates'])
        assert all(not calendar_flags(d,load(ROOT/'semester_calendar_review.json')) for d in w['dates'])
        assert 0<=w['previously_seen_session_signatures']<=w['selected_sessions']
    pub=load(folder/'publication.json')
    for name,digest in pub['files'].items():assert sha(folder/name)==digest,name
    for name,digest in pub['scripts'].items():assert sha(Path(__file__).with_name(name))==digest,name
    print('PASS: semester counts reconcile; 336 added sessions; unchanged source timetable; proposed 90/810 runs remain unexecuted.')
    print('Public checks verify aggregates and provenance; detailed session equality and signature overlap are checked against local private records.')


if __name__=='__main__':main()
