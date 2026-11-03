"""Post-hoc paired passenger diagnosis; associations are not switch causal effects."""
import argparse
import bisect
from collections import defaultdict
from datetime import datetime, timezone
import gzip
from pathlib import Path
from statistics import mean

from repair_study_utils import load,save,sha,seed_interval


def read_trace(path):
    with gzip.open(path,'rt',encoding='utf-8') as f:
        import json
        return json.load(f)


def passenger_groups(arrival,switches):
    times=[s[0] for s in switches]
    i=bisect.bisect_right(times,arrival)-1
    assert i>=0
    state=switches[i][1]
    age=arrival-times[i]
    # First state assignment is not a policy switch.
    band='initial' if i==0 else ('after_switch_lt300s' if age<300 else 'after_switch_ge300s')
    return state,band


def canonical_switches(switches):
    result=[(t,name.split()[0]) for t,name in switches]
    assert result and result[0][0]==0 and all(c in ['C1','C7'] for t,c in result)
    assert all(result[i][0]<result[i+1][0] for i in range(len(result)-1))
    return result


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--prior',required=True,type=Path);ap.add_argument('--output',required=True,type=Path)
    args=ap.parse_args();prior=args.prior.resolve();out=args.output.resolve()
    p=load(prior/'protocol.json');rows=load(prior/'raw.json')
    assert len(rows)==450
    idx={(r['date'],r['variant'],r['seed'],r['dt'],r['controller']):r for r in rows}
    manifest=load(prior/'private_trace_manifest.json');trace_hashes={r['file']:r['sha256'] for r in manifest}
    run_files=sorted((prior/'runs').glob('*.json'));run_hashes={f.name:sha(f) for f in run_files}
    assert len(run_files)==75
    protocol=dict(created_utc=datetime.now(timezone.utc).isoformat(),
        design='Post-hoc diagnosis of existing repaired evaluation traces; no simulations or parameter search',
        model_commit=p['model_commit'],prior_protocol_hash=sha(prior/'protocol.json'),prior_raw_hash=sha(prior/'raw.json'),
        prior_run_hashes=run_hashes,prior_trace_manifest_hash=sha(prior/'private_trace_manifest.json'),
        script_hash=sha(Path(__file__)),
        groups='Original absolute-clock 15-minute arrival windows; origin G/other; class/background/through; D10 state at request arrival; initial / <300s / >=300s since most recent switch',
        weighting='Equal date/background/seed means. Group contribution = sum paired wait differences in group / all passengers in that input. Absent groups contribute zero; conditional means omit absent groups.',
        interpretation='Post-hoc descriptive associations; switching and state depend on demand. No causal switch cost, real passenger inference, or new untouched evaluation. Intervals use seed means, unadjusted for multiple comparisons.')
    out.mkdir(parents=True,exist_ok=False);save(out/'protocol.json',protocol)
    aggregates=[];decisions=[]
    for file in run_files:
        result=load(file);day,variant,seed=[result[k] for k in ['date','variant','seed']]
        begin,end=p['windows'][day]
        for run in result['runs']:
            dt=run['dt'];traces={}
            for c in p['controllers']:
                name=f'{day}_{variant}_{seed}_{dt}_{c}.json.gz'
                path=prior/'private_traces'/name
                assert sha(path)==trace_hashes[name]
                traces[c]=read_trace(path)['rows']
            n=len(traces['C1']);assert n==idx[(day,variant,seed,dt,'C1')]['n']
            logs=run['logs']['D10'];switches=canonical_switches(logs['switches']);ds=logs['decisions']
            assert all(passenger_groups(x['t'],switches)[0]==x['choice'] for x in ds)
            decision=dict(date=day,variant=variant,seed=seed,dt=dt,n_decisions=len(ds),switches=len(switches)-1,
                chosen_C7_fraction=mean(x['choice']=='C7' for x in ds),
                entry_condition_fraction=mean(x['n']>=10 and x['from_entry']>=.70 and x['high_endpoint']<=.10 for x in ds),
                entry_pass_but_C1_fraction=mean(x['n']>=10 and x['from_entry']>=.70 and x['high_endpoint']<=.10 and x['choice']=='C1' for x in ds),
                entry_share_below70_fraction=mean(x['from_entry']<.70 for x in ds),
                count_below10_fraction=mean(x['n']<10 for x in ds),
                high_endpoint_above10_fraction=mean(x['high_endpoint']>.10 for x in ds))
            # Time share differs from decision and passenger share.
            time_c7=sum(max(0,min(switches[i+1][0] if i+1<len(switches) else end-begin,end-begin)-t)
                        for i,(t,c) in enumerate(switches) if c=='C7' and t<end-begin)
            decision['C7_time_fraction']=time_c7/(end-begin);decisions.append(decision)
            buckets=defaultdict(lambda:dict(n=0,sum_D10_C1=0.,sum_C7_C1=0.,sum_D10_C7=0.,
                improved=0,worsened=0,unchanged=0,long_C1=0,long_D10=0))
            for a,b,c in zip(traces['C1'],traces['D10'],traces['C7']):
                # All non-outcome columns must agree; passenger indices have meaning only within the same input.
                for k in [0,1,2,3,6,7,8]: assert a[k]==b[k]==c[k]
                assert all(x[4] is not None and x[5] is not None for x in [a,b,c])
                w1,wd,w7=[x[4]-x[1] for x in [a,b,c]];delta=wd-w1
                state,band=passenger_groups(a[1],switches)
                groups=[('all','all'),('layer',a[7]),('origin',a[6]),('window',str(a[8])),
                        ('arrival_state',state),('switch_age',band),('layer_state',a[7]+'|'+state),
                        ('layer_age',a[7]+'|'+band)]
                for key in groups:
                    g=buckets[key];g['n']+=1;g['sum_D10_C1']+=delta;g['sum_C7_C1']+=w7-w1;g['sum_D10_C7']+=wd-w7
                    g['improved']+=int(delta < -1e-9);g['worsened']+=int(delta > 1e-9);g['unchanged']+=int(abs(delta)<=1e-9)
                    g['long_C1']+=int(w1>60);g['long_D10']+=int(wd>60)
            actual=idx[(day,variant,seed,dt,'D10')]['awt']-idx[(day,variant,seed,dt,'C1')]['awt']
            assert abs(buckets[('all','all')]['sum_D10_C1']/n-actual)<1e-10
            for dimension in ['layer','origin','window','arrival_state','switch_age','layer_state','layer_age']:
                parts=[g for (d,l),g in buckets.items() if d==dimension]
                assert sum(g['n'] for g in parts)==n
                assert abs(sum(g['sum_D10_C1'] for g in parts)/n-actual)<1e-10
            for (dimension,label),g in sorted(buckets.items()):
                aggregates.append(dict(date=day,variant=variant,seed=seed,dt=dt,dimension=dimension,label=label,
                    n=g['n'],total_n=n,share=g['n']/n,conditional_delta=g['sum_D10_C1']/g['n'],
                    contribution=g['sum_D10_C1']/n,C7_C1_contribution=g['sum_C7_C1']/n,
                    D10_C7_contribution=g['sum_D10_C7']/n,improved_fraction=g['improved']/g['n'],
                    worsened_fraction=g['worsened']/g['n'],unchanged_fraction=g['unchanged']/g['n'],
                    long_wait_change_percentage_points=100*(g['long_D10']-g['long_C1'])/g['n']))
    summaries=[];decision_summary=[]
    for dt in p['steps']:
        for variant in ['all']+list(p['variants']):
            selected=[r for r in aggregates if r['dt']==dt and (variant=='all' or r['variant']==variant)]
            total_inputs=len([r for r in selected if r['dimension']=='all'])
            groups=defaultdict(list)
            for r in selected:groups[(r['dimension'],r['label'])].append(r)
            for (dimension,label),part in sorted(groups.items()):
                by_seed=defaultdict(list)
                for r in part:by_seed[r['seed']].append(r['contribution'])
                inputs_per_seed=total_inputs//len(p['seeds'])
                contribution_ci=seed_interval([sum(by_seed[s])/inputs_per_seed for s in p['seeds']])
                summaries.append(dict(dt=dt,variant=variant,dimension=dimension,label=label,
                    contributing_inputs=len(part),total_inputs=total_inputs,
                    share= sum(r['share'] for r in part)/total_inputs,
                    conditional_delta_mean=mean(r['conditional_delta'] for r in part),
                    contribution=contribution_ci,
                    C7_C1_contribution=sum(r['C7_C1_contribution'] for r in part)/total_inputs,
                    D10_C7_contribution=sum(r['D10_C7_contribution'] for r in part)/total_inputs,
                    improved_fraction_mean=mean(r['improved_fraction'] for r in part),
                    worsened_fraction_mean=mean(r['worsened_fraction'] for r in part)))
            dd=[r for r in decisions if r['dt']==dt and (variant=='all' or r['variant']==variant)]
            decision_summary.append(dict(dt=dt,variant=variant,**{k:mean(r[k] for r in dd) for k in dd[0] if k not in ['date','variant','seed','dt']}))
    for dt in p['steps']:
        for variant in ['all']+list(p['variants']):
            part=[r for r in summaries if r['dt']==dt and r['variant']==variant]
            total=next(r['contribution']['mean'] for r in part if r['dimension']=='all')
            for dimension in ['layer','origin','window','arrival_state','switch_age','layer_state','layer_age']:
                assert abs(sum(r['contribution']['mean'] for r in part if r['dimension']==dimension)-total)<1e-10
    save(out/'summary.json',dict(groups=summaries,decisions=decision_summary))
    save(out/'local_input_groups.json',aggregates)
    assert run_hashes=={f.name:sha(f) for f in run_files}
    assert sha(prior/'raw.json')==protocol['prior_raw_hash']
    save(out/'execution.json',dict(reused_simulation_runs=450,new_simulation_runs=0,paired_inputs=75,
        verified_traces=len(trace_hashes),all_partition_checks_pass=True,prior_unchanged=True,
        protocol_hash=sha(out/'protocol.json'),summary_hash=sha(out/'summary.json')))
    print('Diagnosis complete: 450 existing traces, all partitions reconcile; no new simulations.')


if __name__=='__main__':main()
