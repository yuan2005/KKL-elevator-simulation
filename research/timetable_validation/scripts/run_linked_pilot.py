"""Isolated LG2..16 configuration; paired static-controller pilot."""
from pathlib import Path
import sys,json,hashlib,time,platform
from concurrent.futures import ProcessPoolExecutor,as_completed
import numpy as np
from scipy import stats
from timetable_demand import ROOT,DATA,read,save,sessions_for_day,select_sessions,PARAMS
from linked_demand import generate_linked

from study_paths import REPO
from study_paths import OUTPUT
OUT=OUTPUT/'linked_pilot_2026-10-26_v1'
CASES={'A_no_link_low':(0,0),'B_link_low':(.2,0),'C_no_link_mixed':(0,.5),'D_link_mixed':(.2,.5)}

def init_worker():
    sys.path.insert(0,str(REPO))
    import config as C
    assert 'building' not in sys.modules
    C.FLOORS=['LG2','LG1','G']+[str(i) for i in range(1,17)]
    C.FLOOR_WEIGHTS_OFFICE={f:w for f,w in C.FLOOR_WEIGHTS_OFFICE.items() if f in C.FLOORS}
    C.FLOOR_WEIGHTS_CLASS={f:w for f,w in C.FLOOR_WEIGHTS_CLASS.items() if f in C.FLOORS}

def task(args):
    case,seed,selected,begin,end=args
    from building import IDX
    from traffic import Passenger
    from sim import Sim
    from controllers import Static
    from strategies import make_combos
    from metrics import summarize
    demand=generate_linked(selected,seed,begin,end,*CASES[case]);trips=demand['trips']
    fingerprint=hashlib.sha256(json.dumps(trips,sort_keys=True).encode()).hexdigest()
    rows=[];session_by={s['session_id']:s for s in selected}
    for combo in ['C0','C1','C7']:
        pax=[Passenger(i,p['t_seconds'],IDX[p['origin']],IDX[p['destination']],i) for i,p in enumerate(trips)]
        sim=Sim(pax,Static(make_combos()[combo]),gen_end=end-begin).run()
        m=summarize(sim);m.update(case=case,seed=seed,combo=combo,input_sha256=fingerprint,
                                 not_alighted=sum(p.t_alight is None for p in pax))
        assert m['n']==len(trips)
        m['late_transfer_lift_count']=sum(p.t_alight is None or p.t_alight+begin>session_by[t['destination_session']]['start_min']*60
             for p,t in zip(pax,trips) if t['phase']=='transfer')
        for layer in ['class','background','through']:
            part=summarize(sim,keep=lambda p:trips[p.id]['layer']==layer)
            m[layer+'_awt']=part['awt'];m[layer+'_n']=part['n']
        g=summarize(sim,keep=lambda p:p.o==IDX['G']);m['G_awt']=g['awt'];m['G_n']=g['n']
        # Check that pre-generated chains do not request a later lift before previous alighting.
        last={};violations=0
        for p,t in zip(pax,trips):
            person=t.get('person')
            if person is None:continue
            if person in last and (last[person] is None or last[person]>p.t):violations+=1
            last[person]=p.t_alight
        m['chain_availability_violations']=violations
        rows.append(m)
    if seed==2026:save(OUT/(case+'_input.json'),demand)
    return rows,dict(case=case,seed=seed,input_sha256=fingerprint,**demand['audit'])

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    if (OUT/'raw.json').exists():raise RuntimeError('Output exists; preserve completed run')
    sessions=sessions_for_day(read('semester1_kkl_dated_candidates.json'),'2026-10-26')
    selected,excluded=select_sessions(sessions,{r['room']:r for r in read('room_registry.json')},'capacity_assumption')
    begin=(min(s['start_min'] for s in sessions)-15)*60;end=(max(s['end_min'] for s in sessions)+15)*60
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')}
    save(OUT/'manifest.json',dict(date='2026-10-26',seeds=list(range(2026,2036)),cases=CASES,
        selected_sessions=selected,excluded_sessions=excluded,window=[begin,end],input_assumptions=PARAMS,
        overrides=dict(floors=['LG2','LG1','G']+[str(i) for i in range(1,17)],
        transfer_gap_min=[10,30],transfer_departure_delay_seconds=[0,120],
        background_intents_per_5min=12,through_intents_per_5min=6,
        background_high_floor_range='4..16',background_direction_split=[.3,.3,.4],
        through_generation='independent Poisson/uniform, not fixed 30 percent',
        no_link_class_model='all unlinked class endpoints go to/from entrances; differs from v0'),
        original_source_hashes=hashes,script_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(__file__).with_name('linked_demand.py'),Path(__file__).with_name('timetable_demand.py')]},python=platform.python_version()))
    start=time.time();rows=[];audits=[]
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        fs=[pool.submit(task,(case,seed,selected,begin,end)) for case in CASES for seed in range(2026,2036)]
        for i,f in enumerate(as_completed(fs),1):
            rr,aa=f.result();rows.extend(rr);audits.append(aa)
            print(f'Completed {i}/40 inputs ({3*i}/120 runs); {time.time()-start:.1f}s',flush=True)
    rows.sort(key=lambda r:(r['case'],r['seed'],r['combo']))
    save(OUT/'raw.json',rows);save(OUT/'input_audits.json',audits)
    summaries=[]
    for case in CASES:
        for combo in ['C0','C1','C7']:
            rs=sorted([r for r in rows if r['case']==case and r['combo']==combo],key=lambda r:r['seed'])
            base=sorted([r for r in rows if r['case']==case and r['combo']=='C1'],key=lambda r:r['seed'])
            a=np.array([r['awt'] for r in rs]);d=a-np.array([r['awt'] for r in base]);ci=float(stats.t.ppf(.975,9)*d.std(ddof=1)/np.sqrt(10))
            summaries.append(dict(case=case,combo=combo,awt=float(a.mean()),paired_delta_vs_C1=float(d.mean()),
                paired_delta_ci95=[float(d.mean()-ci),float(d.mean()+ci)],
                mean_p90=float(np.mean([r['p90'] for r in rs])),mean_lwt=float(np.mean([r['lwt'] for r in rs])),
                mean_n=float(np.mean([r['n'] for r in rs])),mean_G_awt=float(np.mean([r['G_awt'] for r in rs])),
                unserved=sum(r['unserved'] for r in rs),not_alighted=sum(r['not_alighted'] for r in rs),
                chain_availability_violations=sum(r['chain_availability_violations'] for r in rs),
                late_transfer_lift_count=sum(r['late_transfer_lift_count'] for r in rs)))
    for case in CASES:
        for seed in range(2026,2036):assert len({r['input_sha256'] for r in rows if r['case']==case and r['seed']==seed})==1
    assert hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')}
    save(OUT/'summary.json',summaries)
    save(OUT/'verification.json',dict(runs=len(rows),inputs=len(audits),original_source_unchanged=True,paired_input_hashes_equal=True,elapsed_seconds=time.time()-start))
    print(json.dumps(summaries,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
