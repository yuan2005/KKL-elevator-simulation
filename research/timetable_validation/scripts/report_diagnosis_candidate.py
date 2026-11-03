"""Report descriptive window attribution and a frozen single-factor development test."""
import argparse
from collections import defaultdict
from pathlib import Path
from statistics import mean
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from repair_study_utils import load,save,sha,seed_interval


def diagnosis(folder):
    data=load(folder/'summary.json');p=load(folder/'protocol.json');e=load(folder/'execution.json')
    assert sha(folder/'protocol.json')==e['protocol_hash'] and sha(folder/'summary.json')==e['summary_hash']
    groups=data['groups'];decisions=data['decisions']
    lines=['# 修复版分时段诊断：收益、等待增加与切换关联','',
        '复用已完成的 450 次原评价条件运行，核验 450 份本地轨迹；本次新增仿真为 0。原先固定的 15 分钟窗口保留；状态/切换年龄分组属于事后诊断。未删除不利窗口，也未重新挑选阈值。','',
        '贡献定义：每次输入中某组的等待差总和 ÷ 该输入的全体乘客数，再对日期/背景/种子等权平均。因此每个完整分区的贡献可以相加，还原全日 D10−C1。它不同于该组自己的平均等待差。','',
        '## 人流层对全体平均等待差的贡献（秒）','',
        '| 步长 | 人流层 | 组内平均等待差 | 对全体均值的贡献 |','|---|---|---:|---:|']
    for r in groups:
        if r['variant']=='all' and r['dimension']=='layer':
            lines.append(f"| {r['dt']} | {r['label']} | {r['conditional_delta_mean']:+.4f} | {r['contribution']['mean']:+.4f} |")
    lines+=['','负值代表 D10 比 C1 等待短。独立背景人流层增加的等待，抵消了部分课堂/过路人流的节省。背景人流层不同于 low/mixed/high 三种背景楼层场景。','',
        '## 请求到达时的 D10 状态关联','',
        '| 步长 | 人流层及到达状态 | 对全体均值的贡献（秒） |','|---|---|---:|']
    for r in groups:
        if r['variant']=='all' and r['dimension']=='layer_state':
            lines.append(f"| {r['dt']} | {r['label']} | {r['contribution']['mean']:+.4f} |")
    lines+=['','这是按请求到达时 D10 已选择的状态分组。等待期间可能再次切换，且电梯位置/负载有历史效应；状态由需求决定。因此不能解释成 C7 的独立因果成本或某次切换的损失。','',
        '## 完整时段覆盖','',
        '以下四段只是把全部原 15 分钟窗口按时钟合并展示，属于事后描述，并非新主要指标；没有按结果挑选窗口。','',
        '| 步长 | 背景场景 | 12点前 | 12–14点 | 14–18点 | 18点后 |','|---|---|---:|---:|---:|---:|']
    periods=[('before12',0,43200),('12-14',43200,50400),('14-18',50400,64800),('after18',64800,86400)]
    time_rows=[]
    for dt in [.25,.125]:
        for variant in ['all','low','mixed','high']:
            rs=[r for r in groups if r['dt']==dt and r['variant']==variant]
            vals=[sum(r['contribution']['mean'] for r in rs if r['dimension']=='window' and lo<=int(r['label'])<hi) for _,lo,hi in periods]
            total=next(r['contribution']['mean'] for r in rs if r['dimension']=='all')
            assert abs(sum(vals)-total)<1e-10
            lines.append('| '+str(dt)+' | '+variant+' | '+' | '.join(f'{v:+.4f}' for v in vals)+' |')
            time_rows.append(dict(dt=dt,variant=variant,**{name:v for (name,_,_),v in zip(periods,vals)}))
    lines+=['','单位是对全日平均等待差的贡献（秒），不是时段自身的平均等待。收益分布在多个时段，低层背景场景是主要来源；不能将上午的贡献全部解释为课表效应。','',
        '## 切换与触发条件','',
        '| 背景 | 平均切换次数/天 | C7 时间占比 | 条件满足但仍为 C1 的决策比例 |','|---|---:|---:|---:|']
    for r in decisions:
        if r['dt']==.125 and r['variant']!='all':
            lines.append(f"| {r['variant']} | {r['switches']:.2f} | {100*r['C7_time_fraction']:.2f}% | {100*r['entry_pass_but_C1_fraction']:.2f}% |")
    lines+=['','两个步长的决策日志相同。C7 时间占比与请求占比、决策次数占比不同，不要互换。低层场景中高楼层端点比例从未超过 10%，因此仅改变高层阈值不能解释其进入 C7 的延迟。','',
        '## 下一步的单因素检验','',
        '不能从这些关联断定“少切换一定更好”：切换后前 300 秒的全部行程整体仍有净收益。为检验更保守切换的影响，只增加连续确认次数 2→3（进入和退出均改），保留 300 秒观察窗、60 秒判断、600 秒最短停留、h=0.10 及全部人流设定。','',
        '只在原开发日期和种子上比较；这项选择已受到已使用评价数据的启发，后续不能将 11 月 2–6 日再次称为未接触评价集。是否推进由预先冻结的整体等待比较决定，切换数下降本身不算成功。','',
        '区间仅按五个种子构造，分组/窗口未经多重比较校正；不覆盖模型参数、课表重复或真实人流误差。原诊断 v1 的策略日志是完整名称，裸 C7 字符串比较导致时间占比错误为零；已保留记录，v2 正规化标签并逐决策验证，未重跑或改动任何仿真。']
    (folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    save(folder/'time_block_summary.json',time_rows)
    fig,axes=plt.subplots(3,1,figsize=(12,8),sharex=True)
    for ax,variant in zip(axes,['low','mixed','high']):
        for dt in [.25,.125]:
            rs=sorted([r for r in groups if r['dt']==dt and r['variant']==variant and r['dimension']=='window'],key=lambda r:int(r['label']))
            ax.plot([int(r['label'])/3600 for r in rs],[r['contribution']['mean'] for r in rs],label=f'dt={dt} s',linewidth=1.4)
        ax.axhline(0,color='gray',lw=.7);ax.set_ylabel(variant+'\ncontribution (s)');ax.grid(alpha=.2)
    axes[0].legend();axes[-1].set_xlabel('Clock hour of request arrival (all fixed 15-minute windows)')
    fig.suptitle('D10 - C1 contribution to whole-day mean waiting by arrival window')
    fig.text(.5,.015,'450 existing runs | model dd06745 | 5 dates x 5 seeds per scenario | descriptive, not causal',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.04,1,.95]);fig.savefig(folder/'window_contributions.png',dpi=170);plt.close(fig)
    print('Saved complete diagnosis and full-window figure.')


