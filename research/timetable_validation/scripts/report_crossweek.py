"""Public aggregate audit and paired, seed-clustered cross-week summaries."""
import argparse
from pathlib import Path
from statistics import mean
from repair_study_utils import load,save,sha,seed_interval
from run_background_sensitivity import KEYS
from study_paths import ROOT

METRICS=['awt','p90','lwt','G_awt','other_awt']
COMPARISONS=[('D10','C1'),('C7','C1'),('D10','C7')]


def row_key(r):return (r['date'],r['variant'],r['seed'],r['dt'],r['controller'])


def paired_index(rows,p):
    index={row_key(r):r for r in rows}
    expected={(d,v,s,dt,c) for d in p['dates'] for v in p['variants'] for s in p['seeds'] for dt in p['steps'] for c in p['controllers']}
    assert len(index)==len(rows) and set(index)==expected,'Incomplete or duplicate experiment cells'
    for d in p['dates']:
        for v in p['variants']:
            for s in p['seeds']:
                group=[index[(d,v,s,dt,c)] for dt in p['steps'] for c in p['controllers']]
                assert len({r['input_sha256'] for r in group})==len({r['n'] for r in group})==1,'Unpaired input'
    return index


def contrast(index,dates,variants,seeds,dt,left,right,metric):
    values=[mean(index[(d,v,s,dt,left)][metric]-index[(d,v,s,dt,right)][metric]
        for d in dates for v in variants) for s in seeds]
    return dict(seed_means=values,**seed_interval(values))


def check_demand_ledger(r):
    ledger=r['ledger']
    assert sum(ledger.get(layer+'_lift',0) for layer in ['class','background','through'])==r['n']
    assert ledger['class_intent']==2*r['attendance_slots']-r['transfer_links']
    for layer in ['class','background','through']:
        assert ledger.get(layer+'_intent',0)==sum(ledger.get(layer+'_'+mode,0) for mode in ['lift','stairs','same_floor'])


def analyze(folder):
    p=load(folder/'protocol.json');e=load(folder/'execution.json');rows=load(folder/'raw.json')
    assert e['protocol_hash']==sha(folder/'protocol.json')
    assert len(rows)==e['runs']==p['runs']==810
    assert e['inputs']==p['inputs']==135
    assert p['plan_hash']==sha(ROOT/'published_results/semester_coverage_v1/next_experiment_plan.json')
    assert p['frozen_selection_hash']==sha(ROOT/'published_results/adaptive_v1/frozen_selection.json')
    assert p['revision']==load(ROOT/'published_results/room_revision_repaired_v1/protocol.json')['revision']
    index=paired_index(rows,p);checks=load(folder/'input_checks.json');daily=load(folder/'daily_coverage.json')
    checkindex={(r['date'],r['variant'],r['seed']):r for r in checks}
    assert len(checkindex)==len(checks)==135
    assert set(checkindex)=={(d,v,s) for d in p['dates'] for v in p['variants'] for s in p['seeds']}
    date_week={d:w['role'] for w in p['weeks'] for d in w['dates']}
    assert len(daily)==15 and {r['date'] for r in daily}==set(p['dates'])
    for w in p['weeks']:
        selected=[r for r in daily if r['week']==w['role']]
        assert {r['date'] for r in selected}==set(w['dates'])
        assert sum(r['selected'] for r in selected)==w['selected_sessions']
        assert sum(r['assumed_headcount_sessions'] for r in selected)==w['assumed_headcount_sessions']
    for r in checks:
        assert r['week']==date_week[r['date']]
        assert r['controller_grid_inputs_match'] and r['grid_decisions_match']
        assert r['selected_sessions']==next(d['selected'] for d in daily if d['date']==r['date'])
        check_demand_ledger(r)
        for dt in p['steps']:
            for c in p['controllers']:
                row=index[(r['date'],r['variant'],r['seed'],dt,c)]
                assert row['week']==r['week'] and row['input_sha256']==r['input_sha256'] and row['n']==r['n']
    totals={k:sum(r[k] for r in rows) for k in KEYS}
    assert e['anomaly_totals']==totals
    assert e['anomalous_runs']==sum(any(r[k] for k in KEYS) for r in rows)
    scopes=[('all',p['dates'])]+[(w['role'],w['dates']) for w in p['weeks']]
    variants=[('all',p['variants'])]+[(v,[v]) for v in p['variants']]
    means=[];contrasts=[]
    for week,dates in scopes:
        for variant,vs in variants:
            for dt in p['steps']:
                for c in p['controllers']:
                    group=[index[(d,v,s,dt,c)] for d in dates for v in vs for s in p['seeds']]
                    means.append(dict(week=week,variant=variant,dt=dt,controller=c,runs=len(group),
                        **{m:mean(r[m] for r in group) for m in ['n','switches']+METRICS}))
                for left,right in COMPARISONS:
                    for metric in METRICS:
                        contrasts.append(dict(week=week,variant=variant,dt=dt,comparison=left+'-'+right,metric=metric,
                            **contrast(index,dates,vs,p['seeds'],dt,left,right,metric)))
    screens=[]
    for dt in p['steps']:
        overall=next(r for r in contrasts if r['week']==r['variant']=='all' and r['dt']==dt and r['comparison']=='D10-C1' and r['metric']=='awt')
        variant_rows=[r for r in contrasts if r['week']=='all' and r['variant']!='all' and r['dt']==dt and r['comparison']=='D10-C1' and r['metric']=='awt']
        limits=p['benefit_screen']
        conditions=dict(saving_at_least_025=-overall['mean']>=limits['minimum_saving_seconds'],
            interval_below_zero=overall['high']<limits['interval_upper_below'],
            no_variant_regression_over_05=max(r['mean'] for r in variant_rows)<=limits['max_variant_regression_seconds'],
            zero_anomalies=not any(totals.values()))
        screens.append(dict(dt=dt,mean_saving=-overall['mean'],**conditions,passes=all(conditions.values())))
    grid=[]
    for c in p['controllers']:
        values=[mean(index[(d,v,s,.125,c)]['awt']-index[(d,v,s,.25,c)]['awt'] for d in p['dates'] for v in p['variants']) for s in p['seeds']]
        grid.append(dict(controller=c,comparison='dt0.125-dt0.25',**seed_interval(values)))
    return dict(new_runs=810,paired_inputs=135,means=means,contrasts=contrasts,benefit_screens=screens,
        grid_awt_changes=grid,anomaly_totals=totals,daily_coverage=daily,
        units=dict(awt='seconds',p90='seconds',lwt='percent; contrasts in percentage points',G_awt='seconds',other_awt='seconds'),
        aggregation='Equal-weight run metrics; P90 is a mean of per-run P90 values, not a pooled percentile.')


