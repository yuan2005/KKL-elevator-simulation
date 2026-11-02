"""Recompute registry comparisons and paired changes from public aggregate rows."""
import argparse
from pathlib import Path
from statistics import mean
from repair_study_utils import load,save,sha,seed_interval
from study_paths import ROOT
from run_background_sensitivity import KEYS


def analyze(folder):
    p=load(folder/'protocol.json');e=load(folder/'execution.json')
    rows={'original':load(folder/'reused_baselines.json'),'revised':load(folder/'raw.json')}
    checks=load(folder/'input_checks.json')
    assert e['protocol_hash']==sha(folder/'protocol.json')
    assert len(rows['original'])==len(rows['revised'])==p['runs']==e['runs']==90 and len(checks)==15
    history=load(ROOT/'published_results/motion_repair_v1/raw.json')+load(ROOT/'published_results/motion_repair_extra_seeds_v1/raw.json')
    key=lambda r:(r['date'],r['seed'],r['dt'],r['controller'])
    original={key(r):r for r in history if r['variant']=='mixed' and r['dt'] in p['steps']}
    assert {key(r):r for r in rows['original']}==original
    expected={(d,s,dt,c) for d in p['dates'] for s in p['seeds'] for dt in p['steps'] for c in p['controllers']}
    indices={v:{key(r):r for r in rs} for v,rs in rows.items()}
    for index in indices.values():assert set(index)==expected
    for r in checks:
        assert all(r[k] for k in ['background_unchanged','through_unchanged','existing_attendance_unchanged'])
        for v,label in [('original','old'),('revised','new')]:
            for dt in p['steps']:
                for c in p['controllers']:
                    row=indices[v][(r['date'],r['seed'],dt,c)]
                    assert row['input_sha256']==r[label+'_input_sha256'] and row['n']==r[label+'_n']
            ledger=r[label+'_ledger']
            assert sum(ledger.get(layer+'_lift',0) for layer in ['class','background','through'])==r[label+'_n']
            assert ledger['class_intent']==2*r[label+'_attendance_slots']-r[label+'_transfer_links']
        for layer in ['background','through']:
            assert {k:v for k,v in r['old_ledger'].items() if k.startswith(layer+'_')}=={k:v for k,v in r['new_ledger'].items() if k.startswith(layer+'_')}
    assert e['anomaly_totals']=={k:sum(r[k] for r in rows['revised']) for k in KEYS}
    assert e['anomalous_runs']==sum(any(r[k] for k in KEYS) for r in rows['revised'])
    means=[];contrasts=[];interactions=[]
    for dt in p['steps']:
        for v,rs in rows.items():
            for c in p['controllers']:
                group=[r for r in rs if r['dt']==dt and r['controller']==c]
                means.append(dict(registry=v,dt=dt,controller=c,**{m:mean(r[m] for r in group) for m in ['n','awt','p90','lwt','G_awt','other_awt','switches']}))
        for c in ['C7','D10']:
            for metric in ['awt','p90','lwt','G_awt','other_awt']:
                values={}
                for v,index in indices.items():
                    values[v]=[mean(index[(d,s,dt,c)][metric]-index[(d,s,dt,'C1')][metric] for d in p['dates']) for s in p['seeds']]
                    contrasts.append(dict(registry=v,dt=dt,comparison=c+'-C1',metric=metric,**seed_interval(values[v])))
                interactions.append(dict(dt=dt,comparison=c+'-C1',metric=metric,**seed_interval([n-o for n,o in zip(values['revised'],values['original'])])))
    demand={k:mean(r[k] for r in checks) for k in ['old_n','new_n','old_selected_sessions','new_selected_sessions','old_attendance_slots','new_attendance_slots','old_transfer_links','new_transfer_links']}
    return dict(means=means,contrasts=contrasts,contrast_changes=interactions,demand=demand,anomaly_totals=e['anomaly_totals'],new_runs=90,reused_runs=90)


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);ap.add_argument('--check',action='store_true');a=ap.parse_args()
    s=analyze(a.folder)
    if a.check:
        assert s==load(a.folder/'summary.json');print('PASS: 90 revised-room runs, 90 exact reused rows, demand accounting, intervals and contrast changes verified.');return
    save(a.folder/'summary.json',s)
    lines=['# 两间教室补全后的固定策略检查','',
        '仅将已核验的 KKLG110/111 纳入课堂选择，使用独立新版登记表。10 月 26–30 日、种子 4000–4002、mixed 背景、C1/C7/D10、0.25/0.125 秒步长，共新增 90 次；原登记表复用同模型 90 条记录。阈值、物理参数及独立背景/过路流保持不变。','',
        '| 步长 | 登记表 | C1 等待 | C7 等待 | D10 等待 | D10−C1 [95%种子区间] | D10切换/日 |','|---|---|---:|---:|---:|---|---:|']
    for dt in [.25,.125]:
        for v in ['original','revised']:
            m={r['controller']:r for r in s['means'] if r['dt']==dt and r['registry']==v}
            c=next(r for r in s['contrasts'] if r['dt']==dt and r['registry']==v and r['comparison']=='D10-C1' and r['metric']=='awt')
            lines.append(f"| {dt} | {v} | {m['C1']['awt']:.4f} | {m['C7']['awt']:.4f} | {m['D10']['awt']:.4f} | {c['mean']:+.4f} [{c['low']:+.4f}, {c['high']:+.4f}] | {m['D10']['switches']:.2f} |")
    lines+=['','| 步长 | 新登记表造成的 (D10−C1) 变化 [95%种子区间] |','|---|---|']
    for r in s['contrast_changes']:
        if r['comparison']=='D10-C1' and r['metric']=='awt':lines.append(f"| {r['dt']} | {r['mean']:+.4f} [{r['low']:+.4f}, {r['high']:+.4f}] |")
    d=s['demand'];lines+=['',f"这五个开发日的平均可选课次 {d['old_selected_sessions']:.1f}→{d['new_selected_sessions']:.1f}；平均乘梯请求 {d['old_n']:.1f}→{d['new_n']:.1f}；抽取出勤人次 {d['old_attendance_slots']:.1f}→{d['new_attendance_slots']:.1f}；转课连接 {d['old_transfer_links']:.1f}→{d['new_transfer_links']:.1f}。整学期增加 336 个可选课次与本实验五日增量是不同统计范围。",'',
        '各版本内部所有策略和步长使用同一乘客输入。版本之间的独立背景/过路行程及原有课堂出勤抽样一致；新增课堂改变转课连接和部分课堂行程，因此跨版本以日期/种子配对。区间使用三个种子的五日均值。', '',
        '运行检查：'+str(s['anomaly_totals'])+'。完整 P90、长等待、G/非G及 C7 比较保存在 summary.json。', '',
        '本批是 mixed 开发条件的输入修订检查。此前 low/mixed/high 评价的 0.131/0.152 秒收益仍属于旧登记表；尚未形成新版全场景评价。']
    (a.folder/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8',newline='\n')
    print('Room revision report saved; anomaly totals:',s['anomaly_totals'])


if __name__=='__main__':main()
