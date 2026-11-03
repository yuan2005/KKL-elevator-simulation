"""Low-background missing-headcount check across three previously studied weeks."""
import argparse,hashlib,json,time
from concurrent.futures import ProcessPoolExecutor,as_completed
from datetime import datetime,timezone
from pathlib import Path
from study_paths import ROOT
from repair_study_utils import load,save,sha,sources,freeze,verify_frozen
from run_headcount_sensitivity import with_missing_fraction
from run_crossweek import worker as paired_worker
from run_background_sensitivity import layer_fingerprint,KEYS
from run_linked_pilot import init_worker
from linked_demand import generate_linked
from timetable_demand import sessions_for_day,select_sessions
import run_adaptive

FRACTIONS=[.4,.6,.8]
STEPS=[.25,.125]
CONTROLLERS=['C1','C7','D10']


from run_repaired_headcount import fingerprint,verify_isolation


def worker(job):
    week,day,seed,selected,begin,end,baseline=job
    params=dict(run_adaptive.BASE,**run_adaptive.VARIANTS['low'])
    reference=generate_linked(selected,seed,begin,end,**params)
    assert {r['input_sha256'] for r in baseline}=={fingerprint(reference)}
    assert with_missing_fraction(selected,.6)==selected
    records=[];checks=[]
    for fraction in FRACTIONS:
        adjusted=with_missing_fraction(selected,fraction)
        demand=reference if fraction==.6 else generate_linked(adjusted,seed,begin,end,**params)
        isolation=verify_isolation(selected,adjusted,reference,demand)
        audit=demand['audit']
        checks.append(dict(week=week,date=day,seed=seed,capacity_fraction=fraction,input_sha256=fingerprint(demand),n=len(demand['trips']),
            ledger=audit['ledger'],attendance_slots=audit['attendance_slots'],transfer_links=audit['transfer_links'],
            selected_sessions=len(selected),known_headcount_sum=sum(s['headcount'] for s in adjusted if s['headcount_snapshot'] is not None),
            assumed_headcount_sum=sum(s['headcount'] for s in adjusted if s['headcount_snapshot'] is None),
            background_fingerprint=layer_fingerprint(demand,'background'),through_fingerprint=layer_fingerprint(demand,'through'),**isolation))
        if fraction==.6:continue
        result=paired_worker((week,day,'low',seed,adjusted,begin,end,STEPS,CONTROLLERS))
        assert result['runs'][0]['input_audit']==audit
        for grid in result['runs']:
            for row in grid['rows']:
                assert row['input_sha256']==fingerprint(demand)
                row['capacity_fraction']=fraction
        result['capacity_fraction']=fraction;records.append(result)
    return dict(week=week,date=day,seed=seed,records=records,input_checks=checks)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--baseline',required=True,type=Path)
    ap.add_argument('--data-dir',required=True,type=Path);ap.add_argument('--output',required=True,type=Path);a=ap.parse_args()
    public=ROOT/'published_results/crossweek_repaired_v1';p=load(a.baseline/'protocol.json')
    prior={n:sha(a.baseline/n) for n in ['protocol.json','execution.json','raw.json']}
    assert prior=={n:sha(public/n) for n in prior}
    assert sources()==p['source_hashes']==load(ROOT/'published_results/motion_repair_v1/protocol.json')['source_hashes']
    assert p['base']==run_adaptive.BASE and p['variants']==['low','mixed','high'] and p['steps']==STEPS and p['controllers']==CONTROLLERS
    revision=load(a.data_dir/'revision.json');assert revision==p['revision']
    datahashes={n:sha(a.data_dir/n) for n in revision['new_hashes']};assert datahashes==revision['new_hashes']
    rows=[r for r in load(a.baseline/'raw.json') if r['variant']=='low'];assert len(rows)==270
    coverage=load(a.baseline/'coverage_private.json');jobs=[];daily=[]
    events=load(a.data_dir/'semester1_kkl_dated_candidates.json');rooms={r['room']:r for r in load(a.data_dir/'room_registry.json')}
    for day in p['dates']:
        sessions=sessions_for_day(events,day);selected,excluded=select_sessions(sessions,rooms,'capacity_assumption')
        assert selected==coverage[day]['selected'] and excluded==coverage[day]['excluded']
        begin,end=coverage[day]['window']
        assert [begin,end]==[(min(s['start_min'] for s in sessions)-15)*60,(max(s['end_min'] for s in sessions)+15)*60]
        daily.append(dict(week=coverage[day]['week'],date=day,selected=len(selected),known=sum(s['headcount_snapshot'] is not None for s in selected),
            assumed=sum(s['headcount_snapshot'] is None for s in selected),excluded=len(excluded)))
        for seed in p['seeds']:
            base=[r for r in rows if (r['date'],r['seed'])==(day,seed)];assert len(base)==6
            jobs.append((coverage[day]['week'],day,seed,selected,begin,end,base))
    scripts=[Path(__file__).with_name(n) for n in ['run_low_headcount.py','run_repaired_headcount.py','run_headcount_sensitivity.py','run_crossweek.py',
        'repair_study_utils.py','run_background_sensitivity.py','run_adaptive.py','run_linked_pilot.py','adaptive_policy.py',
        'linked_demand.py','timetable_demand.py','study_paths.py']]
    for path in scripts:
        if path.name in p['script_hashes']:assert sha(path)==p['script_hashes'][path.name]
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),model_commit=p['model_commit'],
        model_review_snapshot=dict(pr=2,state='open',formal_reviews=0,checked_date='2026-11-02',head='bb47e94fe146bc4a285547b4ef20683a994af97c'),
        design='Low-background cross-week sensitivity: only missing class headcounts change; all existing inputs/controls retained',
        weeks=p['weeks'],dates=p['dates'],seeds=p['seeds'],steps=STEPS,controllers=CONTROLLERS,variants=['low'],base=run_adaptive.BASE,variant_parameters={'low':run_adaptive.VARIANTS['low']},
        fractions=FRACTIONS,reference_fraction=.6,new_fractions=[.4,.8],runs=540,reused_runs=270,paired_inputs=135,
        revision=revision,baseline_hashes=prior,coverage_hash=sha(a.baseline/'coverage_private.json'),
        primary='D10-C1 and D10-C7 AWT at each fraction/grid, overall and per week; change of each contrast relative to 60 percent',
        secondary='C7-C1, P90, long-wait percentage, G/non-G waits, switches, demand accounting and six anomaly counts',
        pairing='Same trips across strategies/grids at each fraction. Common date/seeds across fractions; known attendance and independent background/through movements unchanged. Missing attendance and transfer chains may change.',
        uncertainty='95% t intervals over three seed-level means across 15 dates (or five dates per week); descriptive, unadjusted for multiple comparisons',
        decision='Keep thresholds fixed; report ranking/sign and contrast changes. The original 0.25 s policy screen is retained as context, not reselected on this low-only exploratory study.',
        scope='Previously examined Sep 21-25, Oct 5-9, Nov 16-20; low background only. This follow-up was motivated by observed low-background benefit, so is sensitivity analysis rather than fresh independent policy evaluation. 40/60/80 percent are exploratory assumed headcounts before 80 percent attendance. High/mixed cross-week interactions and the reserve week are not run.')
    digest=freeze(a.output,protocol,scripts);save(a.output/'reused_baselines.json',rows);save(a.output/'daily_coverage.json',daily)
    save(a.output/'coverage_private.json',coverage)
    start=time.time();new=[];checks=[]
    print('FROZEN: 540 new runs plus 270 exact reused low-background baselines',flush=True)
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        for i,f in enumerate(as_completed([pool.submit(worker,j) for j in jobs]),1):
            r=f.result();save(a.output/'runs'/f"{r['date']}_{r['seed']}.json",r)
            new.extend(row for record in r['records'] for grid in record['runs'] for row in grid['rows']);checks.extend(r['input_checks'])
            print(f'{i}/45 date/seed inputs; {i*12}/540 new runs; {time.time()-start:.0f}s',flush=True)
    new.sort(key=lambda r:(r['date'],r['seed'],r['capacity_fraction'],r['dt'],r['controller']))
    checks.sort(key=lambda r:(r['date'],r['seed'],r['capacity_fraction']))
    save(a.output/'raw.json',new);save(a.output/'input_checks.json',checks)
    verify_frozen(a.output,protocol,digest,scripts)
    assert datahashes=={n:sha(a.data_dir/n) for n in datahashes}
    assert prior=={n:sha(a.baseline/n) for n in prior}
    save(a.output/'execution.json',dict(runs=len(new),reused_runs=270,paired_inputs=len(checks),seconds=time.time()-start,
        protocol_hash=digest,anomaly_totals={k:sum(r[k] for r in new) for k in KEYS},
        anomalous_runs=sum(any(r[k] for k in KEYS) for r in new),source_unchanged=True,inputs_unchanged=True,baseline_unchanged=True))
    print('FINISHED low-background headcount sensitivity',flush=True)


if __name__=='__main__':main()
