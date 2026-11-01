"""Version-separated analysis of repair extensions, scenario baselines and frozen rules."""
import argparse
from collections import defaultdict
from pathlib import Path
from statistics import mean

from study_paths import ROOT
from repair_study_utils import load,save,sha,seed_interval


def validate_archive(folder):
    p=load(folder/'protocol.json');e=load(folder/'execution.json');r=load(folder/'raw.json')
    assert sha(folder/'protocol.json')==e['protocol_hash']
    assert len(r)==e['runs']==p['runs']
    return p,e,r


def paired_index(rows):
    index={}
    for r in rows:
        key=r['date'],r['variant'],r['seed'],r['dt'],r['controller']
        assert key not in index;index[key]=r
    return index


def contrast_rows(rows,controls=('C7','D10')):
    idx=paired_index(rows);out=[]
    for dt in sorted({r['dt'] for r in rows},reverse=True):
        for variant in ['all']+sorted({r['variant'] for r in rows}):
            baseline=[r for r in rows if r['dt']==dt and r['controller']=='C1' and (variant=='all' or r['variant']==variant)]
            for c in controls:
                seeds=defaultdict(list)
                for b in baseline:
                    other=idx[(b['date'],b['variant'],b['seed'],dt,c)]
                    assert other['input_sha256']==b['input_sha256'] and other['n']==b['n']
                    seeds[b['seed']].append(other['awt']-b['awt'])
                out.append(dict(dt=dt,variant=variant,comparison=c+'-C1',
                    **seed_interval([mean(v) for _,v in sorted(seeds.items())]),
                    seed_means={str(k):mean(v) for k,v in sorted(seeds.items())}))
    return out


