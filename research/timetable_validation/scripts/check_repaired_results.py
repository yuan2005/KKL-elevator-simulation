"""Verify public repaired-model aggregates without private timetables or traces."""
from collections import defaultdict
from statistics import mean
from scipy import stats
import numpy as np
from study_paths import ROOT
from repair_study_utils import load,sha

PUBLIC=ROOT/'published_results'


def batch(name,expected,model):
    folder=PUBLIC/name;p=load(folder/'protocol.json');e=load(folder/'execution.json');rows=load(folder/'raw.json')
    assert len(rows)==e['runs']==p['runs']==expected
    assert p['source_hashes']==model
    assert sha(folder/'protocol.json')==e['protocol_hash']
    assert e['anomaly_totals']=={k:sum(r[k] for r in rows) for k in e['anomaly_totals']}
    assert e['anomalous_runs']==sum(any(r[k] for k in e['anomaly_totals']) for r in rows)
    return rows


def contrasts(rows,reported):
    idx={(r['date'],r['variant'],r['seed'],r['dt'],r['controller']):r for r in rows}
    assert len(idx)==len(rows)
    for result in reported:
        by_seed=defaultdict(list);c=result['comparison'].split('-')[0]
        for r in rows:
            if r['controller']!='C1' or r['dt']!=result['dt'] or (result['variant']!='all' and r['variant']!=result['variant']):continue
            other=idx[(r['date'],r['variant'],r['seed'],r['dt'],c)]
            assert other['input_sha256']==r['input_sha256'] and other['n']==r['n']
            by_seed[r['seed']].append(other['awt']-r['awt'])
        values=np.array([mean(v) for v in by_seed.values()]);m=float(values.mean())
        half=float(stats.t.ppf(.975,len(values)-1)*stats.sem(values))
        assert len(values)==result['n_seeds']
        assert max(abs(m-result['mean']),abs(m-half-result['low']),abs(m+half-result['high']))<1e-12


def main():
    model=load(PUBLIC/'motion_repair_v1/protocol.json')['source_hashes']
    first=batch('motion_repair_v1',135,model);extra=batch('motion_repair_extra_seeds_v1',270,model)
    grids=first+extra;summary=load(PUBLIC/'motion_repair_three_seeds_v1/summary.json')
    contrasts(grids,summary['contrasts'])
    grouped=defaultdict(list)
    for r in grids:grouped[(r['date'],r['variant'],r['seed'])].append(r)
    assert len(grouped)==45
    for part in grouped.values():
        assert {(r['dt'],r['controller']) for r in part}=={(dt,c) for dt in [.25,.125,.0625] for c in ['C1','C7','D10']}
        assert len({r['input_sha256'] for r in part})==1
    assert summary['anomaly_totals']=={k:sum(r[k] for r in grids) for k in summary['anomaly_totals']}
    for entry in load(PUBLIC/'motion_repair_three_seeds_v1/provenance.json')['input_archives']:
        assert sha(PUBLIC/entry['name']/'raw.json')==entry['raw_hash']
        assert sha(PUBLIC/entry['name']/'protocol.json')==entry['protocol_hash']
    scenarios=batch('s1_s6_repaired_v2',360,model)
    sidx={(r['point'],r['combo'],r['seed']):r for r in scenarios};assert len(sidx)==360
    assert set(sidx)=={(f'S{i}',c,s) for i in range(1,7) for c in ['C0','C1','C2','C3','C4','C7'] for s in range(10)}
    for result in load(PUBLIC/'s1_s6_repaired_v2/summary.json')['summaries']:
        part=[sidx[(result['scenario'],result['controller'],s)] for s in range(10)]
        assert abs(mean(r['awt'] for r in part)-result['awt'])<1e-12
        differences=[r['awt']-sidx[(r['point'],'C1',r['seed'])]['awt'] for r in part]
        assert abs(mean(differences)-result['paired_vs_C1']['mean'])<1e-12
        assert all(r['input_sha256']==sidx[(r['point'],'C1',r['seed'])]['input_sha256'] for r in part)
    evaluation=batch('adaptive_repaired_evaluation_v1',450,model)
    ev=load(PUBLIC/'adaptive_repaired_evaluation_v1/summary.json');contrasts(evaluation,ev['contrasts'])
    old={(r['date'],r['variant'],r['seed'],r['controller']):r for r in load(PUBLIC/'adaptive_v1/evaluate/raw.json')}
    assert {(r['date'],r['variant'],r['seed'],r['dt'],r['controller']) for r in evaluation}=={
        (d,v,s,dt,c) for d,v,s,c in old for dt in [.25,.125]}
    assert all(r['input_sha256']==old[(r['date'],r['variant'],r['seed'],r['controller'])]['input_sha256'] for r in evaluation)
    for screen in ev['screens']:
        delta=next(r for r in ev['contrasts'] if r['dt']==screen['dt'] and r['variant']=='all' and r['comparison']=='D10-C1')
        assert screen['saving_at_least_025']==(-delta['mean']>=.25)
        assert screen['seed_ci_upper_below_zero']==(delta['high']<0)
        assert screen['no_variant_regression_over_05']==all(r['mean']<=.5 for r in ev['contrasts'] if r['dt']==screen['dt'] and r['variant']!='all' and r['comparison']=='D10-C1')
        assert screen['zero_service_chain_anomalies']==all(r[k]==0 for r in evaluation if r['dt']==screen['dt'] for k in ['unserved','not_alighted','chain_availability_violations'])
        assert screen['passes_original_screen']==all(v for k,v in screen.items() if k not in ['dt','passes_original_screen'])
    print('PASS: 1215 unique repaired-model runs (135 prior + 1080 new), paired inputs, seed intervals and unchanged acceptance screens.')
    print('Historical original-model archive: 2145 separate runs. Combined 405-row grid analysis adds no simulations.')
    print('Private passenger traces / full strata are checked locally by report_repaired_studies.py, not by this public-only check.')


if __name__=='__main__':main()
