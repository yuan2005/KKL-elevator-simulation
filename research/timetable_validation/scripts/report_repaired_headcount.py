"""Recompute missing-headcount effects and changes of paired policy contrasts."""
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
    assert len(new)==p['runs']==e['runs']==180 and len(baseline)==p['reused_runs']==e['reused_runs']==90
    prior=ROOT/'published_results/room_revision_repaired_v1'
    assert baseline==load(prior/'raw.json')
    assert p['baseline_hashes']=={n:sha(prior/n) for n in p['baseline_hashes']}
    assert p['revision']==load(prior/'protocol.json')['revision']
    rows=new+[dict(r,capacity_fraction=.6) for r in baseline]
    key=lambda r:(r['date'],r['seed'],r['capacity_fraction'],r['dt'],r['controller'])
    index={key(r):r for r in rows}
    expected={(d,s,f,dt,c) for d in p['dates'] for s in p['seeds'] for f in p['fractions'] for dt in p['steps'] for c in p['controllers']}
    assert len(index)==len(rows)==270 and set(index)==expected
    ci={(r['date'],r['seed'],r['capacity_fraction']):r for r in checks}
    assert len(ci)==len(checks)==e['paired_inputs']==p['paired_inputs']==45
    assert set(ci)=={(d,s,f) for d in p['dates'] for s in p['seeds'] for f in p['fractions']}
    daily=load(folder/'daily_coverage.json');assert len(daily)==5 and {r['date'] for r in daily}==set(p['dates'])
    for r in checks:
        base=ci[(r['date'],r['seed'],.6)]
        assert all(r[k] for k in ['known_headcounts_unchanged','known_attendance_unchanged','selected_sessions_unchanged','background_unchanged','through_unchanged'])
        for k in ['selected_sessions','known_headcount_sum','background_fingerprint','through_fingerprint']:assert r[k]==base[k]
        day=next(d for d in daily if d['date']==r['date'])
        assert r['selected_sessions']==day['selected']==day['known']+day['assumed']
        check_demand_ledger(r)
        for dt in p['steps']:
            for c in p['controllers']:
                row=index[(r['date'],r['seed'],r['capacity_fraction'],dt,c)]
                assert row['input_sha256']==r['input_sha256'] and row['n']==r['n'] and row['variant']=='mixed'
    totals={k:sum(r[k] for r in new) for k in KEYS}
    assert e['anomaly_totals']==totals and e['anomalous_runs']==sum(any(r[k] for k in KEYS) for r in new)
    means=[];contrasts=[];changes=[]
    for dt in p['steps']:
        for f in p['fractions']:
            for c in p['controllers']:
                group=[r for r in rows if r['capacity_fraction']==f and r['dt']==dt and r['controller']==c]
                means.append(dict(capacity_fraction=f,dt=dt,controller=c,**{m:mean(r[m] for r in group) for m in ['n','switches']+METRICS}))
        for left,right in PAIRS:
            for metric in METRICS:
                values={f:[mean(index[(d,s,f,dt,left)][metric]-index[(d,s,f,dt,right)][metric] for d in p['dates']) for s in p['seeds']] for f in p['fractions']}
                for f in p['fractions']:
                    contrasts.append(dict(capacity_fraction=f,dt=dt,comparison=left+'-'+right,metric=metric,seed_means=values[f],**seed_interval(values[f])))
                    if f!=.6:
                        delta=[a-b for a,b in zip(values[f],values[.6])]
                        changes.append(dict(capacity_fraction=f,reference_fraction=.6,dt=dt,comparison=left+'-'+right,metric=metric,seed_means=delta,**seed_interval(delta)))
    demand=[dict(capacity_fraction=f,**{k:mean(r[k] for r in checks if r['capacity_fraction']==f)
        for k in ['n','attendance_slots','transfer_links','known_headcount_sum','assumed_headcount_sum']}) for f in p['fractions']]
    return dict(new_runs=180,reused_runs=90,means=means,contrasts=contrasts,contrast_changes=changes,demand=demand,
        daily_coverage=daily,new_anomaly_totals=totals,all_anomaly_totals={k:sum(r[k] for r in rows) for k in KEYS},
        units=dict(awt='seconds',p90='seconds; mean of per-run percentiles',lwt='percent; contrasts in percentage points',G_awt='seconds',other_awt='seconds'))


