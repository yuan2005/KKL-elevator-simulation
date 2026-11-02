"""Paired-date study of two verified room-floor additions; frozen controls."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib,json,time
from pathlib import Path
from study_paths import ROOT
from repair_study_utils import load,save,sha,sources,freeze,verify_frozen,event_violations,instrument_motion
from run_linked_pilot import init_worker
from timetable_demand import sessions_for_day,select_sessions
from linked_demand import generate_linked
from run_background_sensitivity import layer_fingerprint,KEYS,STEPS
import run_adaptive


def compare_demands(old,new):
    for layer in ['background','through']:
        assert layer_fingerprint(old,layer)==layer_fingerprint(new,layer)
    for sid,n in old['audit']['session_attendance'].items():
        assert new['audit']['session_attendance'][sid]==n
    return dict(background_unchanged=True,through_unchanged=True,existing_attendance_unchanged=True)


def worker(job):
    day,seed,oldsel,newsel,begin,end,baseline=job
    old=generate_linked(oldsel,seed,begin,end,**run_adaptive.BASE)
    new=generate_linked(newsel,seed,begin,end,**run_adaptive.BASE)
    fingerprint=lambda d:hashlib.sha256(json.dumps(d['trips'],sort_keys=True).encode()).hexdigest()
    assert {r['input_sha256'] for r in baseline}=={fingerprint(old)}
    checked=compare_demands(old,new)
    inputs=dict(date=day,seed=seed,old_input_sha256=fingerprint(old),new_input_sha256=fingerprint(new),
        old_selected_sessions=len(oldsel),new_selected_sessions=len(newsel),**checked,
        old_n=len(old['trips']),new_n=len(new['trips']),old_ledger=old['audit']['ledger'],new_ledger=new['audit']['ledger'],
        old_attendance_slots=old['audit']['attendance_slots'],new_attendance_slots=new['audit']['attendance_slots'],
        old_transfer_links=old['audit']['transfer_links'],new_transfer_links=new['audit']['transfer_links'])
    import config as C
    from sim import Sim
    original_run,original_dt=Sim.run,C.DT;audits=[];runs=[]
    def run(sim):
        physical=instrument_motion(sim);result=original_run(sim)
        audits.append(dict(event_time_violations=event_violations(sim),**physical));return result
    try:
        Sim.run=run
        for dt in STEPS:
            C.DT=dt;audits.clear()
            result=run_adaptive.worker((day,'mixed',seed,newsel,begin,end,['C1','C7','D10']))
            assert result['input_audit']==new['audit'] and len(audits)==3
            for row,audit in zip(result['rows'],audits):
                assert row['input_sha256']==fingerprint(new)
                row.update(dt=dt,**audit)
            result['dt']=dt;runs.append(result)
        assert runs[0]['logs']==runs[1]['logs']
    finally:Sim.run=original_run;C.DT=original_dt
    return dict(date=day,seed=seed,runs=runs,input_check=inputs)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--original-grid',required=True,type=Path)
    ap.add_argument('--baselines',nargs=2,required=True,type=Path);ap.add_argument('--data-dir',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
    plan=load(ROOT/'published_results/semester_coverage_v1/next_experiment_plan.json')['first_stage']
    revision=load(a.data_dir/'revision.json');coverage=load(a.original_grid/'coverage_private.json')
    fixed=load(ROOT/'published_results/motion_repair_v1/protocol.json');assert sources()==fixed['source_hashes']
    datahashes={n:sha(a.data_dir/n) for n in revision['new_hashes']};assert datahashes==revision['new_hashes']
    assert revision==load(ROOT/'published_results/semester_coverage_v1/comparison.json')['revision']
    events=load(a.data_dir/'semester1_kkl_dated_candidates.json');rooms={r['room']:r for r in load(a.data_dir/'room_registry.json')}
    baseline=[];prior={}
    for folder in a.baselines:
        p=load(folder/'protocol.json');e=load(folder/'execution.json')
        assert p['source_hashes']==sources() and sha(folder/'protocol.json')==e['protocol_hash']
        prior[folder.name]={n:sha(folder/n) for n in ['protocol.json','raw.json','execution.json']}
        baseline.extend(r for r in load(folder/'raw.json') if r['variant']=='mixed' and r['dt'] in STEPS)
    assert len(baseline)==90
    jobs=[];selected_private={}
    for day in plan['dates']:
        sessions=sessions_for_day(events,day);newsel,excluded=select_sessions(sessions,rooms,'capacity_assumption')
        oldsel=coverage[day]['selected'];oldidx={s['session_id']:s for s in oldsel};newidx={s['session_id']:s for s in newsel}
        assert all(newidx[k]==v for k,v in oldidx.items())
        added=[s for k,s in newidx.items() if k not in oldidx];assert added and {s['room'] for s in added}<=set(revision['changed_rooms'])
        begin,end=coverage[day]['window']
        assert [begin,end]==[(min(s['start_min'] for s in sessions)-15)*60,(max(s['end_min'] for s in sessions)+15)*60]
        selected_private[day]=dict(selected=newsel,excluded=excluded,window=[begin,end])
        for seed in plan['seeds']:
            rows=[r for r in baseline if (r['date'],r['seed'])==(day,seed)];assert len(rows)==6
            jobs.append((day,seed,oldsel,newsel,begin,end,rows))
    scripts=[Path(__file__).with_name(n) for n in ['run_room_revision.py','run_background_sensitivity.py','repair_study_utils.py',
        'run_adaptive.py','run_linked_pilot.py','adaptive_policy.py','linked_demand.py','timetable_demand.py','study_paths.py']]
    for p in scripts:
        if p.name in fixed['script_hashes']:assert sha(p)==fixed['script_hashes'][p.name]
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),model_commit='dd06745a66eb2c21f3cd2ae6ead75220eb4dbfcc',
        design='Room registry revision only: add eligible KKLG110/111 sessions; controls and physical settings unchanged',
        dates=plan['dates'],seeds=plan['seeds'],steps=STEPS,controllers=plan['controllers'],variants=['mixed'],base=run_adaptive.BASE,
        runs=90,reused_baseline_runs=90,inputs=15,revision=revision,prior_hashes=prior,
        coverage_hash=sha(a.original_grid/'coverage_private.json'),plan_hash=sha(ROOT/'published_results/semester_coverage_v1/next_experiment_plan.json'),
        primary='D10-C1 and C7-C1 at both registries, paired change of each contrast after the room addition',
        secondary='AWT/P90/long wait/G/other waits, switching, added demand and service/event/physical anomalies',
        pairing='Dates and seeds shared; independent background and through movements and existing class attendance identical. Classroom movements and transfer links may change.',
        uncertainty='Three seed-level five-day means. Fixed mixed-background development check, not a fresh policy evaluation.')
    digest=freeze(a.output,protocol,scripts);save(a.output/'reused_baselines.json',baseline);save(a.output/'coverage_private.json',selected_private)
    start=time.time();rows=[];inputs=[]
    print('Frozen room revision: 90 new runs + 90 reused baseline rows',flush=True)
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        for i,f in enumerate(as_completed([pool.submit(worker,j) for j in jobs]),1):
            result=f.result();save(a.output/'runs'/f'{result["date"]}_{result["seed"]}.json',result)
            rows.extend(r for run in result['runs'] for r in run['rows']);inputs.append(result['input_check'])
            print(f'{i}/15 inputs; {i*6}/90 runs; {time.time()-start:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['seed'],r['dt'],r['controller']));inputs.sort(key=lambda r:(r['date'],r['seed']))
    save(a.output/'raw.json',rows);save(a.output/'input_checks.json',inputs);verify_frozen(a.output,protocol,digest,scripts)
    assert datahashes=={n:sha(a.data_dir/n) for n in datahashes}
    for folder in a.baselines:assert prior[folder.name]=={n:sha(folder/n) for n in prior[folder.name]}
    save(a.output/'execution.json',dict(runs=len(rows),reused_baseline_runs=90,seconds=time.time()-start,protocol_hash=digest,
        anomaly_totals={k:sum(r[k] for r in rows) for k in KEYS},anomalous_runs=sum(any(r[k] for k in KEYS) for r in rows),
        source_unchanged=True,inputs_unchanged=True,prior_archives_unchanged=True))
    print('FINISHED room revision',flush=True)


if __name__=='__main__':main()
