"""Frozen schedule-date correction; reuse the unchanged cross-week inputs."""
import argparse,time
from pathlib import Path
from datetime import datetime,timezone
from concurrent.futures import ProcessPoolExecutor,as_completed
from repair_study_utils import load,save,sha,sources,freeze,verify_frozen
from run_crossweek import worker
from run_linked_pilot import init_worker
from run_background_sensitivity import KEYS
from study_paths import ROOT
import run_adaptive


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--preparation',required=True,type=Path)
    ap.add_argument('--baseline',required=True,type=Path)
    ap.add_argument('--data-dir',required=True,type=Path)
    ap.add_argument('--output',required=True,type=Path)
    a=ap.parse_args()
    audit=load(a.preparation/'ailt_date_reconciliation.json')
    release=load(ROOT/'published_results/public_schedule_review_v1/audit.json')
    proof=load(a.preparation/'paired_input_checks.json')
    coverage=load(a.preparation/'prepared_coverage_private.json')
    old_coverage=load(a.baseline/'coverage_private.json')
    old=load(a.baseline/'raw.json'); original=load(a.baseline/'protocol.json')
    assert old==load(ROOT/'published_results/crossweek_repaired_v1/raw.json')
    assert sources()==original['source_hashes']
    assert run_adaptive.BASE==original['base'] and run_adaptive.VARIANTS==original['variant_parameters']
    assert sha(a.preparation/'prepared_coverage_private.json')==release['private_prepared_coverage_sha256']
    assert sha(a.preparation/'paired_input_checks.json')==release['paired_checks_sha256']
    assert sha(a.preparation/'ailt_index-CKywM8pk.js')==audit['source_sha256']==release['source_sha256']
    assert all(sha(a.data_dir/n)==h for n,h in audit['input_hashes'].items())
    affected=audit['paired_input_preparation']['affected_dates']
    assert len(affected)==9 and set(coverage)==set(affected)
    dates=original['dates'];seedlist=original['seeds'];variants=original['variants']
    proof_index={(r['date'],r['variant'],r['seed']):r for r in proof}
    assert len(proof)==len(proof_index)==81
    assert set(proof_index)=={(d,v,s) for d in affected for v in variants for s in seedlist}
    jobs=[];daily=[]
    for day in dates:
        base=old_coverage[day]; current=coverage.get(day,base)
        before={s['session_id']:s for s in base['selected']}
        after={s['session_id']:s for s in current['selected']}
        assert len(after)==len(current['selected']) and all(after[k]==v for k,v in before.items())
        extra=[s for k,s in after.items() if k not in before]
        assert len(extra)==int(day in affected)
        assert all(s['course_code']=='AILT9018' and s['headcount_snapshot'] is None for s in extra)
        assert current['window']==base['window'] and current['week']==base['week']
        daily.append(dict(date=day,week=base['week'],old_selected=len(before),new_selected=len(after),
            added_sessions=len(extra),old_assumed=sum(s['headcount_snapshot'] is None for s in before.values()),
            new_assumed=sum(s['headcount_snapshot'] is None for s in after.values()),window=current['window']))
        if day in affected:
            for variant in variants:
                for seed in seedlist:
                    pr=proof_index[(day,variant,seed)]
                    assert all(pr[k] for k in ['baseline_hash_matches','retained_attendance_matches','background_and_through_match'])
                    assert {r['input_sha256'] for r in old if (r['date'],r['variant'],r['seed'])==(day,variant,seed)}=={pr['old_sha256']}
                    jobs.append((base['week'],day,variant,seed,current['selected'],*current['window'],original['steps'],original['controllers']))
    paths=[Path(__file__).with_name(n) for n in ['run_schedule_revision.py','run_crossweek.py','repair_study_utils.py',
        'run_background_sensitivity.py','run_adaptive.py','run_linked_pilot.py','adaptive_policy.py','linked_demand.py','timetable_demand.py','study_paths.py']]
    for p in paths:
        if p.name in original['script_hashes']: assert sha(p)==original['script_hashes'][p.name]
    inputs=[a.preparation/n for n in ['ailt_date_reconciliation.json','prepared_coverage_private.json','paired_input_checks.json','ailt_index-CKywM8pk.js']]
    inputs+=[a.baseline/n for n in ['raw.json','protocol.json','coverage_private.json']]
    tracked={str(p.resolve()):sha(p) for p in inputs}
    protocol={k:original[k] for k in ['model_commit','dates','weeks','seeds','variants','variant_parameters','base','steps','controllers','frozen_selection_hash','benefit_screen','uncertainty']}
    protocol.update(created_utc=datetime.now(timezone.utc).isoformat(),design='AILT9018 public date correction with stable original session IDs',
        affected_dates=affected,new_runs=486,reused_affected_baselines=486,reused_unchanged_rows=324,comparison_rows_per_version=810,
        paired_inputs=81,input_hashes=audit['input_hashes'],preparation_hashes={p.name:sha(p) for p in inputs[:4]},
        baseline_hash=sha(a.baseline/'raw.json'),baseline_protocol_hash=sha(a.baseline/'protocol.json'),
        public_source_url=audit['source_url'],public_source_sha256=audit['source_sha256'],
        primary='Full 15-date changes in seed-paired AWT contrasts D10-C1 and D10-C7; also report each week/background',
        secondary='C7-C1; P90, long-wait fraction, G/non-G mean waiting, switches, demand and numerical anomalies',
        scope='Current published subclass dates; new class headcounts use existing 60% capacity assumption and 80% attendance. No threshold search or reserve-week simulation.',
        pairing='Old retained session IDs and original simulation windows; retained attendance and independent background/through movements match. New classes may change transfer chains.')
    digest=freeze(a.output,protocol,paths)
    save(a.output/'reused_baselines.json',old)
    save(a.output/'daily_coverage.json',daily)
    save(a.output/'coverage_private.json',coverage)
    save(a.output/'preparation_checks.json',proof)
    start=time.time();rows=[];checks=[]
    print('FROZEN: 81 paired inputs; 486 new simulations; 810 exact archived comparison rows',flush=True)
    with ProcessPoolExecutor(max_workers=4,initializer=init_worker) as pool:
        for i,f in enumerate(as_completed([pool.submit(worker,j) for j in jobs]),1):
            r=f.result(); pr=proof_index[(r['date'],r['variant'],r['seed'])]
            check=r['input_check']
            assert check['input_sha256']==pr['new_sha256'] and check['n']==pr['new_n']
            save(a.output/'runs'/f"{r['date']}_{r['variant']}_{r['seed']}.json",r)
            rows.extend(row for g in r['runs'] for row in g['rows']);checks.append(check)
            if i%3==0 or i==len(jobs):print(f'{i}/81 inputs; {i*6}/486 runs; {time.time()-start:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['variant'],r['seed'],r['dt'],r['controller']))
    checks.sort(key=lambda r:(r['date'],r['variant'],r['seed']))
    assert len(rows)==486 and len(checks)==81
    save(a.output/'raw.json',rows);save(a.output/'input_checks.json',checks)
    verify_frozen(a.output,protocol,digest,paths)
    assert all(sha(Path(p))==h for p,h in tracked.items())
    assert all(sha(a.data_dir/n)==h for n,h in audit['input_hashes'].items())
    save(a.output/'execution.json',dict(runs=len(rows),inputs=len(checks),seconds=time.time()-start,protocol_hash=digest,
        anomaly_totals={k:sum(r[k] for r in rows) for k in KEYS},anomalous_runs=sum(any(r[k] for k in KEYS) for r in rows),
        source_unchanged=True,inputs_unchanged=True))
    print('FINISHED schedule revision',flush=True)


if __name__=='__main__':main()