def make_report(s):
    lines=['# 修复模型、新版教室输入的缺失人数敏感性','',
        '10 月 26–30 日，mixed 背景，种子 4000–4002，C1/C7/D10，0.25/0.125 秒步长。只把已纳入但缺少正式人数的班别设为 round(教室容量 × 40%/60%/80%)。40%/80% 新增 180 次，60% 原样复用新版教室实验的 90 次；人数确定后仍使用 80% 出勤抽样。','',
        '| 人数比例 | 步长 | C1等待/秒 | C7等待/秒 | D10等待/秒 | D10−C1 [95%种子区间]/秒 | D10切换/日 |',
        '|---|---|---:|---:|---:|---|---:|']
    for f in [.4,.6,.8]:
        for dt in [.25,.125]:
            m={r['controller']:r for r in s['means'] if r['capacity_fraction']==f and r['dt']==dt}
            c=next(r for r in s['contrasts'] if r['capacity_fraction']==f and r['dt']==dt and r['comparison']=='D10-C1' and r['metric']=='awt')
            lines.append(f"| {f:.0%} | {dt} | {m['C1']['awt']:.4f} | {m['C7']['awt']:.4f} | {m['D10']['awt']:.4f} | {c['mean']:+.4f} [{c['low']:+.4f}, {c['high']:+.4f}] | {m['D10']['switches']:.2f} |")
    lines+=['','| 人数比例 | 步长 | 相对60%的 (D10−C1) 变化 [95%种子区间]/秒 |','|---|---|---|']
    for r in s['contrast_changes']:
        if r['comparison']=='D10-C1' and r['metric']=='awt':lines.append(f"| {r['capacity_fraction']:.0%} | {r['dt']} | {r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}] |")
    lines+=['','| 人数比例 | 日均乘梯请求 | 日均抽取出勤人次 | 日均转课连接 |','|---|---:|---:|---:|']
    for r in s['demand']:lines.append(f"| {r['capacity_fraction']:.0%} | {r['n']:.1f} | {r['attendance_slots']:.1f} | {r['transfer_links']:.1f} |")
    lines+=['',
        '每个日期/种子/人数比例内，策略与步长共享同一组乘客输入。比例之间保持已知班别人数及出勤抽样、独立背景与过路行程一致；缺失人数及转课链可以变化。原样复用的 60% 基线逐行核对来源归档。','',
        '区间使用三个种子的五日均值。相对 60% 的变化比较配对的策略差值，不把不同人数下的乘客视为同一批。区间为条件性 Monte Carlo 不确定性，未作多重比较校正。','',
        '六类新增运行异常：'+str(s['new_anomaly_totals'])+'。P90、长等待百分比、G/非G及 C7 完整比较见 summary.json。','',
        '40%/60%/80% 是探索性人数假设，并非已验证的实际人数范围。本批覆盖原开发周的 mixed 背景；三周 low/high 条件下的收益仍对应原 60% 人数假设。阈值不随本批结果重新选择。']
    return '\n'.join(lines)+'\n'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--check',action='store_true');a=ap.parse_args()
    s=analyze(a.folder);report=make_report(s)
    if a.check:
        assert s==load(a.folder/'summary.json') and report==(a.folder/'结果说明.md').read_text(encoding='utf-8')
        print('PASS: 180 new runs, 90 exact baselines, isolated inputs, paired intervals and contrast changes.')
    else:
        save(a.folder/'summary.json',s);(a.folder/'结果说明.md').write_text(report,encoding='utf-8',newline='\n')
        print('Repaired-headcount report saved; anomalies:',s['new_anomaly_totals'])


if __name__=='__main__':main()
