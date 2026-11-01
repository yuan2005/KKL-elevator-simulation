"""Compare three time grids without assuming a convergence order."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean
import numpy as np
from scipy import stats
from study_paths import ROOT


def load(path):return json.loads(path.read_text(encoding='utf-8'))
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def interval(values):
    avg=mean(values)
    half=float(stats.t.ppf(.975,len(values)-1)*np.std(values,ddof=1)/np.sqrt(len(values)))
    return dict(mean=avg,ci95=[avg-half,avg+half],seed_values=values)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    folder=parser.parse_args().folder
    save=lambda name,value:(folder/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    protocol=load(folder/'protocol.json');new=load(folder/'raw.json')
    prior=ROOT/'published_results/timestep_sensitivity_v1'
    assert sha(prior/'raw.json')==protocol['prior_raw_hash']
    assert sha(prior/'protocol.json')==protocol['prior_protocol_hash']
    assert sha(folder/'protocol.json')==load(folder/'execution.json')['protocol_hash']
    old=load(prior/'raw.json');assert len(new)==135 and len(old)==270
    rows=old+new;steps=[.25,.125,.0625];controls=protocol['controllers'];variants=list(protocol['variants'])
    idx={};groups=defaultdict(dict)
    for r in rows:
        pair=r['date'],r['variant'],r['seed'];key=pair+(r['dt'],r['controller'])
        assert key not in idx
        idx[key]=r;groups[pair][(r['dt'],r['controller'])]=r
    assert len(groups)==45
    for g in groups.values():
        assert set(g)=={(dt,c) for dt in steps for c in controls}
        assert len({r['input_sha256'] for r in g.values()})==len({r['n'] for r in g.values()})==1
    checks=load(folder/'pair_checks.json');assert len(checks)==45
    assert all(all(r[k] for k in ['same_input_hash_all_grids','same_demand_audit_all_grids','same_D10_decisions_and_switches_all_grids']) for r in checks)
    metrics=['awt','p90','lwt','G_awt','other_awt','n','switches']
    error_keys=['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count']
    summaries=[];contrasts=[];refinements=[];sign_changes=[]
    for group_name in ['all']+variants:
        vs=variants if group_name=='all' else [group_name]
        pairs=[key for key in groups if key[1] in vs]
        for dt in steps:
            for c in controls:
                rr=[idx[key+(dt,c)] for key in pairs]
                summaries.append(dict(variant=group_name,dt=dt,controller=c,
                    **{k:mean(r[k] for r in rr) for k in metrics},**{k:sum(r[k] for r in rr) for k in error_keys}))
            for c in ['C7','D10']:
                vals=[mean(idx[(d,v,s,dt,c)]['awt']-idx[(d,v,s,dt,'C1')]['awt'] for d in protocol['dates'] for v in vs) for s in protocol['seeds']]
                contrasts.append(dict(variant=group_name,dt=dt,comparison=c+'-C1',**interval(vals)))
        for quantity in ['C1','C7','D10','C7-C1','D10-C1']:
            def value(pair,dt):
                c=quantity.split('-')[0]
                val=idx[pair+(dt,c)]['awt']
                return val-idx[pair+(dt,'C1')]['awt'] if '-' in quantity else val
            increments=[]
            for a,b in zip(steps[:-1],steps[1:]):
                diffs={p:value(p,b)-value(p,a) for p in pairs}
                seedvals=[mean(x for p,x in diffs.items() if p[2]==s) for s in protocol['seeds']]
                increments.append(dict(from_dt=a,to_dt=b,**interval(seedvals),
                    mean_absolute_input_change=mean(abs(x) for x in diffs.values()),
                    max_absolute_input_change=max(abs(x) for x in diffs.values())))
            refinements.append(dict(variant=group_name,quantity=quantity,increments=increments,
                aggregate_absolute_increment_shrinks=abs(increments[1]['mean'])<abs(increments[0]['mean']),
                paired_mean_absolute_increment_shrinks=increments[1]['mean_absolute_input_change']<increments[0]['mean_absolute_input_change']))
    for c in ['C7','D10']:
        for a,b in zip(steps[:-1],steps[1:]):
            changed=[]
            for p,g in groups.items():
                x=g[(a,c)]['awt']-g[(a,'C1')]['awt'];y=g[(b,c)]['awt']-g[(b,'C1')]['awt']
                if x*y<0:changed.append(dict(date=p[0],variant=p[1],seed=p[2],from_delta=x,to_delta=y))
            sign_changes.append(dict(comparison=c+'-C1',from_dt=a,to_dt=b,count=len(changed),inputs=changed))
    anomalies=[{k:r[k] for k in ['date','variant','seed','dt','controller']+error_keys} for r in rows if any(r[k] for k in error_keys)]
    verification=dict(new_runs=135,combined_rows=405,paired_inputs=45,all_inputs_equal=True,all_D10_logs_equal=True,service_anomalies=anomalies)
    save('verification.json',verification)
    save('summary.json',dict(summary=summaries,contrasts=contrasts,refinements=refinements,sign_changes=sign_changes,
        service_anomaly_run_count=len(anomalies),
        warning='Service anomalies detected: finest-grid metrics are diagnostic failure outputs, NOT valid policy efficacy evidence. Three grids do not prove convergence or accuracy.' if anomalies else 'Three-grid development-date diagnostic; no convergence or accuracy proof.'))
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,4.3));pos=np.arange(3)
    for c in controls:
        rr=[next(r for r in summaries if r['variant']=='all' and r['controller']==c and r['dt']==dt) for dt in steps]
        axes[0].plot(pos,[r['awt'] for r in rr],'o-',label=c)
    axes[0].set(xticks=pos,xticklabels=[str(dt) for dt in steps],xlabel='Timestep (s), coarser to finer',ylabel='Mean waiting (s)',title='Same 45 development inputs')
    axes[0].legend()
    for group_name in ['all']+variants:
        rr=[next(r for r in contrasts if r['variant']==group_name and r['dt']==dt and r['comparison']=='D10-C1') for dt in steps]
        axes[1].plot(pos,[r['mean'] for r in rr],'o-',label=group_name)
    axes[1].axhline(0,color='black',linewidth=.8)
    axes[1].set(xticks=pos,xticklabels=[str(dt) for dt in steps],xlabel='Timestep (s), coarser to finer',ylabel='D10 minus C1 waiting (s)',title='Paired mean contrasts (intervals in report)')
    axes[1].legend()
    if anomalies:
        for ax in axes:ax.axvspan(1.85,2.15,color='red',alpha=.08)
    fig.suptitle('Numerical failure at finest grid: diagnostic outputs, not policy evidence' if anomalies else 'Three-grid check: fixed demand, fixed policy, identical switching logs')
    fig.tight_layout();fig.savefig(folder/'three_grid_check.png',dpi=160);plt.close(fig)
    lines=['# 第三档步长检查','',
        f'**数值核验未通过：有 {len(anomalies)} 次运行存在服务或行程时序异常。最细步长下的均值、区间和策略差仅作故障诊断，不用于宣称策略收益；所有失败运行均保留。**' if anomalies else '未发现服务异常；这本身不证明数值收敛。','',
        '新增 0.0625 秒步长的 135 次仿真，与已保存的 0.25/0.125 秒结果组合，共 405 行用于本轮三档比较；不把旧结果算作新增运行。','',
        '## 三档全体均值','', '| 步长 s | C1 等待 s | C7 等待 s | D10 等待 s | D10−C1 s | 该差的 Monte Carlo 95% 区间 |','|---|---:|---:|---:|---:|---|']
    for dt in steps:
        vals=[next(r['awt'] for r in summaries if r['variant']=='all' and r['dt']==dt and r['controller']==c) for c in controls]
        contrast=next(r for r in contrasts if r['variant']=='all' and r['dt']==dt and r['comparison']=='D10-C1')
        lines.append(f'| {dt} | '+' | '.join(f'{x:.4f}' for x in vals)+f" | {contrast['mean']:+.4f} | [{contrast['ci95'][0]:+.4f}, {contrast['ci95'][1]:+.4f}] |")
    lines+=['','五个开发日期 10/26–30、低/混合/高背景与种子 4000–4002 等权；不是此前留出评价的 0.140 秒。D10 在这些开发条件上选出，三个种子的区间只作条件于该模型与规则的 Monte Carlo 描述。','',
        '## 分背景 D10−C1','', '| 背景 | 步长 s | 差值 s | 95% 区间 |','|---|---:|---:|---|']
    for r in contrasts:
        if r['variant']!='all' and r['comparison']=='D10-C1':lines.append(f"| {r['variant']} | {r['dt']} | {r['mean']:+.4f} | [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}] |")
    lines+=['','## 两次细化的增量','',
        '下面分别看总体均值增量与逐输入差的绝对值均值，后者避免总体均值因正负抵消而掩盖敏感性。增量不是相对真实解的误差。','',
        '| 背景 | 指标 | 细化 | 均值变化 s | 配对95%区间 | 逐输入绝对变化均值 s |','|---|---|---|---:|---|---:|']
    for r in refinements:
        for change in r['increments']:
            lines.append(f"| {r['variant']} | {r['quantity']} | {change['from_dt']}→{change['to_dt']} | {change['mean']:+.4f} | [{change['ci95'][0]:+.4f}, {change['ci95'][1]:+.4f}] | {change['mean_absolute_input_change']:.4f} |")
    lines+=['','## 配对核验','',
        '- 45 组输入跨三步长三控制器的人数与乘客哈希一致；需求审计、D10 决策与切换日志也一致。',
        f'- 三档合计存在服务异常的运行数：{len(anomalies)}，细节见 verification.json。零服务异常不等于零长等待。']
    for r in sign_changes:lines.append(f"- {r['comparison']} 在 {r['from_dt']}→{r['to_dt']} 时有 {r['count']}/45 组差值改变符号；接近零的差值翻转不等于总体结论反转。")
    lines+=['','## 解释限制','',
        '保持原先阈值和收益门槛，未利用第三档结果重新选择 D10。控制器决策日志相同仅排除切换时刻变化，不排除运动/停站/事件顺序造成不同排队轨迹。',
        '不假设误差按某个固定阶数收敛，不用 Richardson 外推。三档结果也不提供严格误差界，不把最细步长自动当成现实真值。',
        '如果整体或逐输入增量没有缩小，停止继续为动态规则调阈值，转向检查数值事件处理与有明确手算预期的微型案例。']
    (folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(new_runs=len(new),combined_rows=len(rows),anomalous_runs=len(anomalies),
        overall=[{k:r[k] for k in ['dt','controller','awt','unserved','not_alighted']} for r in summaries if r['variant']=='all']),indent=2))


if __name__=='__main__':main()