def grid_report(folders,out):
    assert not out.exists();rows=[];protocols=[];checks=[]
    for folder in folders:
        p,e,r=validate_archive(folder);rows.extend(r);protocols.append(p)
        assert e['anomaly_totals']=={k:sum(x[k] for x in r) for k in e['anomaly_totals']}
        assert e['anomalous_runs']==sum(any(x[k] for k in e['anomaly_totals']) for x in r)
        local_checks=load(folder/'pair_checks.json')
        assert len(local_checks)==p['inputs']
        assert all(c['same_inputs_audits_decisions_as_original'] for c in local_checks)
        checks.extend(local_checks)
    assert all(p['source_hashes']==protocols[0]['source_hashes'] for p in protocols)
    for p in protocols:
        for key in ['dates','variants','base','controllers','steps','frozen','prior_raw_hash','prior_coverage_hash']:
            assert p[key]==protocols[0][key],key
        for name,digest in p['script_hashes'].items():
            if name!='run_repair_validation.py':assert digest==protocols[0]['script_hashes'][name]
    assert len(rows)==405 and {r['seed'] for r in rows}=={4000,4001,4002}
    idx=paired_index(rows);base=protocols[0]
    expected={(d,v,s,dt,c) for d in base['dates'] for v in base['variants'] for s in [4000,4001,4002] for dt in base['steps'] for c in base['controllers']}
    assert set(idx)==expected
    assert {(c['date'],c['variant'],c['seed']) for c in checks}=={k[:3] for k in expected}
    old=load(ROOT/'published_results/timestep_sensitivity_v1/raw.json')+load(ROOT/'published_results/timestep_third_grid_v2/raw.json')
    old_idx=paired_index(old)
    assert set(idx)==set(old_idx)
    assert all(idx[k]['input_sha256']==old_idx[k]['input_sha256'] for k in idx)
    means=[dict(dt=dt,controller=c,awt=mean(r['awt'] for r in rows if r['dt']==dt and r['controller']==c))
           for dt in base['steps'] for c in base['controllers']]
    contrasts=contrast_rows(rows);shifts=[]
    pairs=sorted({k[:3] for k in idx})
    for coarse,fine in zip(base['steps'],base['steps'][1:]):
        for quantity in ['C1','C7','D10','C7-C1','D10-C1']:
            def value(k,dt):
                if '-' in quantity:
                    return idx[k+(dt,quantity.split('-')[0])]['awt']-idx[k+(dt,'C1')]['awt']
                return idx[k+(dt,quantity)]['awt']
            values=[(k,value(k,fine)-value(k,coarse)) for k in pairs]
            seeds=defaultdict(list)
            for k,v in values:seeds[k[2]].append(v)
            shifts.append(dict(coarse=coarse,fine=fine,quantity=quantity,
                **seed_interval([mean(v) for _,v in sorted(seeds.items())]),
                mean_absolute=mean(abs(v) for _,v in values),max_absolute=max(abs(v) for _,v in values)))
    keys=['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count','event_time_violations']
    totals={k:sum(r[k] for r in rows) for k in keys}
    out.mkdir(parents=True)
    save(out/'raw.json',sorted(rows,key=lambda r:(r['date'],r['variant'],r['seed'],r['dt'],r['controller'])))
    save(out/'summary.json',dict(runs=len(rows),means=means,contrasts=contrasts,grid_shifts=shifts,anomaly_totals=totals,
        old_fine_anomalous_runs=sum(any(r[k] for k in keys[:-1]) for r in old if r['dt']==.0625),
        new_fine_anomalous_runs=sum(any(r[k] for k in keys) for r in rows if r['dt']==.0625)))
    save(out/'provenance.json',dict(source_hashes=base['source_hashes'],
        input_archives=[dict(name=f.name,raw_hash=sha(f/'raw.json'),protocol_hash=sha(f/'protocol.json'),execution_hash=sha(f/'execution.json')) for f in folders],
        note='405 combined rows = 135 existing + 270 new; combination does not add simulations. Runner CLI only adds seed selection.'))
    lines=['# 修复版三步长验证：补齐三个原开发种子','',
        '新增 270 次，与之前 135 次同模型结果合并为 405 次。5 日期 × 3 背景 × 3 原开发种子 × 3 步长 × C1/C7/D10。输入、审计和 D10 决策匹配原归档；阈值未调整。','',
        '异常计数：'+str(totals),'',
        '| 步长 | C1 等待 | C7 等待 | D10 等待 | D10−C1 [95%种子区间] |',
        '|---|---:|---:|---:|---|']
    for dt in base['steps']:
        m={r['controller']:r['awt'] for r in means if r['dt']==dt}
        c=next(r for r in contrasts if r['dt']==dt and r['variant']=='all' and r['comparison']=='D10-C1')
        lines.append(f"| {dt} | {m['C1']:.4f} | {m['C7']:.4f} | {m['D10']:.4f} | {c['mean']:+.4f} [{c['low']:+.4f}, {c['high']:+.4f}] |")
    lines+=['','| 粗→细 | 指标 | 配对变化 [95%种子区间] | 每输入绝对变化均值 | 最大绝对变化 |','|---|---|---|---:|---:|']
    for r in shifts:
        lines.append(f"| {r['coarse']}→{r['fine']} | {r['quantity']} | {r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}] | {r['mean_absolute']:.4f} | {r['max_absolute']:.4f} |")
    lines+=['','区间先在种子内对固定日期和背景等权平均，再用三个种子构造 t 区间；精度有限，不能覆盖模型或现实误差。这些是已使用的开发条件。均值接近不等于逐输入收敛，亦不代表最细步长是真值。']
    (out/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Three-seed grid checks:',totals,flush=True)


def scenario_report(folder):
    p,e,rows=validate_archive(folder);assert len(rows)==360
    original=load(ROOT/'published_results/s1_s6_original_2026-11-01/raw.json')
    index={(r['point'],r['combo'],r['seed']):r for r in rows}
    old={(r['point'],r['combo'],r['seed']):r for r in original}
    assert len(index)==360 and set(index)==set(old)
    summaries=[]
    for s in p['scenarios']:
        for c in p['controllers']:
            part=[index[(s,c,seed)] for seed in p['seeds']]
            for r in part:
                assert r['input_sha256']==index[(s,'C1',r['seed'])]['input_sha256']
            summaries.append(dict(scenario=s,controller=c,awt=mean(r['awt'] for r in part),
                p90_run_mean=mean(r['p90'] for r in part),long_wait_percent=mean(r['lwt'] for r in part),
                repaired_minus_original=mean(r['awt']-old[(s,c,r['seed'])]['awt'] for r in part),
                paired_vs_C1=seed_interval([r['awt']-index[(s,'C1',r['seed'])]['awt'] for r in part])))
    keys=list(e['anomaly_totals'])
    assert e['anomaly_totals']=={k:sum(r[k] for r in rows) for k in keys}
    assert e['anomalous_runs']==sum(any(r[k] for k in keys) for r in rows)
    save(folder/'summary.json',dict(summaries=summaries,anomaly_totals=e['anomaly_totals']))
    lines=['# 修复版 S1–S6：原参数固定策略复验','',
        '360 次：6 场景 × C0/C1/C2/C3/C4/C7 × 原种子 0–9。保留原 LG3–16 占位参数及 0.25 秒步长；未接入课表、未重拟合策略地图。输入指纹与原 60 组场景/种子匹配。','',
        '全乘客服务及运动/事件异常：'+str(e['anomaly_totals']),'',
        '| 场景 | C0 | C1 | C2 | C3 | C4 | C7 | 等待均值最低 |','|---|---:|---:|---:|---:|---:|---:|---|']
    for s in p['scenarios']:
        m={r['controller']:r['awt'] for r in summaries if r['scenario']==s}
        lines.append('| '+s+' | '+' | '.join(f'{m[c]:.3f}' for c in p['controllers'])+' | '+min(m,key=m.get)+' |')
    lines+=['','单位：秒。均值最低不代表显著优于所有策略；两两比较未作多重比较校正。完整配对区间及旧版变化见 summary.json。此批不同于 LG2 起的课表实验，不应合并成一个均值。','',
        '原历史输出并未覆盖；运动修复允许改变输出，因此不再以全部旧值相同为通过条件。分区等策略在这些场景通过不证明所有负载/停运情况均正确。']
    (folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('S1-S6 checks:',e['anomaly_totals'],flush=True)


def evaluation_report(folder):
    p,e,rows=validate_archive(folder);assert len(rows)==450
    idx=paired_index(rows)
    expected={(d,v,s,dt,c) for d in p['dates'] for v in p['variants'] for s in p['seeds'] for dt in p['steps'] for c in p['controllers']}
    assert set(idx)==expected
    checks=load(folder/'pair_checks.json')
    assert len(checks)==75 and all(r['same_original_inputs_audits_decisions'] for r in checks)
    original=load(ROOT/'published_results/adaptive_v1/evaluate/raw.json')
    old={(r['date'],r['variant'],r['seed'],r['controller']):r for r in original}
    assert all(r['input_sha256']==old[(r['date'],r['variant'],r['seed'],r['controller'])]['input_sha256'] for r in rows)
    assert e['anomaly_totals']=={k:sum(r[k] for r in rows) for k in e['anomaly_totals']}
    assert e['anomalous_runs']==sum(any(r[k] for k in e['anomaly_totals']) for r in rows)
    contrasts=contrast_rows(rows);means=[];screens=[]
    for dt in p['steps']:
        for variant in ['all']+list(p['variants']):
            for c in p['controllers']:
                part=[r for r in rows if r['dt']==dt and r['controller']==c and (variant=='all' or r['variant']==variant)]
                means.append(dict(dt=dt,variant=variant,controller=c,**{k:mean(r[k] for r in part) for k in ['awt','p90','lwt','G_awt','other_awt']}))
        delta=next(r for r in contrasts if r['dt']==dt and r['variant']=='all' and r['comparison']=='D10-C1')
        variant_deltas=[r['mean'] for r in contrasts if r['dt']==dt and r['variant']!='all' and r['comparison']=='D10-C1']
        service=all(r[k]==0 for r in rows if r['dt']==dt for k in ['unserved','not_alighted','chain_availability_violations'])
        screen=dict(dt=dt,saving_at_least_025=-delta['mean']>=.25,seed_ci_upper_below_zero=delta['high']<0,
            no_variant_regression_over_05=max(variant_deltas)<=.5,zero_service_chain_anomalies=service)
        screen['passes_original_screen']=all(v for k,v in screen.items() if k!='dt');screens.append(screen)
    strata=load(folder/'strata.json');groups=defaultdict(dict)
    for r in strata:
        key=r['date'],r['variant'],r['seed'],r['dt'],r['dimension'],r['label']
        assert r['controller'] not in groups[key];groups[key][r['controller']]=r
    bucket=defaultdict(lambda:defaultdict(list))
    for key,g in groups.items():
        assert set(g)==set(p['controllers']) and len({r['n'] for r in g.values()})==1
        for variant in ['all',key[1]]:
            for c in ['C7','D10']:
                b=(key[3],variant,key[4],key[5],c+'-C1')
                bucket[b][key[2]].append(g[c]['awt']-g['C1']['awt'])
    strata_summary=[]
    for key,seeds in sorted(bucket.items()):
        strata_summary.append(dict(dt=key[0],variant=key[1],dimension=key[2],label=key[3],comparison=key[4],
            **seed_interval([mean(v) for _,v in sorted(seeds.items())])))
    # Within each run, every dimension partitions all input passengers exactly once.
    totals=defaultdict(lambda:[0,0.])
    for r in strata:
        k=r['date'],r['variant'],r['seed'],r['dt'],r['controller'],r['dimension']
        totals[k][0]+=r['n'];totals[k][1]+=r['n']*r['awt']
    for k,(n,weighted) in totals.items():
        assert n==idx[k[:5]]['n']
        assert abs(weighted/n-idx[k[:5]]['awt'])<1e-9
    for trace in load(folder/'private_trace_manifest.json'):
        assert sha(folder/'private_traces'/trace['file'])==trace['sha256']
    save(folder/'summary.json',dict(means=means,contrasts=contrasts,screens=screens,anomaly_totals=e['anomaly_totals']))
    save(folder/'strata_summary.json',strata_summary)
    lines=['# 修复版 D10：原评价条件复验','',
        '450 次：已使用的 11 月 2–6 日 × 三类背景 × 原种子 5000–5004 × C1/C7/D10 × 两个预先指定步长。D10 阈值、C1 主对照及原收益门槛不变。本批不是新的未接触评价集。','',
        '服务/链/迟到/事件/物理异常：'+str(e['anomaly_totals']),'',
        '| 步长 | 背景 | C1 | C7 | D10 | D10−C1 [95%种子区间] |','|---|---|---:|---:|---:|---|']
    for dt in p['steps']:
        for variant in ['all']+list(p['variants']):
            m={r['controller']:r['awt'] for r in means if r['dt']==dt and r['variant']==variant}
            c=next(r for r in contrasts if r['dt']==dt and r['variant']==variant and r['comparison']=='D10-C1')
            lines.append(f"| {dt} | {variant} | {m['C1']:.4f} | {m['C7']:.4f} | {m['D10']:.4f} | {c['mean']:+.4f} [{c['low']:+.4f}, {c['high']:+.4f}] |")
    lines+=['','## 原收益筛选','']
    for r in screens:lines.append(f"- dt={r['dt']}: 节省≥0.25秒={r['saving_at_least_025']}；区间上界<0={r['seed_ci_upper_below_zero']}；背景恶化≤0.5秒={r['no_variant_regression_over_05']}；无服务/链异常={r['zero_service_chain_anomalies']}；总体通过={r['passes_original_screen']}。")
    lines+=['','## 预先定义的分组诊断','',
        '| 步长 | 分组 | D10−C1 等权平均等待差（秒） |','|---|---|---:|']
    for r in strata_summary:
        if r['variant']=='all' and r['comparison']=='D10-C1' and r['dimension'] in ['origin','layer']:
            lines.append(f"| {r['dt']} | {r['dimension']}:{r['label']} | {r['mean']:+.4f} |")
    lines+=['','全部固定 15 分钟窗口以及背景分组见 strata_summary.json；每个分组先在种子内对有该分组的日期/背景等权平均。没有请求的分组不填入虚构零等待。分组/窗口区间仅作探索描述，未校正大量比较，不能挑选显著窗口作为主要结论。','',
        '逐乘客匿名索引与时间保存在本地 private_traces，450 份，不随公开汇总上传。输入/决策与原归档一致，分组人数及加权等待可还原每次全体指标。','',
        '五个种子的区间不包含真实人数、设备参数或课表重复的结构不确定性；旧 0.140 秒结果保留为原模型历史参照。不同策略的电梯状态路径不同，分时差异不能单独识别某次切换的因果损失。']
    (folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('Evaluation checks:',e['anomaly_totals'],'screens:',screens,flush=True)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['grids','scenarios','evaluation'])
    ap.add_argument('folders',nargs='+',type=Path);ap.add_argument('--output',type=Path);args=ap.parse_args()
    if args.phase=='grids':grid_report(args.folders,args.output)
    elif args.phase=='scenarios':scenario_report(args.folders[0])
    else:evaluation_report(args.folders[0])


if __name__=='__main__':main()
