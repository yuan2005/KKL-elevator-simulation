"""Frozen C1/C7/D10 comparison on three preselected teaching weeks."""
import argparse,time
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from study_paths import ROOT
from repair_study_utils import load,save,sha,sources,freeze,verify_frozen,event_violations,instrument_motion
from run_linked_pilot import init_worker
from timetable_demand import sessions_for_day,select_sessions
from run_background_sensitivity import KEYS
import run_adaptive


def worker(job):
    role,day,variant,seed,selected,begin,end,steps,names=job
    import config as C
    from sim import Sim
    original_run,original_dt=Sim.run,C.DT
    audits=[];runs=[]
    def checked_run(sim):
        physical=instrument_motion(sim);result=original_run(sim)
        audits.append(dict(event_time_violations=event_violations(sim),**physical))
        return result
    try:
        Sim.run=checked_run
        for dt in steps:
            C.DT=dt;audits.clear()
            result=run_adaptive.worker((day,variant,seed,selected,begin,end,names))
            assert len(audits)==len(names)
            for row,audit in zip(result['rows'],audits):row.update(week=role,dt=dt,**audit)
            result['dt']=dt;runs.append(result)
        assert all(r['logs']==runs[0]['logs'] and r['input_audit']==runs[0]['input_audit'] for r in runs)
        rows=[row for r in runs for row in r['rows']]
        assert len({r['input_sha256'] for r in rows})==len({r['n'] for r in rows})==1
        audit=runs[0]['input_audit']
        check=dict(week=role,date=day,variant=variant,seed=seed,selected_sessions=len(selected),
            input_sha256=rows[0]['input_sha256'],n=rows[0]['n'],ledger=audit['ledger'],
            attendance_slots=audit['attendance_slots'],transfer_links=audit['transfer_links'],
            controller_grid_inputs_match=True,grid_decisions_match=True)
        return dict(week=role,date=day,variant=variant,seed=seed,runs=runs,input_check=check)
    finally:Sim.run=original_run;C.DT=original_dt


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data-dir',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
    planfile=ROOT/'published_results/semester_coverage_v1/next_experiment_plan.json'
    plan=load(planfile)['second_stage'];weeks=[w for w in plan['proposed_weeks'] if w['role']!='reserve']
    assert [w['role'] for w in weeks]==['early','middle','late']
    fixed=load(ROOT/'published_results/motion_repair_v1/protocol.json')
    assert sources()==fixed['source_hashes']
    revision=load(a.data_dir/'revision.json')
    assert revision==load(ROOT/'published_results/room_revision_repaired_v1/protocol.json')['revision']
    datahashes={n:sha(a.data_dir/n) for n in revision['new_hashes']}
    assert datahashes==revision['new_hashes']
    events=load(a.data_dir/'semester1_kkl_dated_candidates.json')
    rooms={r['room']:r for r in load(a.data_dir/'room_registry.json')}
    coverage={};daily=[];jobs=[]
    for w in weeks:
        total=assumed=0
        for day in w['dates']:
            sessions=sessions_for_day(events,day);selected,excluded=select_sessions(sessions,rooms,'capacity_assumption')
            assert selected
            begin=(min(s['start_min'] for s in sessions)-15)*60
            end=(max(s['end_min'] for s in sessions)+15)*60
            n_assumed=sum(s['headcount_basis']=='assumed_60pct_room_capacity' for s in selected)
            total+=len(selected);assumed+=n_assumed
            coverage[day]=dict(week=w['role'],selected=selected,excluded=excluded,window=[begin,end])
            daily.append(dict(week=w['role'],date=day,candidates=len(sessions),selected=len(selected),
                excluded=len(excluded),assumed_headcount_sessions=n_assumed,window=[begin,end]))
            for variant in plan['variants']:
                for seed in plan['seeds']:
                    jobs.append((w['role'],day,variant,seed,selected,begin,end,plan['steps'],plan['controllers']))
        assert total==w['selected_sessions'] and assumed==w['assumed_headcount_sessions'],w['role']
    assert len(jobs)==135 and len(jobs)*len(plan['steps'])*len(plan['controllers'])==plan['planned_new_runs']==810
    scripts=[Path(__file__).with_name(n) for n in ['run_crossweek.py','repair_study_utils.py',
        'run_background_sensitivity.py','run_adaptive.py','run_linked_pilot.py','adaptive_policy.py',
        'linked_demand.py','timetable_demand.py','study_paths.py']]
    for path in scripts:
        if path.name in fixed['script_hashes']:assert sha(path)==fixed['script_hashes'][path.name]
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),model_commit='dd06745a66eb2c21f3cd2ae6ead75220eb4dbfcc',
        model_review=dict(pr=2,state='open',formal_reviews=0,checked_date='2026-11-02',head='62ccc8c08b3a29760a78289f7ce1adb114a2870d'),
        design='Preselected cross-date robustness of unchanged C1/C7/D10 on the revised room registry',
        weeks=weeks,dates=[d for w in weeks for d in w['dates']],seeds=plan['seeds'],variants=plan['variants'],
        variant_parameters=run_adaptive.VARIANTS,base=run_adaptive.BASE,steps=plan['steps'],controllers=plan['controllers'],
        inputs=135,runs=810,revision=revision,plan_hash=sha(planfile),
        frozen_selection_hash=sha(ROOT/'published_results/adaptive_v1/frozen_selection.json'),
        primary='Equal date/variant/seed mean AWT contrast D10-C1 at each grid; report every week and background',
        secondary='C7-C1 and D10-C7; P90, long-wait fraction, G/non-G waits, switches and grid differences',
        benefit_screen=dict(baseline='C1',minimum_saving_seconds=.25,interval_upper_below=0.,max_variant_regression_seconds=.5,zero_anomalies=True),
        pairing='Identical generated trips for all controllers and both grids within each date/variant/seed',
        uncertainty='95% Student t intervals over three seed-level means; fixed dates/backgrounds averaged equally. These are Monte Carlo intervals conditional on model/input assumptions, not building-population intervals.',
        scope=plan['scope']+' Most class signatures repeat; reserve week excluded; no new threshold selection.')
    digest=freeze(a.output,protocol,scripts);save(a.output/'coverage_private.json',coverage);save(a.output/'daily_coverage.json',daily)
    start=time.time();rows=[];checks=[]
    print('FROZEN cross-week study: 135 paired inputs, 810 new runs',flush=True)
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        for i,f in enumerate(as_completed([pool.submit(worker,j) for j in jobs]),1):
            r=f.result();save(a.output/'runs'/f"{r['date']}_{r['variant']}_{r['seed']}.json",r)
            rows.extend(row for grid in r['runs'] for row in grid['rows']);checks.append(r['input_check'])
            if i%3==0 or i==len(jobs):print(f'{i}/135 inputs; {i*6}/810 runs; {time.time()-start:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['variant'],r['seed'],r['dt'],r['controller']))
    checks.sort(key=lambda r:(r['date'],r['variant'],r['seed']))
    save(a.output/'raw.json',rows);save(a.output/'input_checks.json',checks)
    verify_frozen(a.output,protocol,digest,scripts)
    assert datahashes=={n:sha(a.data_dir/n) for n in datahashes}
    save(a.output/'execution.json',dict(runs=len(rows),inputs=len(checks),seconds=time.time()-start,protocol_hash=digest,
        anomaly_totals={k:sum(r[k] for r in rows) for k in KEYS},anomalous_runs=sum(any(r[k] for k in KEYS) for r in rows),
        source_unchanged=True,inputs_unchanged=True))
    print('FINISHED cross-week study',flush=True)


if __name__=='__main__':main()
