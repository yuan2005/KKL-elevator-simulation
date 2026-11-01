"""Hypothetical class-cohort chains and independently controlled background OD."""
from collections import Counter
import hashlib
import numpy as np
from timetable_demand import PARAMS

def rng_for(seed,label):
    return np.random.default_rng(int.from_bytes(hashlib.sha256(f'{seed}:{label}'.encode()).digest()[:8],'big'))

def generate_linked(sessions,seed,begin,end,transfer_fraction=.2,high_share=.5,top=16,
                    attendance=None,background_rate=12,through_rate=6):
    attendance=PARAMS['attendance'] if attendance is None else attendance
    assert 0<=attendance<=1 and background_rate>=0 and through_rate>=0
    assert 0<=transfer_fraction<=1 and 0<=high_share<=1 and top>=4
    floors=['LG2','LG1','G']+[str(i) for i in range(1,top+1)]
    ordered=sorted(sessions,key=lambda s:(s['start_min'],s['session_id']))
    by={s['session_id']:s for s in ordered};tokens={};cohorts={};incoming={};outgoing={};links=[]
    for s in ordered:
        sid=s['session_id'];n=int(rng_for(seed,sid+':attendance').binomial(s['headcount'],attendance))
        cohorts[sid]=[f'{sid}:{i}' for i in range(n)]
        for token in cohorts[sid]:tokens[token]=sid
    # Greedy chronological matching is a declared scenario assumption, not inferred enrolment overlap.
    for dest in ordered:
        dk=dest['session_id'];budget=int(len(cohorts[dk])*transfer_fraction)
        sources=[s for s in ordered if 10<=dest['start_min']-s['end_min']<=30]
        sources.sort(key=lambda s:(dest['start_min']-s['end_min'],s['session_id']))
        for source in sources:
            sk=source['session_id'];remaining=int(len(cohorts[sk])*transfer_fraction)-sum(t in outgoing for t in cohorts[sk])
            count=min(budget,remaining)
            if count<=0:continue
            aa=[t for t in cohorts[sk] if t not in outgoing][:count]
            bb=[t for t in cohorts[dk] if t not in incoming][:count]
            for a,b in zip(aa,bb):
                outgoing[a]=b;incoming[b]=a;links.append(dict(source_token=a,destination_token=b,source_session=sk,destination_session=dk))
            budget-=count
    paths={}
    for token in tokens:
        if token in incoming:continue
        cur=token;chain=[]
        while True:
            assert cur not in paths
            paths[cur]=token;chain.append(cur)
            if cur not in outgoing:break
            cur=outgoing[cur]
    assert len(paths)==len(tokens)
    raw=[]
    def add(t,o,d,layer,key,**meta):
        assert begin<=t<end and o in floors and d in floors
        raw.append(dict(t_seconds=float(t-begin),origin=o,destination=d,layer=layer,trip_key=key,**meta))
    def entry(rng):return 'G' if rng.random()<.6 else 'LG2'
    for token,sid in tokens.items():
        s=by[sid];f=s['floor'];person=paths[token]
        if token not in incoming:
            r=rng_for(seed,token+':entry');t=r.triangular(s['start_min']*60-720,s['start_min']*60-240,s['start_min']*60+120)
            add(t,entry(r),f,'class',token+':entry',person=person,destination_session=sid,phase='entry')
        if token not in outgoing:
            r=rng_for(seed,token+':exit');t=s['end_min']*60+min(r.exponential(90),480)
            add(t,f,entry(r),'class',token+':exit',person=person,source_session=sid,phase='exit')
    for link in links:
        a,b=by[link['source_session']],by[link['destination_session']]
        key=link['source_token']+':transfer';r=rng_for(seed,key)
        t=a['end_min']*60+r.uniform(0,120)
        assert t<b['start_min']*60
        add(t,a['floor'],b['floor'],'class',key,person=paths[link['source_token']],phase='transfer',
            source_session=a['session_id'],destination_session=b['session_id'])
    # Same arrival times and numbers across spatial variants; only floor selection changes.
    low=['LG1','1','2','3'];high=[str(i) for i in range(4,top+1)]
    def floor_draw(r):
        high_draw=r.random();position=r.random();pool=high if high_draw<high_share else low
        return pool[min(int(position*len(pool)),len(pool)-1)]
    for layer,lam in [('background',background_rate),('through',through_rate)]:
        r=rng_for(seed,layer+':schedule');number=int(r.poisson(lam*(end-begin)/300))
        times=sorted(r.uniform(begin,end,number))
        for i,t in enumerate(times):
            key=f'{layer}:{i}';q=rng_for(seed,key+':od')
            if layer=='through':o,d=('LG2','G') if q.random()<.5 else ('G','LG2')
            else:
                kind=q.random()
                if kind<.3:o,d=entry(q),floor_draw(q)
                elif kind<.6:o,d=floor_draw(q),entry(q)
                else:o,d=floor_draw(q),floor_draw(q)
            add(t,o,d,layer,key,phase=layer)
    ledger=Counter();trips=[];movements=[]
    for p in raw:
        layer=p['layer'];ledger[layer+'_intent']+=1
        if p['origin']==p['destination']:mode='same_floor'
        else:
            gap=abs(floors.index(p['origin'])-floors.index(p['destination']))
            prob={1:.7,2:.4}.get(gap,0) if layer!='through' else 0
            mode='stairs' if rng_for(seed,p['trip_key']+':stairs').random()<prob else 'lift'
        ledger[layer+'_'+mode]+=1
        movements.append(dict(p,mode=mode))
        if mode=='lift':trips.append(p)
    trips.sort(key=lambda p:(p['t_seconds'],p['trip_key']))
    for i,p in enumerate(trips):p['id']=i
    n=len(tokens);m=len(links)
    assert ledger['class_intent']==2*n-m
    assert sum(p['phase']=='entry' for p in raw)==n-m
    assert sum(p['phase']=='exit' for p in raw)==n-m
    for layer in ['class','background','through']:
        assert ledger[layer+'_intent']==sum(ledger[layer+'_'+x] for x in ['same_floor','stairs','lift'])
    return dict(trips=trips,movements=movements,links=links,
        audit=dict(attendance_slots=n,transfer_links=m,hypothetical_people=n-m,
                   actual_inbound_link_fraction=m/n if n else 0,ledger=dict(ledger),
                   session_attendance={k:len(v) for k,v in cohorts.items()}))
