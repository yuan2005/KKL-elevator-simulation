"""Freeze a candidate on development dates before evaluating held-out schedule dates."""
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor,as_completed
import json,hashlib,time,argparse
import numpy as np
from scipy import stats
from timetable_demand import ROOT,DATA,read,save,sessions_for_day,select_sessions
from linked_demand import generate_linked
from run_linked_pilot import init_worker,REPO

from study_paths import OUTPUT
OUT=OUTPUT/'adaptive_v1'
BASE=dict(transfer_fraction=.2,high_share=.5,attendance=.8,background_rate=12,through_rate=6,top=16)
VARIANTS={'low':dict(high_share=0.),'mixed':dict(high_share=.5),'high':dict(high_share=1.)}
RULES={'D10':.10,'D25':.25,'D40':.40}

def worker(args):
    day,variant,seed,selected,begin,end,names=args
    from building import IDX
    from traffic import Passenger
    from sim import Sim
    from controllers import Static
    from strategies import make_combos
    from metrics import summarize
    from adaptive_policy import PastDemandController
    demand=generate_linked(selected,seed,begin,end,**dict(BASE,**VARIANTS[variant]));trips=demand['trips']
    sha=hashlib.sha256(json.dumps(trips,sort_keys=True).encode()).hexdigest();rows=[];logs={};by={s['session_id']:s for s in selected}
    for name in names:
        pax=[Passenger(i,p['t_seconds'],IDX[p['origin']],IDX[p['destination']],i) for i,p in enumerate(trips)]
        combos=make_combos();controller=Static(combos[name]) if name in ['C1','C7'] else PastDemandController(combos,pax,IDX,RULES[name])
        sim=Sim(pax,controller,end-begin).run();m=summarize(sim)
        m.update(date=day,variant=variant,seed=seed,controller=name,input_sha256=sha,
            switches=max(0,len(sim.combo_log)-1),not_alighted=sum(p.t_alight is None for p in pax))
        if hasattr(controller,'log'):logs[name]=dict(decisions=controller.log,switches=sim.combo_log)
        for label,keep in [('G',lambda p:p.o==IDX['G']),('other',lambda p:p.o!=IDX['G'])]:
            part=summarize(sim,keep=keep)
            for field in ['n','awt','lwt','p90']:m[label+'_'+field]=part[field]
        last={};bad=0
        for p,t in zip(pax,trips):
            person=t.get('person')
            if person is None:continue
            if person in last and (last[person] is None or last[person]>p.t):bad+=1
            last[person]=p.t_alight
        m['chain_availability_violations']=bad
        m['late_transfer_lift_count']=sum(p.t_alight is None or p.t_alight+begin>by[t['destination_session']]['start_min']*60
            for p,t in zip(pax,trips) if t['phase']=='transfer')
        assert m['n']==len(trips)
        rows.append(m)
    return dict(date=day,variant=variant,seed=seed,rows=rows,logs=logs,input_audit=demand['audit'])

def main():
    ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['develop','evaluate']);phase=ap.parse_args().phase
    folder=OUT/phase;folder.mkdir(parents=True,exist_ok=True);(folder/'runs').mkdir(exist_ok=True)
    if (folder/'raw.json').exists():raise RuntimeError('Completed phase exists')
    if phase=='develop':
        dates=[f'2026-10-{d}' for d in range(26,31)];seeds=list(range(4000,4003));names=['C1','C7']+list(RULES)
    else:
        frozen=json.loads((OUT/'frozen_selection.json').read_text(encoding='utf-8'))
        dates=[f'2026-11-{d:02}' for d in range(2,7)];seeds=list(range(5000,5005));names=['C1','C7',frozen['candidate']]
    events=read('semester1_kkl_dated_candidates.json');rooms={r['room']:r for r in read('room_registry.json')}
    coverage={};jobs=[]
    for day in dates:
        sessions=sessions_for_day(events,day);ss,excluded=select_sessions(sessions,rooms,'capacity_assumption')
        coverage[day]=dict(candidates=len(sessions),selected=ss,excluded=excluded)
        begin=(min(s['start_min'] for s in sessions)-15)*60;end=(max(s['end_min'] for s in sessions)+15)*60
        for variant in VARIANTS:
            for seed in seeds:jobs.append((day,variant,seed,ss,begin,end,names))
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')}
    protocol=dict(phase=phase,dates=dates,seeds=seeds,controllers=names,base=BASE,variants=VARIANTS,
        thresholds=RULES,observation='past [t-300,t), update every 60s, destinations assumed visible at request',
        feature_high='fraction of all past-window lift trips with either endpoint >=4/F; NOT background input high_share',
        enter='n>=10, from_entry>=0.70, high_endpoint<=h',leave='n<10 or from_entry<0.65 or high_endpoint>h+0.05',
        stabilization='two consecutive overlapping-window decisions; dwell>=600s; initial C1; freeze during drain',
        candidate_selection='minimum development equal-date/variant/seed mean AWT, tie within 1e-9 picks lower threshold',
        comparison_baseline='best fixed C1/C7 selected on development; both fixed strategies retained in evaluation',
        exploratory_success='held-out mean saving >=0.25s, paired Monte Carlo CI upper<0, no variant mean regression >0.5s; zero unserved/incomplete/chain conflicts',
        warning='Not independent real traffic validation; adjacent-week schedules repeat. Thresholds and margins arbitrary candidates, not sourced physical limits.',
        source_hashes=hashes,script_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(__file__).with_name('adaptive_policy.py'),Path(__file__).with_name('linked_demand.py'),Path(__file__).with_name('timetable_demand.py'),Path(__file__).with_name('run_linked_pilot.py')]},
        data_hashes={n:hashlib.sha256((DATA/n).read_bytes()).hexdigest() for n in ['semester1_kkl_dated_candidates.json','room_registry.json']},
        inputs=len(jobs),runs=len(jobs)*len(names))
    save(folder/'protocol.json',protocol);save(folder/'coverage.json',coverage)
    start=time.time();rows=[]
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        fs=[pool.submit(worker,j) for j in jobs]
        for i,f in enumerate(as_completed(fs),1):
            r=f.result();rows.extend(r['rows']);save(folder/'runs'/f"{r['date']}_{r['variant']}_{r['seed']}.json",r)
            if i%5==0:print(phase,i,'/',len(jobs),'inputs;',round(time.time()-start),'seconds',flush=True)
    save(folder/'raw.json',rows)
    assert hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')}
    means={name:float(np.mean([r['awt'] for r in rows if r['controller']==name])) for name in names}
    save(folder/'execution.json',dict(seconds=time.time()-start,means=means,original_source_unchanged=True))
    if phase=='develop':
        chosen=min(RULES,key=lambda k:means[k]);fixed=min(['C1','C7'],key=lambda k:means[k])
        save(OUT/'frozen_selection.json',dict(candidate=chosen,threshold=RULES[chosen],fixed_baseline=fixed,
            development_means=means,heldout_not_evaluated_yet=True,
            development_raw_hash=hashlib.sha256((folder/'raw.json').read_bytes()).hexdigest(),
            protocol_hash=hashlib.sha256((folder/'protocol.json').read_bytes()).hexdigest()))
    print(json.dumps(means),flush=True)

if __name__=='__main__':main()
