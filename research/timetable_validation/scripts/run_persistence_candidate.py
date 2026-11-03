"""One additional persistence setting on archived development inputs only."""
import argparse
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import time

from study_paths import ROOT
from repair_study_utils import load,save,sha,sources,freeze,verify_frozen,event_violations,instrument_motion
from run_linked_pilot import init_worker
from run_adaptive import BASE,VARIANTS
from linked_demand import generate_linked

STEPS=[.25,.125]


def worker(job):
    day,variant,seed,selected,begin,end,baseline=job
    import config as C
    from building import IDX
    from traffic import Passenger
    from sim import Sim
    from strategies import make_combos
    from metrics import summarize
    from persistence_candidate import PersistenceController
    demand=generate_linked(selected,seed,begin,end,**dict(BASE,**VARIANTS[variant]));trips=demand['trips']
    digest=hashlib.sha256(json.dumps(trips,sort_keys=True).encode()).hexdigest()
    assert {r['input_sha256'] for r in baseline}=={digest}
    by={s['session_id']:s for s in selected};rows=[];logs={};previous=C.DT
    try:
        for dt in STEPS:
            C.DT=dt
            pax=[Passenger(i,p['t_seconds'],IDX[p['origin']],IDX[p['destination']],i) for i,p in enumerate(trips)]
            controller=PersistenceController(make_combos(),pax,IDX,required=3)
            sim=Sim(pax,controller,end-begin);physical=instrument_motion(sim);sim.run()
            m=summarize(sim);assert m['n']==len(trips)
            m.update(date=day,variant=variant,seed=seed,dt=dt,controller='D10P3',input_sha256=digest,
                switches=max(0,len(sim.combo_log)-1),not_alighted=sum(p.t_alight is None for p in pax),
                event_time_violations=event_violations(sim),**physical)
            last={};bad=0
            for p,trip in zip(pax,trips):
                person=trip.get('person')
                if person is None:continue
                if person in last and (last[person] is None or last[person]>p.t):bad+=1
                last[person]=p.t_alight
            m['chain_availability_violations']=bad
            m['late_transfer_lift_count']=sum(p.t_alight is None or p.t_alight+begin>by[trip['destination_session']]['start_min']*60
                for p,trip in zip(pax,trips) if trip['phase']=='transfer')
            for label in ['G','other','class','background','through']:
                keep=(lambda p:p.o==IDX['G']) if label=='G' else (lambda p:p.o!=IDX['G']) if label=='other' else (lambda p:trips[p.id]['layer']==label)
                part=summarize(sim,keep=keep)
                for field in ['n','awt','lwt','p90']:m[label+'_'+field]=part[field]
            logs[str(dt)]=dict(decisions=controller.log,switches=sim.combo_log)
            rows.append(m)
    finally:C.DT=previous
    # Decision features are unchanged across grids; only the persistence gate differs from D10.
    assert logs[str(STEPS[0])]==logs[str(STEPS[1])]
    return dict(date=day,variant=variant,seed=seed,rows=rows,logs=logs,input_audit=demand['audit'])


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--original-grid',required=True,type=Path)
    ap.add_argument('--baselines',required=True,nargs=2,type=Path);ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args();out=args.output.resolve();grid=args.original_grid.resolve()
    coverage=load(grid/'coverage_private.json');old=load(grid/'protocol.json')
    repaired=load(ROOT/'published_results/motion_repair_v1/protocol.json')
    assert sources()==repaired['source_hashes']
    baselines=[];prior_hashes={};prior_run_paths={}
    for folder in args.baselines:
        p=load(folder/'protocol.json');e=load(folder/'execution.json')
        assert p['source_hashes']==repaired['source_hashes'] and e['anomalous_runs']==0
        assert sha(folder/'protocol.json')==e['protocol_hash']
        for field in ['dates','variants','base','controllers','frozen']:
            assert p[field]==repaired[field]
        for name,digest in repaired['script_hashes'].items():
            if name!='run_repair_validation.py':assert p['script_hashes'][name]==digest
        baselines.extend(r for r in load(folder/'raw.json') if r['dt'] in STEPS)
        prior_hashes[folder.name]={n:sha(folder/n) for n in ['protocol.json','execution.json','raw.json']}
        for path in (folder/'runs').glob('*.json'):
            assert path.name not in prior_run_paths;prior_run_paths[path.name]=path
    assert len(baselines)==270
    jobs=[];prior_logs={}
    for day in old['dates']:
        record=coverage[day];begin,end=record['window']
        for variant in VARIANTS:
            for seed in old['seeds']:
                subset=[r for r in baselines if (r['date'],r['variant'],r['seed'])==(day,variant,seed)]
                assert len(subset)==6
                jobs.append((day,variant,seed,record['selected'],begin,end,subset))
                name=f'{day}_{variant}_{seed}.json';prior_logs[name]=sha(prior_run_paths[name])
    scripts=[Path(__file__).with_name(n) for n in ['run_persistence_candidate.py','persistence_candidate.py','repair_study_utils.py',
        'adaptive_policy.py','run_adaptive.py','run_linked_pilot.py','linked_demand.py','timetable_demand.py','study_paths.py']]
    for path in scripts:
        if path.name in repaired['script_hashes'] and path.name!='run_repair_validation.py':
            assert sha(path)==repaired['script_hashes'][path.name]
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),
        design='One-factor development experiment: persistence 2 -> 3 consecutive decisions in both switch directions',
        model_commit='dd06745a66eb2c21f3cd2ae6ead75220eb4dbfcc',
        dates=old['dates'],variants=VARIANTS,seeds=old['seeds'],steps=STEPS,base=BASE,
        new_controller='D10P3',new_runs=90,runs=90,inputs=45,reused_baseline_runs=270,
        only_change='Consecutive target-state decisions required for switching: 2 -> 3. Update 60s, history 300s, dwell 600s, h=.10, entry .70/exit .65, n>=10, initial C1 unchanged.',
        primary='Paired D10P3-D10 whole-day AWT, equal date/background/seed means at both fixed grids',
        proceed_screen='Proceed to further testing only if D10P3-D10 mean and seed-CI upper are below zero at both grids, every variant mean regression <=0.5s, and zero service/chain/event/physical anomalies. Fewer switches alone is insufficient.',
        original_success_screen='Separately retain >=0.25s saving versus C1, seed-CI upper<0, no variant mean regression>0.5s and zero service/chain anomalies. Not replaced by candidate comparison.',
        secondary='Switch count, G/other waits, P90, >60s waits; all backgrounds retained. No threshold search.',
        selection_warning='Proposed after inspecting previously used evaluation outputs; development-only exploration. November 2-6 is not a new untouched test for this revision. No independent candidate evaluation in this experiment.',
        prior_hashes=prior_hashes,prior_run_hashes=prior_logs,coverage_hash=sha(grid/'coverage_private.json'))
    digest=freeze(out,protocol,scripts);save(out/'reused_baselines.json',baselines)
    start=time.time();rows=[];checks=[]
    print('Frozen single-factor protocol: 90 new runs; 270 archived baseline rows reused',flush=True)
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        futures=[pool.submit(worker,j) for j in jobs]
        for i,f in enumerate(as_completed(futures),1):
            r=f.result();name=f'{r["date"]}_{r["variant"]}_{r["seed"]}.json'
            original=load(prior_run_paths[name])
            assert all(r['input_audit']==v['input_audit'] for v in original['runs'])
            features=lambda logs:[{k:v for k,v in d.items() if k!='choice'} for d in logs['decisions']]
            for dt in STEPS:
                prior_run=next(v for v in original['runs'] if v['dt']==dt)
                assert features(r['logs'][str(dt)])==features(prior_run['logs']['D10'])
            rows.extend(r['rows']);save(out/'runs'/name,r)
            checks.append(dict(date=r['date'],variant=r['variant'],seed=r['seed'],same_input_audit_and_past_features=True))
            if i%5==0:print(f'{i}/45 inputs; {2*i}/90 runs; {time.time()-start:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['variant'],r['seed'],r['dt']))
    save(out/'raw.json',rows);save(out/'pair_checks.json',checks)
    verify_frozen(out,protocol,digest,scripts)
    assert sha(grid/'coverage_private.json')==protocol['coverage_hash']
    for folder in args.baselines:assert prior_hashes[folder.name]=={n:sha(folder/n) for n in prior_hashes[folder.name]}
    assert prior_logs=={name:sha(path) for name,path in prior_run_paths.items()}
    keys=['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count','event_time_violations','physical_constraint_violations']
    save(out/'execution.json',dict(runs=len(rows),inputs=len(checks),reused_baseline_runs=len(baselines),seconds=time.time()-start,
        protocol_hash=digest,source_unchanged=True,prior_archives_unchanged=True,
        anomaly_totals={k:sum(r[k] for r in rows) for k in keys},anomalous_runs=sum(any(r[k] for k in keys) for r in rows)))
    print('FINISHED one-factor experiment',flush=True)


if __name__=='__main__':main()
