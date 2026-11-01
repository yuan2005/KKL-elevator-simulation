"""Check and report a bounded repair archive; no CI from a single seed."""
import argparse
from collections import defaultdict
import hashlib
import json
from pathlib import Path
from statistics import mean

from study_paths import ROOT
from timetable_demand import save
from run_repair_validation import ANOMALIES


def load(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);folder=ap.parse_args().folder
    protocol=load(folder/'protocol.json');rows=load(folder/'raw.json')
    execution=load(folder/'execution.json');checks=load(folder/'pair_checks.json')
    assert sha(folder/'protocol.json')==execution['protocol_hash']
    assert len(rows)==protocol['runs']==135 and len(checks)==protocol['inputs']==15
    expected={(d,v,s) for d in protocol['dates'] for v in protocol['variants'] for s in protocol['seeds']}
    assert {(r['date'],r['variant'],r['seed']) for r in checks}==expected
    assert all(c['same_inputs_audits_decisions_as_original'] for c in checks)
    groups=defaultdict(dict)
    for row in rows:
        key=row['date'],row['variant'],row['seed'];pair=row['dt'],row['controller']
        assert pair not in groups[key];groups[key][pair]=row
    assert set(groups)==expected
    for group in groups.values():
        assert set(group)=={(dt,c) for dt in protocol['steps'] for c in protocol['controllers']}
        assert len({r['input_sha256'] for r in group.values()})==1
        assert len({r['n'] for r in group.values()})==1
    totals={k:sum(r[k] for r in rows) for k in ANOMALIES}
    assert totals==execution['anomaly_totals']
    assert sum(any(r[k] for k in ANOMALIES) for r in rows)==execution['anomalous_runs']
    # Public historical metrics, restricted to the exact same demand fingerprints.
    original=load(ROOT/'published_results/timestep_sensitivity_v1/raw.json')+load(ROOT/'published_results/timestep_third_grid_v2/raw.json')
    before={}
    for r in original:
        key=r['date'],r['variant'],r['seed']
        if key in groups:
            assert r['input_sha256']==groups[key][(r['dt'],r['controller'])]['input_sha256']
            before[key+(r['dt'],r['controller'])]=r
    assert len(before)==len(rows)
    means=[];contrasts=[];shifts=[];anomalies=[]
    for dt in protocol['steps']:
        subset=[r for r in rows if r['dt']==dt]
        old=[r for r in before.values() if r['dt']==dt]
        anomalies.append(dict(dt=dt,old_anomalous_runs=sum(any(r[k] for k in ANOMALIES[:-1]) for r in old),
            repaired_anomalous_runs=sum(any(r[k] for k in ANOMALIES) for r in subset),runs=len(subset)))
        for c in protocol['controllers']:
            part=[r for r in subset if r['controller']==c]
            means.append(dict(dt=dt,controller=c,awt=mean(r['awt'] for r in part),
                original_awt=mean(before[(r['date'],r['variant'],r['seed'],dt,c)]['awt'] for r in part)))
        for variant in ['all']+list(protocol['variants']):
            selected=[g for key,g in groups.items() if variant=='all' or key[1]==variant]
            for c in ['C7','D10']:
                contrasts.append(dict(dt=dt,variant=variant,comparison=c+'-C1',
                    mean=mean(g[(dt,c)]['awt']-g[(dt,'C1')]['awt'] for g in selected)))
    for coarse,fine in zip(protocol['steps'],protocol['steps'][1:]):
        for c in protocol['controllers']:
            diffs=[g[(fine,c)]['awt']-g[(coarse,c)]['awt'] for g in groups.values()]
            shifts.append(dict(coarse=coarse,fine=fine,quantity=c,
                mean=mean(diffs),mean_absolute=mean(abs(v) for v in diffs),max_absolute=max(abs(v) for v in diffs)))
        for c in ['C7','D10']:
            diffs=[(g[(fine,c)]['awt']-g[(fine,'C1')]['awt'])-(g[(coarse,c)]['awt']-g[(coarse,'C1')]['awt']) for g in groups.values()]
            shifts.append(dict(coarse=coarse,fine=fine,quantity=c+'-C1',
                mean=mean(diffs),mean_absolute=mean(abs(v) for v in diffs),max_absolute=max(abs(v) for v in diffs)))
    minimal=load(folder/'minimal_reproducer.json')
    assert minimal['source_hashes']==protocol['source_hashes']
    assert all(r['alight'] is not None and r['alight']<=r['simulation_stop'] and not r['target_drops'] for r in minimal['results'])
    summary=dict(means=means,contrasts=contrasts,grid_shifts=shifts,anomaly_totals=totals,
        paired_original_anomalies=anomalies,raw_hash=sha(folder/'raw.json'),
        limitations='One original development seed. No Monte Carlo CI, independent policy evaluation or numerical convergence claim.')
    save(folder/'summary.json',summary)
    lines=['# 运动学修复：固定输入复验','',
        '修复前已独立复现相邻楼层无法送达的问题。本次修复统一可停靠判断和分段加速/巡航/减速积分，并记录步内到达时间；提前结束检查等待已安排的下梯事件。', '',
        '实验先冻结方案：5 个已研究日期 × 3 种背景 × 原开发种子 4000 × 3 个步长 × C1/C7/D10，共 135 次。保留原课表选择、人流假设和阈值，不重新选择规则。输入指纹、人流审计和 D10 决策日志与原始版本一致。','',
        '## 服务与事件检查','',
        '| 步长（秒） | 相同输入的原版异常运行 | 修复后异常运行 | 每版运行数 |',
        '|---|---:|---:|---:|']
    lines += [f"| {r['dt']} | {r['old_anomalous_runs']} | {r['repaired_anomalous_runs']} | {r['runs']} |" for r in anomalies]
    lines += ['', '原版异常口径：未上梯、未下梯、人流链冲突或转课迟到；修复版额外检查时间戳先后及是否超过仿真结束时间。原版没有同口径时间戳统计，因此不声称两个检查集合完全相同。', '',
        '修复后异常总数：'+', '.join(f'{k}={v}' for k,v in totals.items())+'。', '',
        '## 修复版平均等待（秒）','',
        '| 步长 | C1 | C7 | D10 | D10−C1 |','|---|---:|---:|---:|---:|']
    for dt in protocol['steps']:
        vals={r['controller']:r['awt'] for r in means if r['dt']==dt}
        lines.append(f"| {dt} | {vals['C1']:.4f} | {vals['C7']:.4f} | {vals['D10']:.4f} | {vals['D10']-vals['C1']:+.4f} |")
    lines += ['', '均值对日期和背景等权，非实测频率加权。仅一个随机种子，不给出蒙特卡洛置信区间。', '',
        '## 步长细化差异','',
        '| 粗→细 | 指标 | 有符号均值变化 | 每输入绝对变化均值 | 最大绝对变化 |',
        '|---|---|---:|---:|---:|']
    lines += [f"| {r['coarse']}→{r['fine']} | {r['quantity']} | {r['mean']:+.4f} | {r['mean_absolute']:.4f} | {r['max_absolute']:.4f} |" for r in shifts]
    lines += ['', '## 解释边界与下一步','',
        '- 本次是已知故障的有限修复验证；三个步长、一个种子不能证明所有工况正确或数值收敛。',
        '- 仍有离散的呼梯登记、重新分配、关门和控制器事件，不能将解析运动时间等同于整个仿真无步长误差。',
        '- 原来的 0.140 秒收益属于旧模拟器的历史结果；本次开发输入复验不能替代原评价条件重跑，更不能证明实际 KKL 改善。',
        '- 下一步先由模型负责人审阅修复，然后在独立目录重跑相关 S1–S6/固定规则基线与原评价条件。策略修改应另设开发和未使用评价条件。',
        '- 现有参数未实测校准；不因修复后异常消失而扩大真实性结论。', '']
    (folder/'结果说明.md').write_text('\n'.join(lines),encoding='utf-8')
    print(json.dumps(dict(runs=len(rows),anomalies=totals,means=means),ensure_ascii=False,indent=2))


if __name__=='__main__':main()
