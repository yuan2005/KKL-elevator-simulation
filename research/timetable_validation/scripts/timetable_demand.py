"""Auditable aggregate travel-demand prototype; no student identity inference."""
from pathlib import Path
from collections import defaultdict, Counter
import json, hashlib, argparse
import numpy as np

from study_paths import ROOT, DATA, OUTPUT
FLOORS = ['LG2','LG1','G']+[str(i) for i in range(1,17)]
PARAMS = dict(attendance=0.8, missing_headcount_capacity_fraction=0.6,
              inbound_from_entrance=0.85, outbound_to_entrance=0.8,
              entrance_G=0.6, stairs_one_floor=0.7, stairs_two_floors=0.4,
              background_persons_per_5min=12, thru_share_of_lift_trips=0.3,
              consecutive_gap_minutes=10)

def minute(hm):
    h,m=map(int,hm.split(':'));return h*60+m

def read(name):return json.loads((DATA/name).read_text(encoding='utf-8'))
def save(path,value):
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')

def sessions_for_day(events,day):
    """Merge same class/room short gaps; identify clashes before excluding anything."""
    groups=defaultdict(list)
    for e in events:
        if e['date']==day:groups[(e['class_number'],e['room'])].append(e)
    result=[]
    for _,es in sorted(groups.items()):
        current=None
        for e in sorted(es,key=lambda e:e['start_time']):
            start,end=minute(e['start_time']),minute(e['end_time'])
            if current is not None and start<=current['end_min']+PARAMS['consecutive_gap_minutes']:
                current['end_min']=max(end,current['end_min'])
                current['source_events'].append(e['event_id'])
                current['source_rows'].extend(e['source_rows'])
                current['flags']=sorted(set(current['flags']+e['flags']))
                assert current['headcount_snapshot']==e['business_headcount_snapshot']
            else:
                current=dict(class_number=e['class_number'],course_code=e['course_code'],
                    class_section=e['class_section'],room=e['room'],date=day,start_min=start,end_min=end,
                    source_events=[e['event_id']],source_rows=list(e['source_rows']),flags=list(e['flags']),
                    headcount_snapshot=e['business_headcount_snapshot'],conflicting_sessions=[])
                result.append(current)
    result.sort(key=lambda s:(s['start_min'],s['room'],s['class_number']))
    for i,s in enumerate(result):s['session_id']=f'{day}-C{i+1:03d}'
    for i,a in enumerate(result):
        for b in result[i+1:]:
            if a['room']==b['room'] and max(a['start_min'],b['start_min'])<min(a['end_min'],b['end_min']):
                a['conflicting_sessions'].append(b['session_id']);b['conflicting_sessions'].append(a['session_id'])
    return result

def select_sessions(sessions,rooms,mode):
    selected=[];excluded=[]
    for s in sessions:
        r=rooms[s['room']]; reasons=[]
        if s['conflicting_sessions']:reasons.append('unresolved_shared_room')
        if r['floor'] is None:reasons.append('unverified_floor')
        if any('reading_week' in f or 'after_general_teaching_end' in f or 'cross_source_schedule_difference' in f for f in s['flags']):
            reasons.append('calendar_or_schedule_review')
        n=s['headcount_snapshot']; provenance='business_2026-10-31_snapshot'
        if n is None:
            if mode=='capacity_assumption' and r['capacity'] is not None:
                n=round(r['capacity']*PARAMS['missing_headcount_capacity_fraction']);provenance='assumed_60pct_room_capacity'
            else:reasons.append('missing_headcount')
        if reasons:
            excluded.append(dict(session_id=s['session_id'],course_code=s['course_code'],room=s['room'],reasons=reasons))
        else:
            selected.append(dict(s,floor=r['floor'],headcount=int(n),headcount_basis=provenance,
                capacity=r['capacity'],headcount_exceeds_capacity=r['capacity'] is not None and n>r['capacity']))
    return selected,excluded

