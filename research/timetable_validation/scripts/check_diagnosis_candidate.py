"""Public-only consistency checks for post-hoc diagnosis and one-factor candidate."""
from collections import defaultdict
from statistics import mean
import numpy as np
from scipy import stats
from study_paths import ROOT
from repair_study_utils import load,sha


def main():
    public=ROOT/'published_results';folder=public/'repaired_window_diagnosis_v2'
    p=load(folder/'protocol.json');e=load(folder/'execution.json');d=load(folder/'summary.json')
    assert sha(folder/'protocol.json')==e['protocol_hash'] and sha(folder/'summary.json')==e['summary_hash']
    assert sha(public/'adaptive_repaired_evaluation_v1/raw.json')==p['prior_raw_hash']
    assert e['new_simulation_runs']==0 and e['reused_simulation_runs']==450
    for dt in [.25,.125]:
        for variant in ['all','low','mixed','high']:
            rows=[r for r in d['groups'] if r['dt']==dt and r['variant']==variant]
            total=next(r['contribution']['mean'] for r in rows if r['dimension']=='all')
            for dim in ['layer','origin','window','arrival_state','switch_age','layer_state','layer_age']:
                assert abs(sum(r['contribution']['mean'] for r in rows if r['dimension']==dim)-total)<1e-10
                assert abs(sum(r['share'] for r in rows if r['dimension']==dim)-1)<1e-10
    assert all(0<=r['C7_time_fraction']<=1 for r in d['decisions'])
    assert all(r['C7_time_fraction']>0 for r in d['decisions'] if r['switches']>0)
    folder=public/'persistence_three_develop_v1';p=load(folder/'protocol.json');e=load(folder/'execution.json')
    new=load(folder/'raw.json');old=load(folder/'reused_baselines.json');summary=load(folder/'summary.json')
    assert len(new)==e['runs']==p['runs']==90 and len(old)==270
    assert sha(folder/'protocol.json')==e['protocol_hash']
    original=load(public/'motion_repair_v1/raw.json')+load(public/'motion_repair_extra_seeds_v1/raw.json')
    key=lambda r:(r['date'],r['variant'],r['seed'],r['dt'],r['controller'])
    original={key(r):r for r in original}
    assert all(original[key(r)]==r for r in old)
    idx={key(r):r for r in new+old};assert len(idx)==360
    assert set(idx)=={(d,v,s,dt,c) for d in p['dates'] for v in p['variants'] for s in p['seeds'] for dt in p['steps'] for c in ['C1','C7','D10','D10P3']}
    for r in new:
        for c in ['C1','C7','D10']:
            b=idx[key(r)[:-1]+(c,)]
            assert r['input_sha256']==b['input_sha256'] and r['n']==b['n']
    assert e['anomaly_totals']=={k:sum(r[k] for r in new) for k in e['anomaly_totals']}
    for result in summary['comparisons']:
        values=defaultdict(list);baseline=result['comparison'].split('-')[1];metric=result['metric']
        for r in new:
            if r['dt']==result['dt'] and (result['variant']=='all' or r['variant']==result['variant']):
                values[r['seed']].append(r[metric]-idx[key(r)[:-1]+(baseline,)][metric])
        points=np.array([mean(v) for v in values.values()]);mu=float(points.mean())
        half=float(stats.t.ppf(.975,len(points)-1)*stats.sem(points))
        assert len(points)==result['n_seeds']
        assert max(abs(mu-result['mean']),abs(mu-half-result['low']),abs(mu+half-result['high']))<1e-10
    for screen in summary['screens']:
        def get(v,b):return next(r for r in summary['comparisons'] if r['dt']==screen['dt'] and r['variant']==v and r['comparison']=='D10P3-'+b and r['metric']=='awt')
        delta=get('all','D10');c1=get('all','C1')
        safe=all(r[k]==0 for r in new if r['dt']==screen['dt'] for k in e['anomaly_totals'])
        advance=delta['mean']<0 and delta['high']<0 and max(get(v,'D10')['mean'] for v in p['variants'])<=.5 and safe
        assert screen['passes_development_screen']==advance
        assert screen['passes_original_C1_screen']==(c1['mean']<=-.25 and c1['high']<0 and max(get(v,'C1')['mean'] for v in p['variants'])<=.5 and safe)
    assert summary['advance_candidate']==all(r['passes_development_screen'] for r in summary['screens'])
    print('PASS: diagnostic partitions reconcile; 90 new candidate runs pair with 270 unchanged baseline rows; intervals and frozen decisions verified.')
    print('Total archived unique simulations: 3450 = 2145 original-model + 1215 repaired follow-up + 90 candidate. Diagnosis adds none.')


if __name__=='__main__':main()