def make_report(s):
    lines=['# 三个教学周的固定规则比较','',
        '9 月 21–25 日、10 月 5–9 日、11 月 16–20 日；新版教室登记表、种子 6000–6002、low/mixed/high 背景、C1/C7/D10、0.25/0.125 秒步长，共 810 次新运行。所有策略与步长在每个日期/背景/种子内共享乘客输入。阈值与物理参数保持不变。','',
        '## 总体结果','',
        '| 步长 | C1 等待/秒 | C7 等待/秒 | D10 等待/秒 | D10−C1 [95%种子区间]/秒 | 0.25秒收益门槛及其余条件 |',
        '|---|---:|---:|---:|---|---|']
    for dt in [.25,.125]:
        m={r['controller']:r for r in s['means'] if r['week']==r['variant']=='all' and r['dt']==dt}
        c=next(r for r in s['contrasts'] if r['week']==r['variant']=='all' and r['dt']==dt and r['comparison']=='D10-C1' and r['metric']=='awt')
        screen=next(r for r in s['benefit_screens'] if r['dt']==dt)
        lines.append(f"| {dt} | {m['C1']['awt']:.4f} | {m['C7']['awt']:.4f} | {m['D10']['awt']:.4f} | {c['mean']:+.4f} [{c['low']:+.4f}, {c['high']:+.4f}] | {'通过' if screen['passes'] else '未通过'} |")
    lines+=['','## 分周、分背景结果','',
        '| 周 | 背景 | 步长 | D10−C1 [95%种子区间]/秒 | C7−C1/秒 | D10−C7/秒 | D10切换/日 |',
        '|---|---|---|---|---:|---:|---:|']
    for week in ['early','middle','late']:
        for variant in ['low','mixed','high']:
            for dt in [.25,.125]:
                c={r['comparison']:r for r in s['contrasts'] if r['week']==week and r['variant']==variant and r['dt']==dt and r['metric']=='awt'}
                m=next(r for r in s['means'] if r['week']==week and r['variant']==variant and r['dt']==dt and r['controller']=='D10')
                r=c['D10-C1']
                lines.append(f"| {week} | {variant} | {dt} | {r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}] | {c['C7-C1']['mean']:+.4f} | {c['D10-C7']['mean']:+.4f} | {m['switches']:.2f} |")
    lines+=['','## 统计范围与检查','',
        '负差值表示左侧策略等待较短。总体对日期、背景和种子等权；区间以三个种子的条件均值计算 Student t 区间，不把日期或同一输入下的策略当成独立重复。分组区间为描述性结果，未进行多重比较校正。完整 P90、长等待百分比、G/非G等待及对 C7 的区间见 summary.json。P90 汇总为逐次仿真 P90 的均值；长等待差以百分点为单位。','',
        '这三周分别有 250、252、246 个纳入课次，其中 141、143、137 个使用人数假设。绝大部分排课签名已在此前日期出现。采用 9 月 2 日课表快照和 10 月 31 日人数资料，不代表相应日期的实测出勤；11 月日期为计划课表推演。','',
        '六类异常总计：'+str(s['anomaly_totals'])+'。模型与输入运行前后哈希一致；135 份输入在策略和步长之间配对，D10 决策日志在两步长一致。','',
        '运行结果用于现有规则的跨日期稳健性检查。模型负责人审阅、实际容量/速度/门时、人数和乘梯率校准仍会影响对 KKL 的解释。']
    return '\n'.join(lines)+'\n'


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--check',action='store_true');a=ap.parse_args()
    s=analyze(a.folder);report=make_report(s)
    if a.check:
        assert s==load(a.folder/'summary.json')
        assert report==(a.folder/'结果说明.md').read_text(encoding='utf-8')
        print('PASS: 810 cross-week runs, 135 paired inputs, coverage, seed intervals, benefit screens and report verified.')
    else:
        save(a.folder/'summary.json',s)
        (a.folder/'结果说明.md').write_text(report,encoding='utf-8',newline='\n')
        print('Cross-week report saved; screens:',s['benefit_screens'])


if __name__=='__main__':main()
