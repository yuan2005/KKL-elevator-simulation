"""Paired numerical-grid diagnostics; no retrospective success threshold."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean
import numpy as np
from scipy import stats
from study_paths import ROOT


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    folder=parser.parse_args().folder
    load=lambda p:json.loads(p.read_text(encoding='utf-8'))
    save=lambda name,obj:(folder/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    protocol=load(folder/'protocol.json');rows=load(folder/'raw.json');checks=load(folder/'pair_checks.json')
    assert len(rows)==270 and len(checks)==45
    assert hashlib.sha256((folder/'protocol.json').read_bytes()).hexdigest()==load(folder/'execution.json')['protocol_hash']
    prior_path=ROOT/'published_results/adaptive_v1/develop/raw.json'
    assert hashlib.sha256(prior_path.read_bytes()).hexdigest()==protocol['prior_development_raw_hash']
    prior={(r['date'],r['variant'],r['seed'],r['controller']):r for r in load(prior_path)}
    groups=defaultdict(dict);idx={};comparisons=0;maxdiff=0.
    for row in rows:
        pair=(row['date'],row['variant'],row['seed'])
        key=pair+(row['dt'],row['controller'])
        assert key not in idx
        idx[key]=row;groups[pair][(row['dt'],row['controller'])]=row
        if row['dt']==.25:
            previous=prior[pair+(row['controller'],)]
            assert row['input_sha256']==previous['input_sha256']
            for k,value in previous.items():
                if isinstance(value,(int,float)):
                    diff=abs(row[k]-value);comparisons+=1;maxdiff=max(diff,maxdiff)
                    assert diff<1e-12,(key,k,row[k],value)
    assert len(groups)==45
    for group in groups.values():
        assert len(group)==6 and len({r['input_sha256'] for r in group.values()})==1
        assert len({r['n'] for r in group.values()})==1
    assert all(r['identical_input_audits'] and r['identical_decision_and_switch_logs'] for r in checks)
    summary=[];contrasts=[];shifts=[];flips=[]
    metrics=['awt','p90','lwt','G_awt','other_awt','n','switches']
    errors=['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count']
    def interval(values):
        avg=mean(values);half=float(stats.t.ppf(.975,len(values)-1)*np.std(values,ddof=1)/np.sqrt(len(values)))
        return dict(mean=avg,ci95=[avg-half,avg+half],seed_differences=values)
    for variant in ['all']+list(protocol['variants']):
        variants=list(protocol['variants']) if variant=='all' else [variant]
        relevant=[r for r in rows if r['variant'] in variants]
        for dt in protocol['steps']:
            for c in protocol['controllers']:
                rr=[r for r in relevant if r['dt']==dt and r['controller']==c]
                summary.append(dict(variant=variant,dt=dt,controller=c,
                    **{k:mean(r[k] for r in rr) for k in metrics},**{k:sum(r[k] for r in rr) for k in errors}))
            for c in ['C7','D10']:
                deltas=[mean(idx[(day,v,s,dt,c)]['awt']-idx[(day,v,s,dt,'C1')]['awt'] for day in protocol['dates'] for v in variants) for s in protocol['seeds']]
                contrasts.append(dict(variant=variant,dt=dt,comparison=c+'-C1',**interval(deltas)))
        for c in protocol['controllers']:
            values=[mean(idx[(day,v,s,.125,c)]['awt']-idx[(day,v,s,.25,c)]['awt'] for day in protocol['dates'] for v in variants) for s in protocol['seeds']]
            shifts.append(dict(variant=variant,quantity=c+'_awt',**interval(values)))
        for c in ['C7','D10']:
            values=[mean((idx[(day,v,s,.125,c)]['awt']-idx[(day,v,s,.125,'C1')]['awt'])-
                         (idx[(day,v,s,.25,c)]['awt']-idx[(day,v,s,.25,'C1')]['awt']) for day in protocol['dates'] for v in variants) for s in protocol['seeds']]
            shifts.append(dict(variant=variant,quantity=c+'-C1_contrast',**interval(values)))
    for c in ['C7','D10']:
        rr=[]
        for pair,g in groups.items():
            a=g[(.25,c)]['awt']-g[(.25,'C1')]['awt'];b=g[(.125,c)]['awt']-g[(.125,'C1')]['awt']
            if a*b<0:rr.append(dict(date=pair[0],variant=pair[1],seed=pair[2],coarse_delta=a,fine_delta=b))
        flips.append(dict(comparison=c+'-C1',count=len(rr),inputs=rr))
    anomalies=[{k:r[k] for k in ['date','variant','seed','dt','controller']+errors} for r in rows if any(r[k] for k in errors)]
    verification=dict(runs=270,paired_inputs=45,coarse_metric_comparisons=comparisons,coarse_max_abs_difference=maxdiff,
        same_inputs_across_steps_and_controls=True,same_D10_decision_and_switch_logs=True,service_anomalies=anomalies)
    save('verification.json',verification)
    save('summary.json',dict(summary=summary,contrasts=contrasts,fine_minus_coarse=shifts,sign_flips=flips,
        scope='Two-grid sensitivity on explored dates; not proof of convergence, actual KKL accuracy, or a new held-out D10 evaluation'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,4.3))
    variants=list(protocol['variants']);positions=np.arange(len(variants))
    for c,color in [('C1','C0'),('C7','C1'),('D10','C2')]:
        for dt,style in [(.25,'--'),(.125,'-')]:
            rr=[next(r for r in summary if r['variant']==v and r['dt']==dt and r['controller']==c) for v in variants]
            axes[0].plot(positions,[r['awt'] for r in rr],marker='o',linestyle=style,color=color,label=f'{c}, dt={dt}')
    axes[0].set(xticks=positions,xticklabels=variants,ylabel='Mean waiting (s)',title='Same passengers; two time grids')
    axes[0].legend(fontsize=8)
    for dt,offset in [(.25,-.07),(.125,.07)]:
        rr=[next(r for r in contrasts if r['variant']==v and r['dt']==dt and r['comparison']=='D10-C1') for v in variants]
        axes[1].errorbar(positions+offset,[r['mean'] for r in rr],yerr=[r['ci95'][1]-r['mean'] for r in rr],fmt='o',capsize=4,label=f'dt={dt}')
    axes[1].axhline(0,color='black',linewidth=.8)
    axes[1].set(xticks=positions,xticklabels=variants,ylabel='D10 minus C1 waiting (s)',title='95% Monte Carlo intervals (3 seeds)')
    axes[1].legend()
    fig.suptitle('Timestep sensitivity: fixed demand and unchanged switching decisions')
    fig.tight_layout();fig.savefig(folder/'timestep_sensitivity.png',dpi=160);plt.close(fig)
    lines=['# 仿真步长敏感性检查','',
        '5 个已探索日期 × 3 类背景 × 3 个种子 × 2 个步长 × 3 个控制器 = 270 次仿真。协议预先保存；既有阈值、设备和需求保持不变。','',
        '## 平均等待（秒）','', '| 背景 | 步长 s | C1 | C7 | D10 |','|---|---:|---:|---:|---:|']
    for v in ['all']+variants:
        for dt in protocol['steps']:
            values=[next(r['awt'] for r in summary if r['variant']==v and r['dt']==dt and r['controller']==c) for c in protocol['controllers']]
            lines.append(f"| {v} | {dt} | "+' | '.join(f'{x:.4f}' for x in values)+' |')
    lines+=['','按固定日期、背景、种子等权；all 的背景权重是实验设计，不是实际发生频率。','',
        '## D10−C1：平均等待差','', '| 背景 | 步长 s | 差值 s | Monte Carlo 95% 区间 s |','|---|---:|---:|---|']
    for r in contrasts:
        if r['comparison']=='D10-C1':lines.append(f"| {r['variant']} | {r['dt']} | {r['mean']:+.4f} | [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}] |")
    lines+=['','负差表示 D10 等待更短。每个种子先对固定日期/背景平均，再以三个配对重复计算 t 区间，自由度2；不覆盖模型参数或实际需求误差。','',
        '## 缩小步长造成的变化','', '| 背景 | 指标 | 0.125−0.25 s | 配对 Monte Carlo 95% 区间 |','|---|---|---:|---|']
    for r in shifts:
        lines.append(f"| {r['variant']} | {r['quantity']} | {r['mean']:+.4f} | [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}] |")
    lines+=['','contrast 行为两种步长下策略差值之差，不能用两个独立区间是否重叠代替这项配对比较。','',
        '## 核验','',f'- 45 组输入各用于两步长三策略，人数、输入哈希与需求审计一致；D10 决策及切换日志在两步长之间完全一致。',
        f'- 0.25 秒的 135 次运行与历史开发期结果对照，共 {comparisons} 项数值，最大绝对差 {maxdiff}。',
        f'- 有服务异常的运行数：{len(anomalies)}；具体未上梯/未下梯/时序冲突/晚到保留在 verification.json。']
    for r in flips:lines.append(f"- {r['comparison']} 的单输入均值差符号在 {r['count']}/45 组中变化；详情在 summary.json。接近零的差值翻转不等于总体结论翻转。")
    lines+=['','## 边界','',
        '本轮复用开发期 10/26–30 和种子 4000–4002。0.25 秒的基准收益是开发期结果，不是此前 11/02–06 留出评价的 0.140 秒。',
        '只有两个步长，不能宣称已数值收敛，更不能把细步长结果当作精确真值。即使规则切换时刻不变，车位、减速、登记呼梯及排队轨迹仍可能改变。',
        '若绝对等待或策略差对步长敏感，应先追加更细步长/检查运动与事件处理，再讨论小幅收益或调整阈值。不能为了策略胜出选择步长。']
    (folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(overall=[r for r in summary if r['variant']=='all'],D10_contrasts=[r for r in contrasts if r['comparison']=='D10-C1'],anomalies=anomalies,verification=verification),indent=2))


if __name__=='__main__':main()
