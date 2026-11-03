"""One-factor background-intent intensity check on the repaired simulator."""
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
import run_adaptive
from linked_demand import generate_linked

STEPS=[.25,.125]
RATES=[6,12,24]
KEYS=['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count','event_time_violations','physical_constraint_violations']


def layer_fingerprint(demand,layer):
    rows=sorted([{k:v for k,v in p.items() if k!='id'} for p in demand['movements'] if p['layer']==layer],key=lambda p:p['trip_key'])
    return hashlib.sha256(json.dumps(rows,sort_keys=True).encode()).hexdigest()


def worker(job):
    day,seed,selected,begin,end,baseline=job
    import config as C
    from sim import Sim
    original_run=Sim.run;original_dt=C.DT;original_rate=run_adaptive.BASE['background_rate']
    reference=generate_linked(selected,seed,begin,end,**run_adaptive.BASE)
    base_hash=hashlib.sha256(json.dumps(reference['trips'],sort_keys=True).encode()).hexdigest()
    assert {r['input_sha256'] for r in baseline}=={base_hash}
    layers={layer:layer_fingerprint(reference,layer) for layer in ['class','through']}
    rows=[];runs=[];inputs=[];audits=[]
    def checked(sim):
        physical=instrument_motion(sim);result=original_run(sim)
        audits.append(dict(event_time_violations=event_violations(sim),**physical))
        return result
    try:
        Sim.run=checked
        for rate in RATES:
            demand=reference if rate==12 else generate_linked(selected,seed,begin,end,**dict(run_adaptive.BASE,background_rate=rate))
            assert layers=={layer:layer_fingerprint(demand,layer) for layer in layers}
            for key in ['attendance_slots','transfer_links','hypothetical_people','session_attendance']:
                assert demand['audit'][key]==reference['audit'][key]
            fingerprint=hashlib.sha256(json.dumps(demand['trips'],sort_keys=True).encode()).hexdigest()
            inputs.append(dict(date=day,seed=seed,background_rate=rate,input_sha256=fingerprint,
                total_lift_requests=len(demand['trips']),**demand['audit']['ledger'],
                class_and_through_unchanged=True,class_fingerprint=layers['class'],through_fingerprint=layers['through']))
            if rate==12:continue
            run_adaptive.BASE['background_rate']=rate
            rate_runs=[]
            for dt in STEPS:
                C.DT=dt;audits.clear()
                r=run_adaptive.worker((day,'mixed',seed,selected,begin,end,['C1','C7','D10']))
                assert len(audits)==3
                assert r['input_audit']==demand['audit']
                for row,audit in zip(r['rows'],audits):
                    assert row['input_sha256']==fingerprint
                    row.update(dt=dt,background_rate=rate,**audit)
                r.update(dt=dt,background_rate=rate);rate_runs.append(r);rows.extend(r['rows'])
            assert rate_runs[0]['logs']==rate_runs[1]['logs']
            runs.extend(rate_runs)
    finally:Sim.run=original_run;C.DT=original_dt;run_adaptive.BASE['background_rate']=original_rate
    return dict(date=day,seed=seed,rows=rows,runs=runs,input_checks=inputs)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--original-grid',required=True,type=Path)
    ap.add_argument('--baselines',required=True,nargs=2,type=Path);ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args();out=args.output.resolve();grid=args.original_grid.resolve()
    old=load(grid/'protocol.json');coverage=load(grid/'coverage_private.json')
    frozen=load(ROOT/'published_results/motion_repair_v1/protocol.json')
    assert sources()==frozen['source_hashes'] and run_adaptive.BASE['background_rate']==12
    baselines=[];prior={}
    for folder in args.baselines:
        p=load(folder/'protocol.json');e=load(folder/'execution.json')
        assert p['source_hashes']==frozen['source_hashes'] and e['anomalous_runs']==0
        assert sha(folder/'protocol.json')==e['protocol_hash']
        for k in ['dates','base','variants','controllers','frozen']:assert p[k]==frozen[k]
        prior[folder.name]={n:sha(folder/n) for n in ['protocol.json','raw.json','execution.json']}
        baselines.extend(dict(r,background_rate=12) for r in load(folder/'raw.json') if r['variant']=='mixed' and r['dt'] in STEPS)
    assert len(baselines)==90
    scripts=[Path(__file__).with_name(n) for n in ['run_background_sensitivity.py','repair_study_utils.py','run_adaptive.py',
        'run_linked_pilot.py','adaptive_policy.py','linked_demand.py','timetable_demand.py','study_paths.py']]
    for p in scripts:
        if p.name in frozen['script_hashes']:assert sha(p)==frozen['script_hashes'][p.name]
    jobs=[]
    for day in old['dates']:
        record=coverage[day]
        for seed in old['seeds']:
            base=[r for r in baselines if (r['date'],r['seed'])==(day,seed)];assert len(base)==6
            jobs.append((day,seed,record['selected'],*record['window'],base))
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),
        design='One-factor background-intent rate sensitivity; fixed mixed-floor background and frozen D10',
        model_commit='dd06745a66eb2c21f3cd2ae6ead75220eb4dbfcc',dates=old['dates'],seeds=old['seeds'],steps=STEPS,
        rates=RATES,base=dict(run_adaptive.BASE),controllers=['C1','C7','D10'],new_rates=[6,24],runs=180,inputs=30,reused_baseline_runs=90,
        rate_units='Independent background trip intents per 300 seconds before same-floor/stairs filtering, not measured lift arrivals',
        range_reason='Reuse the previous multiday study half/base/double assumptions (6/12/24). Through rate remains 6; high_share remains .5.',
        primary='D10-C1 mean waiting at each rate and paired change relative to rate 12; C7-C1 retained',
        secondary='Absolute AWT/P90/long waits, G/other waits, switching, realized layer counts and service/physical/event anomalies',
        pairing='Same input across controls and grids at each rate. Class and through movements identical across rates; background times/counts change, so no individual background-passenger causal pairing across rates.',
        uncertainty='Three seed-level means over five fixed development dates. This is an assumption sensitivity check, not a new candidate selection.',
        checks=['Verify classroom and through-movement fingerprints ignoring global trip indices',
                'Verify unchanged attendance and transfer links','Report every rate and grid, including service anomalies'],
        prior_hashes=prior,coverage_hash=sha(grid/'coverage_private.json'))
    digest=freeze(out,protocol,scripts);save(out/'reused_baselines.json',baselines)
    start=time.time();rows=[];inputs=[]
    print('Frozen background study: 180 new runs + 90 archived base-rate rows',flush=True)
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        futures=[pool.submit(worker,j) for j in jobs]
        for i,f in enumerate(as_completed(futures),1):
            r=f.result();rows.extend(r['rows']);inputs.extend(r['input_checks'])
            save(out/'runs'/f'{r["date"]}_{r["seed"]}.json',r)
            print(f'{i}/15 date/seed pairs; {i*12}/180 runs; {time.time()-start:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['seed'],r['background_rate'],r['dt'],r['controller']))
    inputs.sort(key=lambda r:(r['date'],r['seed'],r['background_rate']))
    save(out/'raw.json',rows);save(out/'input_checks.json',inputs)
    verify_frozen(out,protocol,digest,scripts)
    assert sha(grid/'coverage_private.json')==protocol['coverage_hash']
    for folder in args.baselines:assert prior[folder.name]=={n:sha(folder/n) for n in prior[folder.name]}
    save(out/'execution.json',dict(runs=len(rows),reused_baseline_runs=len(baselines),input_conditions=len(inputs),
        seconds=time.time()-start,protocol_hash=digest,source_unchanged=True,prior_archives_unchanged=True,
        anomaly_totals={k:sum(r[k] for r in rows) for k in KEYS},anomalous_runs=sum(any(r[k] for k in KEYS) for r in rows)))
    print('FINISHED background sensitivity',flush=True)


if __name__=='__main__':main()
