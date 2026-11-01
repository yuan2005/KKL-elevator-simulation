"""Add a third grid to the archived two-grid study without retuning anything."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from study_paths import REPO, OUTPUT
from timetable_demand import save
from run_linked_pilot import init_worker
from run_adaptive import worker as adaptive_worker

DT=.0625


def load(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def worker(job):
    args, prior_path=job
    prior=load(prior_path)
    import config as C
    previous=C.DT
    try:
        C.DT=DT
        result=adaptive_worker(args)
        assert C.DT==DT
        result['dt']=DT
        for row in result['rows']:row['dt']=DT
        fingerprints={r['input_sha256'] for run in prior['runs'] for r in run['rows']}
        fingerprints.update(r['input_sha256'] for r in result['rows'])
        assert len(fingerprints)==1
        # Persisted tuples become JSON arrays; compare the same representation.
        canonical_logs=json.loads(json.dumps(result['logs']))
        assert all(result['input_audit']==run['input_audit'] and canonical_logs==run['logs'] for run in prior['runs'])
        return result
    finally:
        C.DT=previous


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--prior',required=True,type=Path,help='Local two-grid output including coverage_private.json and runs/')
    parser.add_argument('--output',type=Path,default=OUTPUT/'timestep_third_grid_v1')
    args=parser.parse_args();prior=args.prior.resolve();out=args.output.resolve()
    if out.exists():raise FileExistsError('Preserve completed experiments; use a new directory')
    old=load(prior/'protocol.json');coverage=load(prior/'coverage_private.json')
    source_hashes={p.name:sha(p) for p in REPO.glob('*.py')}
    assert old['steps']==[.25,.125] and source_hashes==old['original_source_hashes']
    scripts=[Path(__file__).with_name(n) for n in ['run_third_grid.py','run_adaptive.py','run_linked_pilot.py',
        'timetable_demand.py','linked_demand.py','adaptive_policy.py','study_paths.py']]
    for p in scripts:
        if p.name!='run_third_grid.py':assert sha(p)==old['script_hashes'][p.name]
    jobs=[];prior_logs={}
    for day in old['dates']:
        record=coverage[day];begin,end=record['window']
        for variant in old['variants']:
            for seed in old['seeds']:
                file=prior/'runs'/f'{day}_{variant}_{seed}.json'
                prior_logs[file.name]=sha(file)
                jobs.append(((day,variant,seed,record['selected'],begin,end,old['controllers']),file))
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),design='Third-grid numerical diagnostic on identical archived development inputs; no new selection or holdout',
        dates=old['dates'],seeds=old['seeds'],variants=old['variants'],controllers=old['controllers'],base=old['base'],frozen=old['frozen'],
        dt=DT,previous_steps=old['steps'],new_inputs=len(jobs),new_runs=len(jobs)*len(old['controllers']),
        primary='Absolute waiting shifts and paired C7-C1 / D10-C1 contrast shifts across 0.25 -> 0.125 -> 0.0625 seconds',
        interpretation='Compare signed increments and whether absolute increments shrink. No convergence order or Richardson extrapolation assumed for this state-switching queueing simulator. Three grids do not establish an error bound.',
        decision='Keep thresholds and old acceptance screen unchanged. If refinements do not stabilize the small effect, stop threshold tuning and inspect numerical event handling before making precise benefit claims.',
        uncertainty='Three seed-level paired averages across fixed dates/backgrounds; Monte Carlo uncertainty conditional on the development-selected rule',
        original_source_hashes=source_hashes,script_hashes={p.name:sha(p) for p in scripts},
        prior_protocol_hash=sha(prior/'protocol.json'),prior_raw_hash=sha(prior/'raw.json'),
        prior_coverage_hash=sha(prior/'coverage_private.json'),prior_run_hashes=prior_logs)
    out.mkdir(parents=True);(out/'runs').mkdir();(out/'scripts').mkdir()
    for p in scripts:shutil.copyfile(p,out/'scripts'/p.name)
    save(out/'protocol.json',protocol);protocol_hash=sha(out/'protocol.json')
    print(f'Frozen third-grid protocol: {len(jobs)} inputs / {protocol["new_runs"]} new runs',flush=True)
    started=time.time();rows=[];checks=[]
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        futures=[pool.submit(worker,j) for j in jobs]
        for i,f in enumerate(as_completed(futures),1):
            r=f.result();rows.extend(r['rows'])
            filename=f"{r['date']}_{r['variant']}_{r['seed']}.json"
            save(out/'runs'/filename,r)
            checks.append(dict(date=r['date'],variant=r['variant'],seed=r['seed'],same_input_hash_all_grids=True,
                same_demand_audit_all_grids=True,same_D10_decisions_and_switches_all_grids=True))
            if i%5==0:print(f'{i}/{len(jobs)} inputs; {3*i}/{protocol["new_runs"]} runs; {time.time()-started:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['variant'],r['seed'],r['controller']))
    save(out/'raw.json',rows);save(out/'pair_checks.json',checks)
    assert source_hashes=={p.name:sha(p) for p in REPO.glob('*.py')}
    assert protocol_hash==sha(out/'protocol.json') and sha(prior/'raw.json')==protocol['prior_raw_hash']
    assert sha(prior/'coverage_private.json')==protocol['prior_coverage_hash']
    assert prior_logs=={n:sha(prior/'runs'/n) for n in prior_logs}
    save(out/'execution.json',dict(new_runs=len(rows),inputs=len(checks),seconds=time.time()-started,
        protocol_hash=protocol_hash,original_source_unchanged=True,prior_archive_unchanged=True))
    print('FINISHED',len(rows),flush=True)


if __name__=='__main__':main()
