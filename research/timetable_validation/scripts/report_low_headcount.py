"""Recompute low-background headcount contrasts overall and per teaching week."""
import argparse
from pathlib import Path
from statistics import mean
from repair_study_utils import load,save,sha,seed_interval
from study_paths import ROOT
from run_background_sensitivity import KEYS
from report_crossweek import check_demand_ledger

METRICS=['awt','p90','lwt','G_awt','other_awt']
PAIRS=[('D10','C1'),('C7','C1'),('D10','C7')]


def analyze(folder):
    p=load(folder/'protocol.json');e=load(folder/'execution.json')
    new=load(folder/'raw.json');baseline=load(folder/'reused_baselines.json');checks=load(folder/'input_checks.json')
    assert e['protocol_hash']==sha(folder/'protocol.json')
    assert len(new)==p['runs']==e['runs']==540 and len(baseline)==p['reused_runs']==e['reused_runs']==270
    prior=ROOT/'published_results/crossweek_repaired_v1'
    assert baseline==[r for r in load(prior/'raw.json') if r['variant']=='low']
    assert p['baseline_hashes']=={n:sha(prior/n) for n in p['baseline_hashes']}
    assert p['revision']==load(prior/'protocol.json')['revision']
    rows=new+[dict(r,capacity_fraction=.6) for r in baseline]
    key=lambda r:(r['date'],r['seed'],r['capacity_fraction'],r['dt'],r['controller'])
    index={key(r):r for r in rows}
    expected={(d,s,f,dt,c) for d in p['dates'] for s in p['seeds'] for f in p['fractions'] for dt in p['steps'] for c in p['controllers']}
    assert len(index)==len(rows)==810 and set(index)==expected
    ci={(r['date'],r['seed'],r['capacity_fraction']):r for r in checks}
    assert len(ci)==len(checks)==e['paired_inputs']==p['paired_inputs']==135
    assert set(ci)=={(d,s,f) for d in p['dates'] for s in p['seeds'] for f in p['fractions']}
    daily=load(folder/'daily_coverage.json');assert len(daily)==15 and {r['date'] for r in daily}==set(p['dates'])
    date_week={d:w['role'] for w in p['weeks'] for d in w['dates']}
    assert set(date_week)==set(p['dates'])
    for w in p['weeks']:
        part=[d for d in daily if d['week']==w['role']]
        assert {d['date'] for d in part}==set(w['dates'])
        assert sum(d['selected'] for d in part)==w['selected_sessions']
        assert sum(d['assumed'] for d in part)==w['assumed_headcount_sessions']
    for r in checks:
        assert r['week']==date_week[r['date']]
        base=ci[(r['date'],r['seed'],.6)]
        assert all(r[k] for k in ['known_headcounts_unchanged','known_attendance_unchanged','selected_sessions_unchanged','background_unchanged','through_unchanged'])
        for k in ['selected_sessions','known_headcount_sum','background_fingerprint','through_fingerprint']:assert r[k]==base[k]
        day=next(d for d in daily if d['date']==r['date'])
        assert r['selected_sessions']==day['selected']==day['known']+day['assumed']
        check_demand_ledger(r)
        for dt in p['steps']:
            for c in p['controllers']:
                row=index[(r['date'],r['seed'],r['capacity_fraction'],dt,c)]
                assert row['input_sha256']==r['input_sha256'] and row['n']==r['n'] and row['variant']=='low' and row['week']==r['week']
    totals={k:sum(r[k] for r in new) for k in KEYS}
    assert e['anomaly_totals']==totals and e['anomalous_runs']==sum(any(r[k] for k in KEYS) for r in new)
    summary=summarize_scopes(index,rows,checks,p)
    return dict(new_runs=540,reused_runs=270,**summary,daily_coverage=daily,new_anomaly_totals=totals,
        all_anomaly_totals={k:sum(r[k] for r in rows) for k in KEYS},
        units=dict(awt='seconds',p90='seconds; mean of per-run percentiles',lwt='percent; contrasts in percentage points',G_awt='seconds',other_awt='seconds'))


