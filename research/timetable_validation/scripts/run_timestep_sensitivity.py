"""Paired timestep check with frozen demand and D10, using explored dates.

Each process runs both grids serially, resetting config.DT after each pair.
All controller/dispatch intervals remain in physical seconds, not tick counts.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from timetable_demand import DATA, read, save, sessions_for_day, select_sessions
from study_paths import ROOT, REPO, OUTPUT
from run_linked_pilot import init_worker
from run_adaptive import worker as adaptive_worker, BASE, VARIANTS

DATES=[f'2026-10-{d}' for d in range(26,31)]
SEEDS=[4000,4001,4002]
STEPS=[.25,.125]
CONTROLS=['C1','C7','D10']


def worker(args):
    import config as C
    original_dt=C.DT
    runs=[]
    try:
        for dt in STEPS:
            C.DT=dt
            r=adaptive_worker(args)
            assert C.DT==dt
            r['dt']=dt
            for row in r['rows']:
                row['dt']=dt
            runs.append(r)
        assert len({row['input_sha256'] for r in runs for row in r['rows']})==1
        assert runs[0]['input_audit']==runs[1]['input_audit']
        # Past-arrival-only rules should see identical windows on both exact binary grids.
        assert runs[0]['logs']==runs[1]['logs']
        return dict(date=args[0],variant=args[1],seed=args[2],runs=runs)
    finally:
        C.DT=original_dt


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,default=OUTPUT/'timestep_sensitivity_v1')
    out=parser.parse_args().output.resolve()
    if out.exists():raise FileExistsError('Use a new output directory')
    events=read('semester1_kkl_dated_candidates.json')
    rooms={r['room']:r for r in read('room_registry.json')}
    jobs=[];coverage={};counts=[]
    for day in DATES:
        sessions=sessions_for_day(events,day)
        selected,excluded=select_sessions(sessions,rooms,'capacity_assumption')
        begin=(min(s['start_min'] for s in sessions)-15)*60
        end=(max(s['end_min'] for s in sessions)+15)*60
        coverage[day]=dict(selected=selected,excluded=excluded,window=[begin,end])
        counts.append(dict(date=day,candidates=len(sessions),selected=len(selected),excluded=len(excluded),window=[begin,end]))
        for variant in VARIANTS:
            for seed in SEEDS:jobs.append((day,variant,seed,selected,begin,end,CONTROLS))
    sources={p.name:sha(p) for p in REPO.glob('*.py')}
    scripts=[Path(__file__).with_name(n) for n in ['run_timestep_sensitivity.py','run_adaptive.py','run_linked_pilot.py','timetable_demand.py','linked_demand.py','adaptive_policy.py','study_paths.py']]
    previous=ROOT/'published_results/adaptive_v1/develop/raw.json'
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),
        design='Paired numerical sensitivity; explored development dates, unchanged demand/controls; not a new holdout',
        dates=DATES,seeds=SEEDS,steps=STEPS,controllers=CONTROLS,base=BASE,variants=VARIANTS,
        inputs=len(jobs),runs=len(jobs)*len(STEPS)*len(CONTROLS),
        frozen=dict(missing_headcount_capacity_fraction=.6,D10_threshold=.1,controller_interval_seconds=60,
                    rolling_window_seconds=300,min_dwell_seconds=600,dispatch_interval_seconds=1),
        primary='Paired fine-minus-coarse change in AWT and in D10-C1 / C7-C1 contrasts; equal date/variant/seed weighting',
        checks=['identical demand hashes/audits across both grids and all controls','identical D10 decision/switch logs across grids',
                'coarse metrics match prior development archive','service anomalies retained and reported'],
        interpretation='Two grids cannot prove numerical convergence or establish an error bound. No pass tolerance chosen after seeing output. Unstable ranking or material contrast shifts trigger finer-grid/model review before retuning.',
        uncertainty='Seed-level paired contrasts, each averaged across fixed dates/variants; three-replicate t intervals reflect Monte Carlo variability only',
        original_source_hashes=sources,data_hashes={n:sha(DATA/n) for n in ['semester1_kkl_dated_candidates.json','room_registry.json']},
        script_hashes={p.name:sha(p) for p in scripts},prior_development_raw_hash=sha(previous))
    out.mkdir(parents=True);(out/'runs').mkdir();(out/'scripts').mkdir()
    for p in scripts:shutil.copyfile(p,out/'scripts'/p.name)
    save(out/'protocol.json',protocol);save(out/'coverage_private.json',coverage);save(out/'coverage_summary.json',counts)
    protocol_hash=sha(out/'protocol.json')
    print(f'Protocol frozen: {len(jobs)} paired inputs / {protocol["runs"]} runs',flush=True)
    start=time.time();rows=[];audit=[]
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        futures=[pool.submit(worker,j) for j in jobs]
        for i,f in enumerate(as_completed(futures),1):
            pair=f.result()
            save(out/'runs'/f"{pair['date']}_{pair['variant']}_{pair['seed']}.json",pair)
            rows.extend(row for run in pair['runs'] for row in run['rows'])
            audit.append(dict(date=pair['date'],variant=pair['variant'],seed=pair['seed'],
                identical_input_audits=True,identical_decision_and_switch_logs=True))
            if i%5==0:print(f'{i}/{len(jobs)} inputs; {6*i}/{protocol["runs"]} runs; {time.time()-start:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['variant'],r['seed'],r['dt'],r['controller']))
    save(out/'raw.json',rows);save(out/'pair_checks.json',audit)
    assert sources=={p.name:sha(p) for p in REPO.glob('*.py')}
    assert protocol_hash==sha(out/'protocol.json')
    save(out/'execution.json',dict(runs=len(rows),inputs=len(audit),seconds=time.time()-start,
        protocol_hash=protocol_hash,original_source_unchanged=True,protocol_unchanged=True))
    print('FINISHED',len(rows),flush=True)


if __name__=='__main__':main()
