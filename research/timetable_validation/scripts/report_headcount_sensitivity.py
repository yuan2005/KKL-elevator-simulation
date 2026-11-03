"""Audit and summarise the predeclared missing-headcount experiment."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean
import numpy as np
from scipy import stats


def main():
    parser=argparse.ArgumentParser();parser.add_argument('folder',type=Path)
    folder=parser.parse_args().folder
    load=lambda name:json.loads((folder/name).read_text(encoding='utf-8'))
    save=lambda name,data:(folder/name).write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    protocol=load('protocol.json');rows=load('raw.json');audits=load('input_audits_private.json')
    assert len(rows)==protocol['runs']==135 and len(audits)==45
    assert hashlib.sha256((folder/'protocol.json').read_bytes()).hexdigest()==load('execution.json')['protocol_hash']
    groups=defaultdict(dict)
    for r in rows:
        key=(r['date'],r['capacity_fraction'],r['seed'])
        assert r['controller'] not in groups[key]
        groups[key][r['controller']]=r
    for group in groups.values():
        assert set(group)==set(protocol['controllers'])
        assert len({r['input_sha256'] for r in group.values()})==1
        assert len({r['n'] for r in group.values()})==1
    idx={(r['date'],r['capacity_fraction'],r['seed'],r['controller']):r for r in rows}
    coverage=load('coverage_private.json')
    audit_idx={(a['date'],a['capacity_fraction'],a['seed']):a['input_audit'] for a in audits}
    for day in protocol['dates']:
        known=[s['session_id'] for s in coverage[day]['selected'] if s['headcount_snapshot'] is not None]
        for seed in protocol['seeds']:
            rr=[audit_idx[(day,f,seed)] for f in protocol['fractions']]
            for sid in known:
                assert len({a['session_attendance'][sid] for a in rr})==1
            for key in ['background_intent','background_lift','through_intent','through_lift']:
                assert len({a['ledger'][key] for a in rr})==1
    for a in audit_idx.values():
        ledger=a['ledger']
        assert ledger['class_intent']==2*a['attendance_slots']-a['transfer_links']
        for layer in ['class','background','through']:
            assert ledger[layer+'_intent']==sum(ledger.get(layer+'_'+m,0) for m in ['lift','stairs','same_floor'])
    metrics=['awt','p90','lwt','G_awt','other_awt','n','switches']
    checks=['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count']
    summary=[];comparisons=[];daily=[]
    for fraction in protocol['fractions']:
        for control in protocol['controllers']:
            rr=[r for r in rows if r['capacity_fraction']==fraction and r['controller']==control]
            summary.append(dict(capacity_fraction=fraction,controller=control,
                **{k:mean(r[k] for r in rr) for k in metrics},**{k:sum(r[k] for r in rr) for k in checks}))
        for control in ['C7','D10']:
            deltas=[mean(idx[(day,fraction,seed,control)]['awt']-idx[(day,fraction,seed,'C1')]['awt'] for day in protocol['dates']) for seed in protocol['seeds']]
            half=float(stats.t.ppf(.975,len(deltas)-1)*np.std(deltas,ddof=1)/np.sqrt(len(deltas)))
            delta=mean(deltas)
            comparisons.append(dict(capacity_fraction=fraction,comparison=control+'-C1',delta=delta,ci95=[delta-half,delta+half],seed_mean_differences=deltas))
        for day in protocol['dates']:
            means={c:mean(idx[(day,fraction,s,c)]['awt'] for s in protocol['seeds']) for c in protocol['controllers']}
            daily.append(dict(date=day,capacity_fraction=fraction,means=means,ranking=sorted(means,key=means.get)))
    failures=[{k:r[k] for k in ['date','capacity_fraction','seed','controller']+checks} for r in rows if any(r[k] for k in checks)]
    result=dict(summary=summary,comparisons=comparisons,daily=daily,service_anomalies=failures,
        checks=dict(paired_inputs=len(groups),known_attendance_unchanged=True,background_and_through_counts_unchanged=True,flow_conservation=True),
        scope='Mixed background only; fixed five explored days; three Monte Carlo seeds; not new held-out validation')
    save('summary.json',result)
    # Retain plots as standalone scientific artifacts.
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    fig,axes=plt.subplots(1,2,figsize=(11,4.2))
    for control in protocol['controllers']:
        rr=[r for r in summary if r['controller']==control]
        axes[0].plot([r['capacity_fraction']*100 for r in rr],[r['awt'] for r in rr],marker='o',label=control)
    axes[0].set(xlabel='Assumed enrolment / room capacity (%)',ylabel='Mean waiting (s)',title='Fixed mixed-background demand')
    axes[0].legend()
    for control,offset in [('C7',-1),('D10',1)]:
        rr=[r for r in comparisons if r['comparison']==control+'-C1']
        axes[1].errorbar([r['capacity_fraction']*100+offset for r in rr],[r['delta'] for r in rr],
            yerr=[r['ci95'][1]-r['delta'] for r in rr],fmt='o-',capsize=4,label=control+' - C1')
    axes[1].axhline(0,color='black',linewidth=.8)
    axes[1].set(xlabel='Assumed enrolment / room capacity (%)',ylabel='Paired waiting difference (s)',title='95% Monte Carlo intervals (3 seeds)')
    axes[1].legend()
    fig.suptitle('Missing-headcount sensitivity: partial timetable, assumed traffic')
    fig.tight_layout();fig.savefig(folder/'headcount_sensitivity.png',dpi=160);plt.close(fig)
    lines=['# 缺失课堂人数敏感性实验','',
        '三种容量比例 × 五个已探索日期 × 三个新种子 × C1/C7/D10 = 135 次仿真。协议先保存，之后运行；没有修改动态阈值或原评价门槛。','',
        '## 范围','',
        '- 日期 2026-10-26 至 30；种子 6000–6002。仅调整已纳入且缺选课人数课堂的容量比例 40%/60%/80%，已知人数和纳入课堂集合不变。',
        '- 固定混合背景：高层端点选择概率 50%，背景 12 人次/5分钟、过路 6 人次/5分钟；出席率 80%、转课上限 20%、设备参数和 D10 h=0.1 不变。',
        '- 容量比例是人数估计，之后另乘随机出席；并非直接乘梯比例。不同人数条件可产生不同乘客，各条件内三策略严格共享乘客。',
        '- 不补回未知楼层、合班等原因排除的课堂。该实验不覆盖低层/高层背景，也不重新评价上一轮三背景等权的 0.140 秒结论。','',
        '## 结果','',
        '| 容量比例 | 策略 | 平均等待 s | P90 均值 s | >60s % | G 等待 s | 非G 等待 s | 平均乘梯人次 | 平均切换次数 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|']
    for r in summary:
        lines.append(f"| {r['capacity_fraction']:.0%} | {r['controller']} | {r['awt']:.3f} | {r['p90']:.3f} | {r['lwt']:.3f} | {r['G_awt']:.3f} | {r['other_awt']:.3f} | {r['n']:.1f} | {r['switches']:.2f} |")
    lines+=['','每个日期/种子等权；P90 是逐次运行的 P90 均值，不是合并乘客后的分位数。人次不是独立学生人数。','',
        '| 容量比例 | 比较 | 平均等待差 s | Monte Carlo 95% 区间 |','|---|---|---:|---|']
    for r in comparisons:
        lines.append(f"| {r['capacity_fraction']:.0%} | {r['comparison']} | {r['delta']:+.4f} | [{r['ci95'][0]:+.4f}, {r['ci95'][1]:+.4f}] |")
    lines+=['','差值小于零表示前者等待更短。每个种子先对五个固定日期平均，再对三个种子的配对差构造 t 区间（自由度2）；不把15次重复视为15个独立现实样本。区间不覆盖输入、设备及真实需求不确定性，重复次数较少。','',
        '## 输入与服务检查','',
        '- 45 组输入在三策略之间人数与哈希一致；已知人数课堂的出席抽样、背景和过路人次在三种容量比例间一致；课堂端点和分流守恒通过。',
        f'- 存在任一未服务/未下梯/时序冲突/转课乘梯晚到的运行数：{len(failures)}。详细列表保存在 summary.json 的 service_anomalies。','',
        '## 每日策略排序（均值从小到大）','', '| 日期 | 容量比例 | 排序 |','|---|---|---|']
    for r in daily:
        lines.append(f"| {r['date']} | {r['capacity_fraction']:.0%} | {' < '.join(r['ranking'])} |")
    lines+=['','排序只按样本均值，不证明相邻策略显著不同。','',
        '本轮为已探索日期的单因素诊断。所有参数范围均为研究设定，不是测得的置信范围；不能把结果写成已校准现实 KKL 或已确定最优阈值。']
    (folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print(json.dumps(dict(comparisons=comparisons,service_anomalies=failures),indent=2))


if __name__=='__main__':
    main()
