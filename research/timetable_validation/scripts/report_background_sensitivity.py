"""Recompute background-rate contrasts from per-run aggregates and plot all conditions."""
import argparse
from collections import defaultdict
from pathlib import Path
from statistics import mean
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from repair_study_utils import load,save,sha,seed_interval
from study_paths import ROOT


def analyze(folder):
    p=load(folder/'protocol.json');e=load(folder/'execution.json')
    new=load(folder/'raw.json');base=load(folder/'reused_baselines.json');checks=load(folder/'input_checks.json')
    assert len(new)==e['runs']==p['runs']==180 and len(base)==90 and len(checks)==45
    assert sha(folder/'protocol.json')==e['protocol_hash']
    assert e['anomaly_totals']=={k:sum(r[k] for r in new) for k in e['anomaly_totals']}
    assert e['anomalous_runs']==sum(any(r[k] for k in e['anomaly_totals']) for r in new)
    historical=load(ROOT/'published_results/motion_repair_v1/raw.json')+load(ROOT/'published_results/motion_repair_extra_seeds_v1/raw.json')
    old={(r['date'],r['seed'],r['dt'],r['controller']):r for r in historical if r['variant']=='mixed' and r['dt'] in p['steps']}
    for r in base:assert {k:v for k,v in r.items() if k!='background_rate'}==old[(r['date'],r['seed'],r['dt'],r['controller'])]
    rows=new+base;idx={(r['date'],r['seed'],r['background_rate'],r['dt'],r['controller']):r for r in rows}
    assert len(idx)==270
    assert set(idx)=={(d,s,rate,dt,c) for d in p['dates'] for s in p['seeds'] for rate in p['rates'] for dt in p['steps'] for c in p['controllers']}
    cidx={(r['date'],r['seed'],r['background_rate']):r for r in checks};assert len(cidx)==45
    for day in p['dates']:
        for seed in p['seeds']:
            inputs=[cidx[(day,seed,rate)] for rate in p['rates']]
            for field in ['class_fingerprint','through_fingerprint','class_lift','through_lift']:
                assert len({r[field] for r in inputs})==1
            for r in inputs:
                assert r['class_and_through_unchanged']
                assert r['background_intent']==sum(r.get('background_'+m,0) for m in ['lift','stairs','same_floor'])
                for dt in p['steps']:
                    for c in p['controllers']:
                        run=idx[(day,seed,r['background_rate'],dt,c)]
                        assert run['input_sha256']==r['input_sha256'] and run['n']==r['total_lift_requests']
    means=[];contrasts=[];interactions=[];demand=[]
    for rate in p['rates']:
        ins=[r for r in checks if r['background_rate']==rate]
        demand.append(dict(rate=rate,**{k:mean(r[k] for r in ins) for k in ['background_intent','background_lift','class_lift','through_lift','total_lift_requests']}))
        for dt in p['steps']:
            for c in p['controllers']:
                part=[r for r in rows if r['background_rate']==rate and r['dt']==dt and r['controller']==c]
                means.append(dict(rate=rate,dt=dt,controller=c,**{k:mean(r[k] for r in part) for k in ['n','awt','p90','lwt','G_awt','other_awt','switches']}))
            for c in ['C7','D10']:
                for metric in ['awt','G_awt','other_awt','p90','lwt']:
                    differences={seed:[idx[(day,seed,rate,dt,c)][metric]-idx[(day,seed,rate,dt,'C1')][metric] for day in p['dates']] for seed in p['seeds']}
                    contrasts.append(dict(rate=rate,dt=dt,comparison=c+'-C1',metric=metric,
                        **seed_interval([mean(v) for v in differences.values()])))
                    if rate!=12:
                        changes=[]
                        for seed in p['seeds']:
                            reference=[idx[(day,seed,12,dt,c)][metric]-idx[(day,seed,12,dt,'C1')][metric] for day in p['dates']]
                            changes.append(mean(differences[seed])-mean(reference))
                        interactions.append(dict(rate=rate,reference_rate=12,dt=dt,comparison=c+'-C1',metric=metric,
                            **seed_interval(changes)))
    return dict(means=means,contrasts=contrasts,rate_interactions=interactions,demand=demand,
        anomaly_totals=e['anomaly_totals'],new_runs=180,reused_baseline_runs=90),p


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--check',action='store_true');args=ap.parse_args()
    summary,p=analyze(args.folder)
    if args.check:
        assert summary==load(args.folder/'summary.json')
        print('PASS: 180 new runs + 90 exact archived rows; input layers, seed contrasts and background interactions verified.')
        return
    save(args.folder/'summary.json',summary)
    lines=['# 背景人流强度检查','',
        '在原开发日期 10 月 26–30 日和三个种子上，将独立背景出行意图设为每 5 分钟 6、12、24 次；背景楼层分布固定为 mixed。课堂人流、转课连接、过路人流及全部策略参数保持不变。6/24 两档新增 180 次，12 档复用同版本 90 条结果；每档均比较 C1/C7/D10 和 0.25/0.125 秒步长。','',
        '取值沿用早期实验的半量/基准/倍量假设。出行意图经过同层和楼梯分流后才形成电梯请求，因此不是实际观测的乘梯到达率。','',
        '## 输入核验','',
        '| 每5分钟背景意图 | 全天背景意图均值 | 背景乘梯请求均值 | 课堂乘梯均值 | 过路乘梯均值 |','|---|---:|---:|---:|---:|']
    for r in summary['demand']:
        lines.append(f"| {r['rate']} | {r['background_intent']:.1f} | {r['background_lift']:.1f} | {r['class_lift']:.1f} | {r['through_lift']:.1f} |")
    lines+=['','课堂和过路行程的时间、起终点及相关元数据指纹跨强度一致；每一强度内各策略/步长使用同一输入。背景请求的时间和数量随强度改变，跨强度比较以日期/种子配对。','',
        '## 策略结果','',
        '| 强度 | 步长 | C1 等待 | C7 等待 | D10 等待 | D10−C1 [95%种子区间] | D10切换次数 |',
        '|---|---|---:|---:|---:|---|---:|']
    for rate in p['rates']:
        for dt in p['steps']:
            m={r['controller']:r for r in summary['means'] if r['rate']==rate and r['dt']==dt}
            c=next(r for r in summary['contrasts'] if r['rate']==rate and r['dt']==dt and r['comparison']=='D10-C1' and r['metric']=='awt')
            lines.append(f"| {rate} | {dt} | {m['C1']['awt']:.4f} | {m['C7']['awt']:.4f} | {m['D10']['awt']:.4f} | {c['mean']:+.4f} [{c['low']:+.4f}, {c['high']:+.4f}] | {m['D10']['switches']:.2f} |")
    lines+=['','## 相对基准强度的策略差变化','',
        '| 强度对比 | 步长 | (D10−C1)变化 [95%种子区间] |','|---|---|---|']
    for r in summary['rate_interactions']:
        if r['comparison']=='D10-C1' and r['metric']=='awt':
            lines.append(f"| {r['rate']}−12 | {r['dt']} | {r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}] |")
    lines+=['','各区间使用三个种子内的五日均值；只描述该组日期、mixed 楼层分布和当前模型下的随机变动。',
        '新运行的服务、链、迟到、事件时间和运动约束检查总计：'+str(summary['anomaly_totals'])+'。',
        '完整 P90、长等待、G/非 G 和 C7−C1 比较见 summary.json。参数敏感性用于判断结论依赖哪些输入；实际强度需要独立客流资料。']
    (args.folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    fig,axes=plt.subplots(1,2,figsize=(11,4.5))
    for c in p['controllers']:
        vals=[next(r['awt'] for r in summary['means'] if r['rate']==rate and r['dt']==.125 and r['controller']==c) for rate in p['rates']]
        axes[0].plot(p['rates'],vals,'o-',label=c)
    axes[0].set_ylabel('Mean waiting (s), dt=0.125 s');axes[0].legend()
    for j,dt in enumerate(p['steps']):
        cs=[next(r for r in summary['contrasts'] if r['rate']==rate and r['dt']==dt and r['comparison']=='D10-C1' and r['metric']=='awt') for rate in p['rates']]
        axes[1].errorbar(np.array(p['rates'])+(j-.5)*.6,[r['mean'] for r in cs],yerr=[r['high']-r['mean'] for r in cs],fmt='o',capsize=4,label=f'dt={dt}')
    axes[1].axhline(0,color='gray',lw=1);axes[1].set_ylabel('D10 - C1 waiting difference (s)');axes[1].legend()
    for ax in axes:ax.set_xticks(p['rates']);ax.set_xlabel('Background trip intents per 5 min');ax.grid(alpha=.2)
    fig.suptitle('Background intensity sensitivity: fixed mixed-floor distribution')
    fig.text(.5,.02,'180 new runs + 90 reused baseline rows | 5 dates x 3 seeds | error bars: 95% seed-level t intervals',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.06,1,.94]);fig.savefig(args.folder/'background_rate.png',dpi=180);plt.close(fig)
    print('Background report complete:',summary['anomaly_totals'])


if __name__=='__main__':main()