def summarize_scopes(index,rows,checks,p):
    means=[];contrasts=[];changes=[];demand=[]
    scopes=[('all',p['dates'])]+[(w['role'],w['dates']) for w in p['weeks']]
    for week,dates in scopes:
        for dt in p['steps']:
            for f in p['fractions']:
                for c in p['controllers']:
                    group=[r for r in rows if r['date'] in dates and r['capacity_fraction']==f and r['dt']==dt and r['controller']==c]
                    means.append(dict(week=week,capacity_fraction=f,dt=dt,controller=c,
                        **{m:mean(r[m] for r in group) for m in ['n','switches']+METRICS}))
            for left,right in PAIRS:
                for metric in METRICS:
                    values={f:[mean(index[(d,s,f,dt,left)][metric]-index[(d,s,f,dt,right)][metric] for d in dates) for s in p['seeds']] for f in p['fractions']}
                    for f in p['fractions']:
                        contrasts.append(dict(week=week,capacity_fraction=f,dt=dt,comparison=left+'-'+right,metric=metric,
                            seed_means=values[f],**seed_interval(values[f])))
                        if f!=.6:
                            delta=[a-b for a,b in zip(values[f],values[.6])]
                            changes.append(dict(week=week,capacity_fraction=f,reference_fraction=.6,dt=dt,comparison=left+'-'+right,
                                metric=metric,seed_means=delta,**seed_interval(delta)))
        for f in p['fractions']:
            group=[r for r in checks if r['date'] in dates and r['capacity_fraction']==f]
            demand.append(dict(week=week,capacity_fraction=f,**{k:mean(r[k] for r in group)
                for k in ['n','attendance_slots','transfer_links','known_headcount_sum','assumed_headcount_sum']}))
    return dict(means=means,contrasts=contrasts,contrast_changes=changes,demand=demand)


def make_report(s):
    lines=['# 低层背景下：人数假设与策略收益','',
        '沿用 9 月 21–25 日、10 月 5–9 日、11 月 16–20 日；low 背景、种子 6000–6002、C1/C7/D10、0.25/0.125 秒步长。缺失人数取容量的 40%/60%/80%，之后仍按 80% 抽样出勤。新增 540 次，原样复用此前 270 次 low/60% 基线。','',
        '| 周 | 人数比例 | 步长 | D10−C1 [95%种子区间]/秒 | D10−C7 [95%种子区间]/秒 | C7−C1/秒 |',
        '|---|---|---|---|---|---:|']
    for week in ['all','early','middle','late']:
        for f in [.4,.6,.8]:
            for dt in [.25,.125]:
                cs={r['comparison']:r for r in s['contrasts'] if r['week']==week and r['capacity_fraction']==f and r['dt']==dt and r['metric']=='awt'}
                fmt=lambda r:f"{r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}]"
                lines.append(f"| {week} | {f:.0%} | {dt} | {fmt(cs['D10-C1'])} | {fmt(cs['D10-C7'])} | {cs['C7-C1']['mean']:+.4f} |")
    lines+=['','## 相对60%人数假设的策略差变化（三周汇总）','',
        '| 人数比例 | 步长 | 对比 | 策略差变化 [95%种子区间]/秒 |','|---|---|---|---|']
    for r in s['contrast_changes']:
        if r['week']=='all' and r['metric']=='awt':
            lines.append(f"| {r['capacity_fraction']:.0%} | {r['dt']} | {r['comparison']} | {r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}] |")
    lines+=['','## 需求与核验','',
        '| 人数比例 | 日均乘梯请求 | 日均抽取出勤人次 | 日均转课连接 |','|---|---:|---:|---:|']
    for r in s['demand']:
        if r['week']=='all':lines.append(f"| {r['capacity_fraction']:.0%} | {r['n']:.1f} | {r['attendance_slots']:.1f} | {r['transfer_links']:.1f} |")
    lines+=['',
        '每个日期/种子/人数比例内，全部策略与步长共用乘客输入。跨人数比例保持已知人数及其出勤抽样、背景与过路行程一致；未知人数及转课链可以变化。60% 行与既有跨周归档逐行核对。','',
        '总体先在种子内对 15 个日期等权平均，再用三个种子计算 95% t 区间；分周为五日均值。区间只描述模型内随机变化，分组比较未作多重比较校正。相对 60% 的变化为配对策略差的变化。','',
        '新增运行六类异常：'+str(s['new_anomaly_totals'])+'。逐周策略差变化、P90、长等待百分比、G/非G等待与切换次数见 summary.json。','',
        '这是一项由已有 low 背景结果引出的敏感性检查，所用日期已经研究过。它不重新选择阈值，也不代替涵盖不同背景的独立评价；high 和跨周 mixed 的人数交互仍未检验，备用周未运行。低层背景和 40/60/80% 是假设条件，不能视为 KKL 实际背景分布或实际人数区间。']
    return '\n'.join(lines)+'\n'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--check',action='store_true');a=ap.parse_args()
    s=analyze(a.folder);report=make_report(s)
    if a.check:
        assert s==load(a.folder/'summary.json') and report==(a.folder/'结果说明.md').read_text(encoding='utf-8')
        print('PASS: 540 new runs, 270 exact baselines, isolated inputs, paired intervals and contrast changes.')
    else:
        save(a.folder/'summary.json',s);(a.folder/'结果说明.md').write_text(report,encoding='utf-8',newline='\n')
        print('Low-headcount report saved; anomalies:',s['new_anomaly_totals'])


if __name__=='__main__':main()
