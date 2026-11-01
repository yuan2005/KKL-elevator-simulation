"""Predeclared one-factor sensitivity study of partial timetable demand."""
from pathlib import Path
import sys,json,hashlib,time
from concurrent.futures import ProcessPoolExecutor,as_completed
from timetable_demand import ROOT,DATA,read,save,sessions_for_day,select_sessions
from linked_demand import generate_linked
from run_linked_pilot import init_worker,REPO

from study_paths import OUTPUT
OUT=OUTPUT/'multiday_2026-10-26_30_v1'
DATES=[f'2026-10-{i}' for i in range(26,31)]
BASE=dict(transfer_fraction=.2,high_share=.5,attendance=.8,background_rate=12,through_rate=6,top=16)
VARIANTS={'reference':{},'attendance_60':dict(attendance=.6),'attendance_100':dict(attendance=1.),
    'background_6':dict(background_rate=6),'background_24':dict(background_rate=24),
    'high_0':dict(high_share=0.),'high_100':dict(high_share=1.),
    'transfer_0':dict(transfer_fraction=0.),'transfer_40':dict(transfer_fraction=.4)}
SEEDS=list(range(3000,3005));COMBOS=['C0','C1','C7']

def worker(args):
    day,variant,seed,selected,begin,end=args
    from building import IDX
    from traffic import Passenger
    from sim import Sim
    from controllers import Static,features
    from strategies import make_combos
    from metrics import summarize
    demand=generate_linked(selected,seed,begin,end,**dict(BASE,**VARIANTS[variant]));trips=demand['trips']
    sha=hashlib.sha256(json.dumps(trips,sort_keys=True).encode()).hexdigest()
    rows=[];by={s['session_id']:s for s in selected}
    # Input-defined peak windows, identical across strategies; not chosen from outcome.
    def peak(p):
        t=p.t+begin
        return any(s['start_min']*60-720<=t<s['start_min']*60+120 or
                   s['end_min']*60<=t<s['end_min']*60+480 for s in selected)
    for combo in COMBOS:
        pax=[Passenger(i,p['t_seconds'],IDX[p['origin']],IDX[p['destination']],i) for i,p in enumerate(trips)]
        m_features=features(pax,end-begin)
        sim=Sim(pax,Static(make_combos()[combo]),end-begin).run()
        m=summarize(sim);m.update(date=day,variant=variant,seed=seed,combo=combo,input_sha256=sha,
             not_alighted=sum(p.t_alight is None for p in pax),features=m_features)
        assert m['n']==len(trips)
        for label,keep in [('G',lambda p:p.o==IDX['G']),('other',lambda p:p.o!=IDX['G']),('peak',peak)]:
            part=summarize(sim,keep=keep)
            for field in ['n','awt','p90','lwt']:m[label+'_'+field]=part[field]
        last={};bad=0
        for p,t in zip(pax,trips):
            person=t.get('person')
            if person is None:continue
            if person in last and (last[person] is None or last[person]>p.t):bad+=1
            last[person]=p.t_alight
        m['chain_availability_violations']=bad
        m['late_transfer_lift_count']=sum(p.t_alight is None or p.t_alight+begin>by[t['destination_session']]['start_min']*60
             for p,t in zip(pax,trips) if t['phase']=='transfer')
        rows.append(m)
    return dict(date=day,variant=variant,seed=seed,rows=rows,audit=demand['audit'],input_sha256=sha)

def main():
    OUT.mkdir(parents=True,exist_ok=True);(OUT/'runs').mkdir(exist_ok=True)
    if (OUT/'raw.json').exists():raise RuntimeError('Completed results exist; preserve them')
    events=read('semester1_kkl_dated_candidates.json');rooms={r['room']:r for r in read('room_registry.json')}
    coverage={};day_inputs={}
    for day in DATES:
        sessions=sessions_for_day(events,day);selected,excluded=select_sessions(sessions,rooms,'capacity_assumption')
        day_inputs[day]=selected
        coverage[day]=dict(candidate_sessions=len(sessions),selected_sessions=selected,excluded_sessions=excluded)
    begin=min(s['start_min'] for ss in day_inputs.values() for s in ss)*60-900
    end=max(s['end_min'] for ss in day_inputs.values() for s in ss)*60+900
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')}
    manifest=dict(purpose='Exploratory multi-date one-factor sensitivity, not held-out validation or threshold training',
        dates=DATES,seeds=SEEDS,combos=COMBOS,base=BASE,variants=VARIANTS,window=[begin,end],
        planned_inputs=len(DATES)*len(SEEDS)*len(VARIANTS),planned_runs=len(DATES)*len(SEEDS)*len(VARIANTS)*3,
        selection='Fixed Monday-Friday block, no selection by outcomes; includes previously explored Monday',
        untouched_future_dates='2026-11-02 to 2026-11-06 reserved for later rule evaluation, not evaluated here',
        assumptions='44-52 or fewer eligible sessions per date; missing counts use 60% capacity; equipment remains hypothetical',
        source_sha256=hashes,data_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [DATA/'semester1_kkl_dated_candidates.json',DATA/'room_registry.json']},
        scripts_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path(__file__).with_name('linked_demand.py'),Path(__file__).with_name('timetable_demand.py'),Path(__file__).with_name('run_linked_pilot.py')]})
    save(OUT/'manifest.json',manifest);save(OUT/'coverage.json',coverage)
    jobs=[(day,v,s,day_inputs[day],begin,end) for v in VARIANTS for day in DATES for s in SEEDS]
    start=time.time();results=[]
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        fs=[pool.submit(worker,j) for j in jobs]
        for i,f in enumerate(as_completed(fs),1):
            result=f.result();results.append(result)
            save(OUT/'runs'/f"{result['date']}_{result['variant']}_{result['seed']}.json",result)
            if i%5==0:print(f'{i}/{len(jobs)} inputs; {i*3}/{len(jobs)*3} runs; {time.time()-start:.0f}s',flush=True)
    rows=[r for result in results for r in result['rows']]
    rows.sort(key=lambda r:(r['date'],r['variant'],r['seed'],r['combo']))
    save(OUT/'raw.json',rows)
    save(OUT/'input_audits.json',[{k:v for k,v in x.items() if k!='rows'} for x in results])
    assert hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')}
    save(OUT/'execution.json',dict(inputs=len(results),runs=len(rows),seconds=time.time()-start,source_unchanged=True))
    print('FINISHED',len(rows),flush=True)

if __name__=='__main__':main()