def generate(selected,seed,window_start,window_end,extra=False):
    rng=np.random.default_rng(seed);trips=[];ledger=Counter();class_counts=[]
    def stream(label):
        digest=hashlib.sha256(f'{seed}:{label}'.encode()).digest()
        return np.random.default_rng(int.from_bytes(digest[:8],'big'))
    def entry():return 'G' if rng.random()<PARAMS['entrance_G'] else 'LG2'
    # Aggregate transfers use only verified teaching floors; not linked student itineraries.
    teaching=['LG1','1','2','3']
    def other_floor(f):return str(rng.choice([x for x in teaching if x!=f]))
    def add(t,o,d,layer,session=None,phase=None,stairs=True):
        assert o!=d and window_start<=t<window_end,(t,window_start,window_end)
        ledger[layer+'_intent']+=1
        gap=abs(FLOORS.index(o)-FLOORS.index(d))
        probability={1:PARAMS['stairs_one_floor'],2:PARAMS['stairs_two_floors']}.get(gap,0) if stairs else 0
        if rng.random()<probability:ledger[layer+'_stairs']+=1;return
        ledger[layer+'_lift']+=1
        trips.append(dict(t_seconds=t-window_start,origin=o,destination=d,layer=layer,
                          session_id=session,phase=phase))
    for s in selected:
        rng=stream(s['session_id'])
        n=int(rng.binomial(s['headcount'],PARAMS['attendance']))
        class_counts.append(dict(session_id=s['session_id'],headcount=s['headcount'],attendance_draw=n,
                                headcount_basis=s['headcount_basis']))
        for _ in range(n):
            f=s['floor'];start=s['start_min']*60;end=s['end_min']*60
            incoming=entry() if rng.random()<PARAMS['inbound_from_entrance'] else other_floor(f)
            outgoing=entry() if rng.random()<PARAMS['outbound_to_entrance'] else other_floor(f)
            add(float(rng.triangular(start-720,start-240,start+120)),incoming,f,'class',s['session_id'],'arrival')
            add(float(end+min(rng.exponential(90),480)),f,outgoing,'class',s['session_id'],'departure')
    if extra:
        rng=stream('background')
        # Explicit synthetic residual background, NOT estimated from timetable or observed queues.
        for m in range(window_start,window_end,60):
            for _ in range(int(rng.poisson(PARAMS['background_persons_per_5min']/5))):
                k=int(rng.choice(3,p=[.3,.3,.4]));f=str(rng.choice(teaching))
                o,d=(entry(),f) if k==0 else ((f,entry()) if k==1 else (f,other_floor(f)))
                add(float(m+rng.uniform(0,60)),o,d,'background')
        rng=stream('through')
        share=PARAMS['thru_share_of_lift_trips']
        n=int(round(len(trips)*share/(1-share)))
        base=[p['t_seconds']+window_start for p in trips]
        for _ in range(n):
            anchor=float(rng.choice(base));low=max(window_start,anchor-120);high=min(window_end,anchor+120)
            t=float(rng.uniform(low,high));o,d=('LG2','G') if rng.random()<.5 else ('G','LG2')
            add(t,o,d,'through',stairs=False)
    trips.sort(key=lambda p:p['t_seconds'])
    for i,p in enumerate(trips):p['id']=i
    for layer in ['class','background','through']:
        assert ledger[layer+'_intent']==ledger[layer+'_stairs']+ledger[layer+'_lift']
    assert ledger['class_intent']==2*sum(c['attendance_draw'] for c in class_counts)
    return trips,dict(ledger),class_counts

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--date',default='2026-10-26');ap.add_argument('--seed',type=int,default=2026)
    a=ap.parse_args();events=read('semester1_kkl_dated_candidates.json');rooms={r['room']:r for r in read('room_registry.json')}
    sessions=sessions_for_day(events,a.date)
    if not sessions:raise ValueError('No source sessions on requested date')
    out=OUTPUT/f'demand_pilot_{a.date}_seed{a.seed}';out.mkdir(parents=True,exist_ok=True)
    # Covers all source sessions, not merely those selected. 15 min padding before and after.
    begin=(min(s['start_min'] for s in sessions)-15)*60;finish=(max(s['end_min'] for s in sessions)+15)*60
    save(out/'sessions.json',sessions)
    results={}
    for mode,extra in [('known_headcounts',False),('capacity_assumption',False),('capacity_assumption_background',True)]:
        selected,excluded=select_sessions(sessions,rooms,'capacity_assumption' if extra else mode)
        trips,ledger,counts=generate(selected,a.seed,begin,finish,extra)
        assert generate(selected,a.seed,begin,finish,extra)==(trips,ledger,counts)
        save(out/(mode+'.json'),dict(selected_sessions=selected,excluded_sessions=excluded,ledger=ledger,
              attendance_draws=counts,trips=trips))
        results[mode]=dict(selected_sessions=len(selected),excluded_sessions=len(excluded),
            exclusion_reason_counts=dict(Counter(reason for s in excluded for reason in s['reasons'])),
            headcount_sources=dict(Counter(s['headcount_basis'] for s in selected)),ledger=ledger,
            floor_origins=dict(Counter(p['origin'] for p in trips)),floor_destinations=dict(Counter(p['destination'] for p in trips)))
    summary=dict(date=a.date,seed=a.seed,source_records=sum(e['date']==a.date for e in events),
        merged_sessions=len(sessions),merged_multi_record_sessions=sum(len(s['source_events'])>1 for s in sessions),
        window_start_seconds_from_midnight=begin,window_end_seconds_from_midnight=finish,
        assumptions=PARAMS,results=results,checks='repeatable seed; conservation; within window; no identical origin/destination',
        source_hashes={n:hashlib.sha256((DATA/n).read_bytes()).hexdigest() for n in ['semester1_kkl_dated_candidates.json','room_registry.json']})
    save(out/'summary.json',summary)
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,ax=plt.subplots(figsize=(11,4))
    last=json.loads((out/'capacity_assumption_background.json').read_text(encoding='utf-8'))
    bins=np.arange(begin,finish+300,300)/3600
    for layer in ['class','background','through']:
        ts=[(p['t_seconds']+begin)/3600 for p in last['trips'] if p['layer']==layer]
        values,_=np.histogram(ts,bins=bins);ax.step(bins[:-1],values,where='post',label=layer)
    ax.set(xlabel='Hour of day',ylabel='Lift trip arrivals / 5 min',title=f'{a.date}: partial timetable + assumed demand (NOT measured traffic)')
    ax.legend();fig.tight_layout();fig.savefig(out/'demand_profile.png',dpi=150);plt.close(fig)
    print(json.dumps(summary,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
