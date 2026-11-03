"""Re-evaluate original S1-S6 settings with the repaired model, not exact replication."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time

from study_paths import ROOT, REPO
from repair_study_utils import load, save, sha, sources, freeze, verify_frozen, event_violations, instrument_motion

sys.path.insert(0,str(REPO))
from traffic import SCENARIOS, generate_scenario
from experiments import MAP_COMBOS, run_scenario
from sim import Sim
import config as C


def worker(task):
    scenario, controller, seed=task
    sc=SCENARIOS[scenario]
    pax,_=generate_scenario(sc,seed)
    fingerprint=hashlib.sha256(json.dumps([(p.id,p.t,p.o,p.d,p.group) for p in pax]).encode()).hexdigest()
    original=Sim.run; audit={}
    def checked(sim):
        counts=instrument_motion(sim);result=original(sim)
        audit.update(counts,event_time_violations=event_violations(sim),
            not_alighted_all=sum(p.t_alight is None for p in sim.pax),
            unserved_all=sum(p.t_board is None for p in sim.pax),
            generated=len(sim.pax),outside_metric_window=sum(p.t>=sc.duration for p in sim.pax))
        return result
    try:
        Sim.run=checked
        row=run_scenario((asdict(sc),controller,seed,()))
    finally: Sim.run=original
    row.update(point=scenario,input_sha256=fingerprint,dt=C.DT,**audit)
    return row


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True,type=Path)
    ap.add_argument('--prior',required=True,type=Path)
    args=ap.parse_args();out=args.output.resolve()
    reference=args.prior.resolve()
    old=load(reference/'manifest.json')
    canonical=lambda value:json.loads(json.dumps(value))
    assert canonical({k:v for k,v in vars(C).items() if k.isupper()})==old['config']
    assert canonical({k:asdict(v) for k,v in SCENARIOS.items()})==old['scenarios']
    assert MAP_COMBOS==old['combos']
    for name,digest in old['source_sha256'].items():
        if name not in ['building.py','sim.py']: assert sources()[name]==digest,name
    repair=load(ROOT/'published_results/motion_repair_v1/protocol.json')
    assert sources()==repair['source_hashes']
    scripts=[Path(__file__),Path(__file__).with_name('repair_study_utils.py'),Path(__file__).with_name('study_paths.py')]
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),
        design='Repaired-model S1-S6 under original placeholder configuration; no policy map refit',
        model_commit='dd06745a66eb2c21f3cd2ae6ead75220eb4dbfcc',
        scenarios=old['scenarios'],config=old['config'],controllers=MAP_COMBOS,seeds=old['seeds'],runs=360,
        checks=['identical original input fingerprints across all six controls',
                'full-population service completion, event order, speed, acceleration, displacement and floor bounds',
                'historical waiting changes retained rather than forced to match'],
        interpretation='Same original LG3..16 hypothetical settings, separate from LG2..16 timetable runs. Seed uncertainty is conditional, not physical calibration.',
        reference_hashes={n:sha(reference/n) for n in ['manifest.json','raw.json','input_audit.json']})
    digest=freeze(out,protocol,scripts);start=time.time();rows=[]
    tasks=[(s,c,seed) for s in SCENARIOS for c in MAP_COMBOS for seed in old['seeds']]
    print('Frozen S1-S6 protocol: 360 runs',flush=True)
    with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(worker,task) for task in tasks]
        for i,f in enumerate(as_completed(futures),1):
            row=f.result();rows.append(row)
            save(out/'runs'/f'{row["point"]}_{row["combo"]}_{row["seed"]}.json',row)
            if i%36==0:print(f'{i}/360 runs; {time.time()-start:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['point'],r['combo'],r['seed']))
    save(out/'raw.json',rows)
    old_inputs={(r['point'],r['seed']):r for r in load(reference/'input_audit.json')}
    for row in rows:
        prior=old_inputs[(row['point'],row['seed'])]
        assert row['input_sha256']==prior['sha256'] and row['generated']==prior['generated']
        assert row['n']==prior['scored'] and row['stairs']==prior['stairs']
    verify_frozen(out,protocol,digest,scripts)
    assert protocol['reference_hashes']=={n:sha(reference/n) for n in protocol['reference_hashes']}
    keys=['unserved_all','not_alighted_all','event_time_violations','physical_constraint_violations']
    save(out/'execution.json',dict(runs=len(rows),seconds=time.time()-start,protocol_hash=digest,
        original_input_matches=len(rows),source_unchanged=True,
        anomaly_totals={k:sum(r[k] for r in rows) for k in keys},
        anomalous_runs=sum(any(r[k] for k in keys) for r in rows)))
    print('FINISHED S1-S6',flush=True)


if __name__=='__main__':main()