def candidate(folder):
    p=load(folder/'protocol.json');e=load(folder/'execution.json');new=load(folder/'raw.json');old=load(folder/'reused_baselines.json')
    assert len(new)==90 and len(old)==270 and sha(folder/'protocol.json')==e['protocol_hash']
    idx={(r['date'],r['variant'],r['seed'],r['dt'],r['controller']):r for r in new+old}
    assert len(idx)==360
    expected={(d,v,s,dt,c) for d in p['dates'] for v in p['variants'] for s in p['seeds'] for dt in p['steps'] for c in ['C1','C7','D10','D10P3']}
    assert set(idx)==expected
    checks=load(folder/'pair_checks.json')
    assert len(checks)==45 and all(r['same_input_audit_and_past_features'] for r in checks)
    for r in new:
        for c in ['C1','C7','D10']:
            b=idx[(r['date'],r['variant'],r['seed'],r['dt'],c)]
            assert r['n']==b['n'] and r['input_sha256']==b['input_sha256']
    assert e['anomaly_totals']=={k:sum(r[k] for r in new) for k in e['anomaly_totals']}
    comparisons=[];means=[];screens=[]
    for dt in p['steps']:
        for variant in ['all']+list(p['variants']):
            part=[r for r in new if r['dt']==dt and (variant=='all' or r['variant']==variant)]
            for c in ['C1','C7','D10','D10P3']:
                rows=[idx[(r['date'],r['variant'],r['seed'],dt,c)] for r in part]
                means.append(dict(dt=dt,variant=variant,controller=c,**{k:mean(r[k] for r in rows) for k in ['awt','switches','p90','lwt','G_awt','other_awt']}))
            for baseline in ['D10','C1','C7']:
                for metric in ['awt','switches','p90','lwt','G_awt','other_awt']:
                    seed_values=defaultdict(list)
                    for r in part:
                        b=idx[(r['date'],r['variant'],r['seed'],dt,baseline)]
                        seed_values[r['seed']].append(r[metric]-b[metric])
                    comparisons.append(dict(dt=dt,variant=variant,comparison='D10P3-'+baseline,metric=metric,
                        **seed_interval([mean(v) for _,v in sorted(seed_values.items())])))
        def get(v,b):return next(r for r in comparisons if r['dt']==dt and r['variant']==v and r['comparison']=='D10P3-'+b and r['metric']=='awt')
        delta=get('all','D10');vs_c1=get('all','C1')
        safe=all(r[k]==0 for r in new if r['dt']==dt for k in e['anomaly_totals'])
        screens.append(dict(dt=dt,improves_D10_mean=delta['mean']<0,D10_ci_upper_below_zero=delta['high']<0,
            no_variant_regression_over05=max(get(v,'D10')['mean'] for v in p['variants'])<=.5,
            zero_anomalies=safe,
            passes_development_screen=delta['mean']<0 and delta['high']<0 and max(get(v,'D10')['mean'] for v in p['variants'])<=.5 and safe,
            passes_original_C1_screen=vs_c1['mean']<=-.25 and vs_c1['high']<0 and max(get(v,'C1')['mean'] for v in p['variants'])<=.5 and safe))
    advance=all(r['passes_development_screen'] for r in screens)
    summary=dict(means=means,comparisons=comparisons,screens=screens,advance_candidate=advance,
        anomaly_totals=e['anomaly_totals'],new_runs=90,reused_baseline_runs=270)
    save(folder/'summary.json',summary)
    lines=['# 单因素开发实验：连续确认次数 2→3','',
        '新增 90 次 D10P3 运行：5 原开发日期 × 3 背景 × 3 原种子 × 2 预定步长。270 条同版本 C1/C7/D10 归档结果作为固定配对对照，未重复计为新增仿真。输入与过去观测特征一致，只有进入/退出策略所需的连续确认次数改变。','',
        '方案在运行前冻结，未重新搜索任何阈值；这只是受已使用评价结果启发的开发实验，不是独立验证。','',
        '异常计数：'+str(e['anomaly_totals']),'',
        '| 步长 | 背景 | D10 等待 | D10P3 等待 | P3−D10 [95%种子区间] | D10→P3 切换次数 |','|---|---|---:|---:|---|---|']
    for dt in p['steps']:
        for variant in ['all']+list(p['variants']):
            m={r['controller']:r for r in means if r['dt']==dt and r['variant']==variant}
            d=next(r for r in comparisons if r['dt']==dt and r['variant']==variant and r['comparison']=='D10P3-D10' and r['metric']=='awt')
            lines.append(f"| {dt} | {variant} | {m['D10']['awt']:.4f} | {m['D10P3']['awt']:.4f} | {d['mean']:+.4f} [{d['low']:+.4f}, {d['high']:+.4f}] | {m['D10']['switches']:.2f}→{m['D10P3']['switches']:.2f} |")
    lines+=['','## 预定判断','',
        '推进条件：两个步长均对 D10 有负的均值差且种子区间上界<0、每背景均值恶化不超过 0.5 秒，并且无服务/链/事件/运动异常。切换减少本身不算通过。原对 C1 的 0.25 秒收益门槛另行保留。','']
    for r in screens:lines.append(f"- dt={r['dt']}：开发推进条件={r['passes_development_screen']}；原 C1 收益筛选={r['passes_original_C1_screen']}。")
    lines+=['',f'整体推进判断：{advance}。',
        '三个种子区间精度有限；本轮仅测试这一候选，不能概括全部防抖机制或证明所有较少切换规则都不好。若不通过，保留原 D10 作为研究基准，不部署/采用 P3，也不在看过结果后改变门槛。',
        '所有日期均为已使用开发数据。若以后修改其他条件，需要独立冻结方案；若选出候选，需要未用于选择的新评价条件。']
    (folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    fig,ax=plt.subplots(figsize=(10,4.8));variants=['all','low','mixed','high'];x=np.arange(4)
    for j,dt in enumerate(p['steps']):
        cs=[next(r for r in comparisons if r['dt']==dt and r['variant']==v and r['comparison']=='D10P3-D10' and r['metric']=='awt') for v in variants]
        ax.errorbar(x+(j-.5)*.14,[r['mean'] for r in cs],yerr=[r['high']-r['mean'] for r in cs],fmt='o',capsize=5,label=f'dt={dt} s')
    ax.axhline(0,color='gray',lw=1);ax.set_xticks(x,['Overall','Low background','Mixed background','High background'])
    ax.set_ylabel('D10P3 - D10 waiting difference (s)');ax.legend();ax.grid(axis='y',alpha=.2)
    ax.set_title('One-factor development test: 3 versus 2 consecutive decisions')
    fig.text(.5,.02,'90 new runs + 270 reused baseline rows | model dd06745 | 3-seed t intervals; not untouched evaluation',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.05,1,1]);fig.savefig(folder/'persistence_comparison.png',dpi=170);plt.close(fig)
    print('Candidate advance:',advance,'screens:',screens)


if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['diagnosis','candidate']);ap.add_argument('folder',type=Path);args=ap.parse_args()
    (diagnosis if args.phase=='diagnosis' else candidate)(args.folder)
