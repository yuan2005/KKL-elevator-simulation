"""Frozen-rule re-evaluation on already used dates, retaining local passenger traces."""
import argparse
from collections import defaultdict
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import time

from study_paths import ROOT, REPO, DATA
from repair_study_utils import load, save, sha, sources, freeze, verify_frozen, event_violations, instrument_motion
from run_linked_pilot import init_worker
from run_adaptive import worker as adaptive_worker, BASE, VARIANTS
from linked_demand import generate_linked
from timetable_demand import sessions_for_day

STEPS=[.25,.125]


def worker(job):
    args,prior_path,trace_folder=job
    day,variant,seed,selected,begin,end,names=args
    import config as C
    from building import IDX
    from sim import Sim
    from metrics import summarize
    old=load(prior_path)
    trips=generate_linked(selected,seed,begin,end,**dict(BASE,**VARIANTS[variant]))['trips']
    fingerprint=hashlib.sha256(json.dumps(trips,sort_keys=True).encode()).hexdigest()
    assert {r['input_sha256'] for r in old['rows']}=={fingerprint}
    original_run=Sim.run;saved_dt=C.DT;runs=[];captured=[]
    def checked(sim):
        name=names[len(captured)]
        counts=instrument_motion(sim);result=original_run(sim)
        assert len(sim.pax)==len(trips)
        groups=defaultdict(list)
        # Private: no class/session/person identities exported to the public aggregate archive.
        payload=[]
        for p,trip in zip(sim.pax,trips):
            assert p.t==trip['t_seconds'] and p.o==IDX[trip['origin']] and p.d==IDX[trip['destination']]
            origin='G' if p.o==IDX['G'] else 'other'
            window=int((p.t+begin)//900)*900
            groups[('window',str(window))].append(p.id)
            groups[('origin',origin)].append(p.id)
            groups[('layer',trip['layer'])].append(p.id)
            payload.append([p.id,p.t,p.o,p.d,p.t_board,p.t_alight,origin,trip['layer'],window])
        aggregates=[]
        for (dimension,label),ids in sorted(groups.items()):
            keep=set(ids)
            m=summarize(sim,keep=lambda p:p.id in keep)
            aggregates.append(dict(dimension=dimension,label=label,
                **{k:m[k] for k in ['n','awt','p90','lwt','att','unserved']},
                not_alighted=sum(sim.pax[i].t_alight is None for i in ids)))
        trace=trace_folder/f'{day}_{variant}_{seed}_{C.DT}_{name}.json.gz'
        with gzip.open(trace,'wt',encoding='utf-8') as f:
            json.dump(dict(columns=['id','arrival','origin_index','destination_index','board','alight','origin_group','layer','window_absolute_seconds'],
                           rows=payload,simulation_stop=sim.t),f,separators=(',',':'))
        captured.append(dict(event_time_violations=event_violations(sim),**counts,
            trace_file=trace.name,trace_sha256=sha(trace),strata=aggregates))
        return result
    try:
        Sim.run=checked
        for dt in STEPS:
            C.DT=dt;captured.clear()
            r=adaptive_worker(args)
            assert len(captured)==len(names)
            assert r['input_audit']==old['input_audit']
            assert json.loads(json.dumps(r['logs']))==old['logs']
            for row,audit in zip(r['rows'],captured):
                assert row['input_sha256']==fingerprint
                row.update(dt=dt,**{k:audit[k] for k in ['event_time_violations','physical_constraint_violations','move_steps']})
            r.update(dt=dt,audits=list(captured));runs.append(r)
    finally: Sim.run=original_run;C.DT=saved_dt
    return dict(date=day,variant=variant,seed=seed,runs=runs,same_original_inputs_audits_decisions=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prior',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path);args=ap.parse_args()
    prior,out=args.prior.resolve(),args.output.resolve()
    old=load(prior/'protocol.json');coverage=load(prior/'coverage.json')
    assert old['controllers']==['C1','C7','D10'] and old['seeds']==list(range(5000,5005))
    assert old['base']==BASE and old['variants']==VARIANTS
    assert sources()==load(ROOT/'published_results/motion_repair_v1/protocol.json')['source_hashes']
    data_hashes={n:sha(DATA/n) for n in old['data_hashes']}
    assert data_hashes==old['data_hashes']
    events=load(DATA/'semester1_kkl_dated_candidates.json')
    scripts=[Path(__file__).with_name(n) for n in ['run_repaired_evaluation.py','repair_study_utils.py',
        'run_adaptive.py','run_linked_pilot.py','linked_demand.py','timetable_demand.py','adaptive_policy.py','study_paths.py']]
    # Ported control/generator code must match the unchanged code used for the earlier repair check.
    frozen_scripts=load(ROOT/'published_results/motion_repair_v1/protocol.json')['script_hashes']
    for p in scripts:
        if p.name in frozen_scripts:assert sha(p)==frozen_scripts[p.name],p.name
    jobs=[];prior_hashes={};windows={}
    for day in old['dates']:
        sessions=sessions_for_day(events,day)
        begin=(min(s['start_min'] for s in sessions)-15)*60
        end=(max(s['end_min'] for s in sessions)+15)*60
        windows[day]=[begin,end]
        for variant in VARIANTS:
            for seed in old['seeds']:
                file=prior/'runs'/f'{day}_{variant}_{seed}.json';prior_hashes[file.name]=sha(file)
                jobs.append(((day,variant,seed,coverage[day]['selected'],begin,end,old['controllers']),file,out/'private_traces'))
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),
        design='Re-evaluation of frozen D10 under repaired motion on already used evaluation dates; not new untouched data',
        model_commit='dd06745a66eb2c21f3cd2ae6ead75220eb4dbfcc',dates=old['dates'],seeds=old['seeds'],
        controllers=old['controllers'],base=BASE,variants=VARIANTS,steps=STEPS,windows=windows,
        inputs=len(jobs),runs=len(jobs)*len(STEPS)*3,threshold=.10,
        primary='Equal date/background/seed mean D10-C1; C1 baseline fixed before rerun, no reselection',
        acceptance=dict(minimum_saving_seconds=.25,paired_seed_ci_upper_below=0,
            maximum_variant_mean_regression_seconds=.5,zero_unserved_incomplete_chain_conflicts=True),
        secondary='Per-run P90, long waits >60s, G versus other origin, layer and fixed absolute-clock 15-minute arrival windows; no outcome-selected windows',
        traces='Local gzip traces with synthetic per-input indices only. Do not publish passenger or classroom details.',
        uncertainty='Five seed-level paired averages over fixed dates/backgrounds; not real-data uncertainty. Both predeclared grids reported.',
        prior_protocol_hash=sha(prior/'protocol.json'),prior_coverage_hash=sha(prior/'coverage.json'),
        prior_raw_hash=sha(prior/'raw.json'),prior_run_hashes=prior_hashes,data_hashes=data_hashes)
    digest=freeze(out,protocol,scripts);(out/'private_traces').mkdir()
    start=time.time();rows=[];strata=[];checks=[];traces=[]
    print(f'Frozen re-evaluation: {len(jobs)} inputs / {protocol["runs"]} runs',flush=True)
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        futures=[pool.submit(worker,job) for job in jobs]
        for i,f in enumerate(as_completed(futures),1):
            result=f.result();key={k:result[k] for k in ['date','variant','seed']}
            save(out/'runs'/f'{result["date"]}_{result["variant"]}_{result["seed"]}.json',result)
            checks.append(dict(**key,same_original_inputs_audits_decisions=True))
            for run in result['runs']:
                rows.extend(run['rows'])
                for row,audit in zip(run['rows'],run['audits']):
                    strata.extend(dict(**key,dt=run['dt'],controller=row['controller'],**g) for g in audit['strata'])
                    traces.append(dict(file=audit['trace_file'],sha256=audit['trace_sha256']))
            if i%5==0:print(f'{i}/{len(jobs)} inputs; {i*6}/{protocol["runs"]} runs; {time.time()-start:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['variant'],r['seed'],r['dt'],r['controller']))
    strata.sort(key=lambda r:(r['date'],r['variant'],r['seed'],r['dt'],r['controller'],r['dimension'],r['label']))
    save(out/'raw.json',rows);save(out/'strata.json',strata);save(out/'pair_checks.json',checks);save(out/'private_trace_manifest.json',traces)
    verify_frozen(out,protocol,digest,scripts)
    assert prior_hashes=={n:sha(prior/'runs'/n) for n in prior_hashes}
    assert data_hashes=={n:sha(DATA/n) for n in data_hashes}
    for n,key in [('protocol.json','prior_protocol_hash'),('raw.json','prior_raw_hash'),('coverage.json','prior_coverage_hash')]:
        assert sha(prior/n)==protocol[key]
    keys=['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count','event_time_violations','physical_constraint_violations']
    save(out/'execution.json',dict(runs=len(rows),inputs=len(checks),private_traces=len(traces),seconds=time.time()-start,
        protocol_hash=digest,source_unchanged=True,prior_archive_unchanged=True,
        anomaly_totals={k:sum(r[k] for r in rows) for k in keys},anomalous_runs=sum(any(r[k] for k in keys) for r in rows)))
    print('FINISHED re-evaluation',flush=True)


if __name__=='__main__':main()
