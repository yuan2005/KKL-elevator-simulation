"""Public reconstruction and paired contrasts for the schedule-date correction."""
import argparse
from pathlib import Path
from statistics import mean
from repair_study_utils import load,save,sha,seed_interval
from report_crossweek import row_key,paired_index,contrast,check_demand_ledger,METRICS,COMPARISONS
from run_background_sensitivity import KEYS
from study_paths import ROOT


def assemble(old,new,affected):
    """Replace affected cells without counting the other archived rows again."""
    assert len({row_key(r) for r in old})==len(old)
    assert len({row_key(r) for r in new})==len(new)
    affected=set(affected)
    assert {row_key(r) for r in new}=={row_key(r) for r in old if r['date'] in affected}
    return sorted(new+[r for r in old if r['date'] not in affected],key=row_key)


def change(old,new):
    assert len(old)==len(new)
    values=[b-a for a,b in zip(old,new)]
    return dict(seed_means=values,**seed_interval(values))


def analyze(folder):
    p=load(folder/'protocol.json');e=load(folder/'execution.json')
    old=load(folder/'reused_baselines.json');new=load(folder/'raw.json')
    assert old==load(ROOT/'published_results/crossweek_repaired_v1/raw.json')
    assert sha(ROOT/'published_results/crossweek_repaired_v1/raw.json')==p['baseline_hash']
    assert e['protocol_hash']==sha(folder/'protocol.json')
    assert e['runs']==p['new_runs']==len(new)==486 and e['inputs']==p['paired_inputs']==81
    assert p['frozen_selection_hash']==sha(ROOT/'published_results/adaptive_v1/frozen_selection.json')
    original=load(ROOT/'published_results/crossweek_repaired_v1/protocol.json')
    assert all(p[k]==original[k] for k in ['dates','weeks','seeds','variants','steps','controllers','base','variant_parameters','source_hashes'])
    revised=assemble(old,new,p['affected_dates'])
    assert len(old)==len(revised)==810
    indices={v:paired_index(rows,p) for v,rows in [('original',old),('revised',revised)]}
    checks=load(folder/'input_checks.json');proof=load(folder/'preparation_checks.json')
    key=lambda r:(r['date'],r['variant'],r['seed'])
    ci={key(r):r for r in checks};pi={key(r):r for r in proof}
    expected={(d,v,s) for d in p['affected_dates'] for v in p['variants'] for s in p['seeds']}
    assert len(ci)==len(checks)==len(pi)==len(proof)==81 and set(ci)==set(pi)==expected
    daily=load(folder/'daily_coverage.json');di={r['date']:r for r in daily}
    old_daily={r['date']:r for r in load(ROOT/'published_results/crossweek_repaired_v1/daily_coverage.json')}
    assert len(di)==len(daily)==15 and set(di)==set(p['dates'])
    date_week={d:w['role'] for w in p['weeks'] for d in w['dates']}
    for d,r in di.items():
        assert r['week']==date_week[d] and r['old_selected']==old_daily[d]['selected']
        assert r['new_selected']==r['old_selected']+int(d in p['affected_dates'])
        assert r['new_assumed']==r['old_assumed']+int(d in p['affected_dates'])
        assert r['window']==old_daily[d]['window']
    for k,r in ci.items():
        pr=pi[k];check_demand_ledger(r)
        assert all(pr[f] for f in ['baseline_hash_matches','retained_attendance_matches','background_and_through_match'])
        assert r['grid_decisions_match'] and r['controller_grid_inputs_match']
        assert r['week']==date_week[r['date']]
        assert r['selected_sessions']==di[r['date']]['new_selected']
        assert r['input_sha256']==pr['new_sha256'] and r['n']==pr['new_n']
        for dt in p['steps']:
            for c in p['controllers']:
                for version,label in [('original','old'),('revised','new')]:
                    row=indices[version][(*k,dt,c)]
                    assert row['week']==date_week[r['date']]
                    assert row['input_sha256']==pr[label+'_sha256'] and row['n']==pr[label+'_n']
    totals={k:sum(r[k] for r in new) for k in KEYS}
    assert totals==e['anomaly_totals']
    assert e['anomalous_runs']==sum(any(r[k] for k in KEYS) for r in new)
    scopes=[('all',p['dates'])]+[(w['role'],w['dates']) for w in p['weeks']]
    variants=[('all',p['variants'])]+[(v,[v]) for v in p['variants']]
    means=[];contrasts=[];changes=[]
    for week,dates in scopes:
        for variant,vs in variants:
            for dt in p['steps']:
                for version,index in indices.items():
                    for c in p['controllers']:
                        group=[index[(d,v,s,dt,c)] for d in dates for v in vs for s in p['seeds']]
                        means.append(dict(version=version,week=week,variant=variant,dt=dt,controller=c,runs=len(group),
                            **{m:mean(r[m] for r in group) for m in ['n','switches']+METRICS}))
                for left,right in COMPARISONS:
                    for metric in METRICS:
                        results={v:contrast(index,dates,vs,p['seeds'],dt,left,right,metric) for v,index in indices.items()}
                        meta=dict(week=week,variant=variant,dt=dt,comparison=left+'-'+right,metric=metric)
                        contrasts.extend(dict(version=v,**meta,**r) for v,r in results.items())
                        changes.append(dict(**meta,**change(results['original']['seed_means'],results['revised']['seed_means'])))
    screens=[]
    for dt in p['steps']:
        r=next(c for c in contrasts if c['version']=='revised' and c['week']==c['variant']=='all' and c['dt']==dt and c['comparison']=='D10-C1' and c['metric']=='awt')
        groups=[c for c in contrasts if c['version']=='revised' and c['week']=='all' and c['variant']!='all' and c['dt']==dt and c['comparison']=='D10-C1' and c['metric']=='awt']
        limit=p['benefit_screen']
        conditions=dict(saving_at_least_025=-r['mean']>=limit['minimum_saving_seconds'],interval_below_zero=r['high']<0,
            no_variant_regression_over_05=max(c['mean'] for c in groups)<=limit['max_variant_regression_seconds'],zero_anomalies=not any(totals.values()))
        screens.append(dict(dt=dt,mean_saving=-r['mean'],**conditions,passes=all(conditions.values())))
    demand=[dict(variant=v,old_n=mean(r['old_n'] for r in proof if r['variant']==v),new_n=mean(r['new_n'] for r in proof if r['variant']==v)) for v in p['variants']]
    return dict(new_runs=486,reused_affected_baselines=486,reused_unchanged_rows=324,full_comparison_rows_per_version=810,
        means=means,contrasts=contrasts,contrast_changes=changes,benefit_screen=screens,demand_affected_dates=demand,
        anomaly_totals=totals,old_selected=sum(r['old_selected'] for r in daily),new_selected=sum(r['new_selected'] for r in daily))


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--check',action='store_true');a=ap.parse_args()
    s=analyze(a.folder)
    if a.check:
        assert s==load(a.folder/'summary.json');print('PASS: schedule correction 486 new runs, exact archived reuse, 81 input checks and paired intervals.');return
    save(a.folder/'summary.json',s)
    lines=['# AILT9018 日期修订后的策略比较','',
        '仅修订已核验的 AILT9018 分班 tutorial 日期。9 个受影响日期、三种背景、种子 6000–6002、C1/C7/D10、步长 0.25/0.125 秒，新增 486 次仿真。对照使用旧版 486 条受影响记录；另 324 条未受影响记录复用以重组完整三周 810 条结果。',
        '', '原模型、D10 阈值、缺失人数的 60% 容量假设和 80% 出勤假设不变；原课次编号、已有出勤抽样、独立背景与过路行程保持一致。新增课堂可改变转课链。','',
        '| 步长 | 输入 | C1平均等待/秒 | C7平均等待/秒 | D10平均等待/秒 | D10−C1 [95%种子区间]/秒 |',
        '|---|---|---:|---:|---:|---|']
    for dt in [.25,.125]:
        for version in ['original','revised']:
            m={r['controller']:r for r in s['means'] if r['version']==version and r['week']==r['variant']=='all' and r['dt']==dt}
            r=next(r for r in s['contrasts'] if r['version']==version and r['week']==r['variant']=='all' and r['dt']==dt and r['comparison']=='D10-C1' and r['metric']=='awt')
            lines.append(f"| {dt} | {version} | {m['C1']['awt']:.4f} | {m['C7']['awt']:.4f} | {m['D10']['awt']:.4f} | {r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}] |")
    lines+=['','| 步长 | 比较 | 修订造成的策略差变化 [95%种子区间]/秒 |','|---|---|---|']
    for r in s['contrast_changes']:
        if r['week']==r['variant']=='all' and r['metric']=='awt':lines.append(f"| {r['dt']} | {r['comparison']} | {r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}] |")
    lines+=['',f"三周选取课次 {s['old_selected']}→{s['new_selected']}；全学期覆盖由 2,957→2,987 是另一统计范围。",'',
        '总体汇总对既定日期和三个假设背景等权；区间使用三个种子的配对均值，表示模型条件下的随机不确定性。完整逐周、逐背景、P90、长等待及 G/非G 结果见 summary.json。', '',
        '六类异常计数：'+str(s['anomaly_totals'])+'。', '',
        '收益门槛检查：'+str(s['benefit_screen'])+'。', '',
        '本批未调整规则，也未运行 11 月 23–27 日保留周。公开课表日期和容量假设仍不能替代实际到课、乘梯或背景人流测量。']
    (a.folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    print('Schedule revision report saved.')


if __name__=='__main__':main()
