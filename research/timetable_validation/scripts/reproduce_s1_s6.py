"""Reproduce original six E1 reference scenarios without fitting a new map."""
from pathlib import Path
import sys, json, hashlib, csv, platform, time
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
import numpy as np
from scipy import stats

from study_paths import ROOT, REPO, OUTPUT
OUT = OUTPUT / 's1_s6_original_2026-11-01'
sys.path.insert(0, str(REPO))
from experiments import run_scenario, MAP_COMBOS, smoke
from traffic import SCENARIOS, generate_scenario
import config as C

def dump(name, data):
    (OUT/name).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False, default=lambda x:x.item()), encoding='utf-8')

def worker(task):
    key, combo, seed = task
    result = run_scenario((asdict(SCENARIOS[key]), combo, seed, ()))
    result['point'] = key
    return result

def main():
    archived = json.loads((ROOT/'published_results/s1_s6_original_2026-11-01/manifest.json').read_text(encoding='utf-8'))['source_sha256']
    changed = [name for name,digest in archived.items()
               if hashlib.sha256((REPO/name).read_bytes()).hexdigest() != digest]
    if changed:
        raise RuntimeError('Historical reproduction requires the original simulator (checkout 419c1b6 in a separate worktree). '
                           'Changed files: '+', '.join(changed)+'. Use a separately labelled experiment for repaired-model results.')
    OUT.mkdir(parents=True, exist_ok=True)
    resume = '--summarize-existing' in sys.argv
    if (OUT/'raw.json').exists() and not resume:
        raise RuntimeError('Completed output exists; choose a new output folder.')
    started=time.time()
    hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')}
    if resume:
        assert hashes == json.loads((OUT/'manifest.json').read_text(encoding='utf-8'))['source_sha256']
    else:
      dump('manifest.json',dict(purpose='Original placeholder model replication, not calibrated KKL prediction',
         commit='f6c9ca4af440cff4a258237e1214c8dc0266fb50', python=platform.python_version(),
         seeds=list(range(10)),combos=MAP_COMBOS,workers=4,source_sha256=hashes,
         scenarios={k:asdict(v) for k,v in SCENARIOS.items()},
         config={k:v for k,v in vars(C).items() if k.isupper()}))
    smoke()
    tasks=[(k,c,s) for k in SCENARIOS for c in MAP_COMBOS for s in range(10)]
    rows=[]
    if resume:
        rows=json.loads((OUT/'raw.json').read_text(encoding='utf-8'))
    else:
      with ProcessPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(worker,t) for t in tasks]
        for f in as_completed(futures):
            rows.append(f.result())
            if len(rows)%36==0: print(f'Completed {len(rows)}/{len(tasks)}; {time.time()-started:.1f}s',flush=True)
    rows.sort(key=lambda r:(r['point'],r['combo'],r['seed']))
    dump('raw.json',rows)
    # Independent input audit and cross-strategy common-input checks.
    inputs=[]
    for key,sc in SCENARIOS.items():
        for seed in range(10):
            pax,stairs=generate_scenario(sc,seed)
            payload=[(p.id,p.t,p.o,p.d,p.group) for p in pax]
            group=[r for r in rows if r['point']==key and r['seed']==seed]
            assert len(group)==6 and len({r['n'] for r in group})==1
            assert all(r['stairs']==stairs and r['n']==sum(p.t<sc.duration for p in pax) for r in group)
            inputs.append(dict(point=key,seed=seed,generated=len(pax),scored=group[0]['n'],
                outside_metric_window=sum(p.t>=sc.duration for p in pax),stairs=stairs,
                sha256=hashlib.sha256(json.dumps(payload).encode()).hexdigest()))
    dump('input_audit.json',inputs)
    summaries=[]
    for k in SCENARIOS:
        baseline=sorted([r for r in rows if r['point']==k and r['combo']=='C1'],key=lambda r:r['seed'])
        for c in MAP_COMBOS:
            rs=sorted([r for r in rows if r['point']==k and r['combo']==c],key=lambda r:r['seed'])
            waits=np.array([r['awt'] for r in rs]); delta=waits-np.array([r['awt'] for r in baseline])
            ci=lambda x:float(stats.t.ppf(.975,len(x)-1)*np.std(x,ddof=1)/np.sqrt(len(x)))
            summaries.append(dict(point=k,combo=c,awt=float(waits.mean()),awt_ci95_half=ci(waits),
                p90_seed_mean=float(np.mean([r['p90'] for r in rs])),lwt_pct=float(np.mean([r['lwt'] for r in rs])),
                n_seed_mean=float(np.mean([r['n'] for r in rs])),unserved_total=sum(r['unserved'] for r in rs),
                paired_delta_vs_C1=float(delta.mean()),paired_ci95_half=ci(delta)))
    dump('summary.json',summaries)
    with (REPO/'results/e1_raw.csv').open(encoding='utf-8',newline='') as f:
        old={(r['point'],r['combo'],int(r['seed'])):r for r in csv.DictReader(f) if r['point'] in SCENARIOS}
    differences=[]; maxdiff=0.
    for r in rows:
        prior=old[(r['point'],r['combo'],r['seed'])]
        for metric in ['n','awt','p90','lwt','att','unserved','stairs','distance_km','starts','car_hours','lam5','s_in','s_out','s_thru','s_from_ent','s_to_ent','s_high']:
            diff=abs(float(r[metric])-float(prior[metric]));maxdiff=max(maxdiff,diff)
            if not np.isclose(float(r[metric]),float(prior[metric]),rtol=1e-9,atol=1e-9):
                differences.append(dict(point=r['point'],combo=r['combo'],seed=r['seed'],metric=metric,new=r[metric],old=float(prior[metric])))
    assert hashes=={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')}
    verification=dict(runs=len(rows),matched_keys=len(rows),metric_comparisons=len(rows)*17,
         differences=differences,max_absolute_difference=maxdiff,source_unchanged=True,
         unserved_total=sum(r['unserved'] for r in rows),outside_window_unique_inputs=sum(r['outside_metric_window'] for r in inputs),
         execution_mode='summarize_existing' if resume else 'simulate_and_summarize',elapsed_seconds_this_invocation=time.time()-started)
    dump('verification.json',verification)
    print(json.dumps(verification,ensure_ascii=False,default=lambda x:x.item()),flush=True)
    lines=['# S1–S6 原模型复现结果','', '原参数，种子 0–9，每场景六种策略，共 360 次。全部指标先逐种子计算，再对十个种子等权平均。不是实际 KKL 等待时间预测。','', '| 场景 | C0 | C1 | C2 | C3 | C4 | C7 | 均值最低 |','|---|---:|---:|---:|---:|---:|---:|---|']
    for k in SCENARIOS:
        ss=[s for s in summaries if s['point']==k];best=min(ss,key=lambda s:s['awt'])
        lines.append('| '+k+' | '+' | '.join(f"{s['awt']:.2f}" for s in ss)+' | '+best['combo']+' |')
    lines+=['','单位：秒。均值最低不等于已证明对所有策略均显著更优。未重新拟合地图或识别切换阈值。', '', f"与仓库原 e1_raw.csv 比较：{len(rows)} 组、{len(rows)*17} 个数值，容差外差异 {len(differences)} 个；最大绝对差 {maxdiff:.3g}。",f"未上梯计数之和：{verification['unserved_total']}。生成时刻落在指标窗口之外的乘客：{verification['outside_window_unique_inputs']} 人（按 60 组独立场景/种子计，不重复算六策略）。", '', '完整参数、源码哈希、每次运行、输入审计、均值及 95% t 区间见同目录 JSON 文件。区间只表示随机种子变动，不能覆盖建筑参数和需求模型误差；P90 为各次运行的 P90 均值，不是汇总乘客的 P90。', '', '复现保留原楼层 LG3–16、容量 16、速度 2.5m/s 等占位值。没有接入真实课表，也没有因用户观察而更改原模型；最低 LG2 等修订应另开实验配置。']
    (OUT/'结果说明.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')

if __name__=='__main__':main()
