"""Bounded paired validation of repaired motion; no parameter or rule selection."""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from study_paths import REPO
from timetable_demand import save
from run_linked_pilot import init_worker
from run_adaptive import worker as adaptive_worker

STEPS = [.25, .125, .0625]
SEEDS = [4000]  # First archived development seed, chosen before repaired runs.
ANOMALIES = ['unserved', 'not_alighted', 'chain_availability_violations',
             'late_transfer_lift_count', 'event_time_violations']


def load(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def source_hashes(): return {p.name: sha(p) for p in REPO.glob('*.py')}


def worker(job):
    args, prior_path = job
    prior = load(prior_path)
    import config as C
    from sim import Sim
    saved_dt, saved_run = C.DT, Sim.run
    clock_checks = []

    def checked_run(sim):
        result = saved_run(sim)
        clock_checks.append(sum(
            (p.t_board is not None and p.t_board < p.t) or
            (p.t_alight is not None and
             (p.t_board is None or p.t_alight < p.t_board or p.t_alight > sim.t + 1e-7))
            for p in sim.pax))
        return result

    Sim.run = checked_run
    runs = []
    try:
        for dt in STEPS:
            C.DT = dt
            clock_checks.clear()
            result = adaptive_worker(args)
            assert len(clock_checks) == len(result['rows'])
            for row, count in zip(result['rows'], clock_checks):
                row.update(dt=dt, event_time_violations=count)
            result['dt'] = dt
            runs.append(result)
    finally:
        C.DT, Sim.run = saved_dt, saved_run
    all_runs = prior['runs'] + runs
    assert len({r['input_sha256'] for run in all_runs for r in run['rows']}) == 1
    canonical = lambda v: json.loads(json.dumps(v))
    assert all(run['input_audit'] == all_runs[0]['input_audit'] and
               canonical(run['logs']) == all_runs[0]['logs'] for run in all_runs)
    return dict(date=args[0], variant=args[1], seed=args[2], runs=runs,
                same_inputs_audits_decisions_as_original=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--prior', required=True, type=Path)
    ap.add_argument('--output', required=True, type=Path)
    ap.add_argument('--seeds', nargs='+', type=int, default=SEEDS)
    args = ap.parse_args()
    prior, out = args.prior.resolve(), args.output.resolve()
    if out.exists(): raise FileExistsError('Use a new output directory; preserve evidence')
    old = load(prior/'protocol.json')
    coverage = load(prior/'coverage_private.json')
    hashes = source_hashes()
    seeds = sorted(set(args.seeds))
    assert seeds and set(seeds) <= set(old['seeds'])
    for name, digest in old['original_source_hashes'].items():
        if name not in ['building.py', 'sim.py']: assert hashes[name] == digest, name
    scripts = [Path(__file__).with_name(n) for n in ['run_repair_validation.py',
        'run_adaptive.py', 'run_linked_pilot.py', 'linked_demand.py',
        'timetable_demand.py', 'adaptive_policy.py', 'study_paths.py']]
    for p in scripts:
        if p.name != 'run_repair_validation.py': assert sha(p) == old['script_hashes'][p.name]
    jobs, prior_files = [], {}
    for day in old['dates']:
        record = coverage[day]
        for variant in old['variants']:
            for seed in seeds:
                file = prior/'runs'/f'{day}_{variant}_{seed}.json'
                prior_files[file.name] = sha(file)
                jobs.append(((day, variant, seed, record['selected'], *record['window'], old['controllers']), file))
    protocol = dict(created_utc=datetime.now(timezone.utc).isoformat(),
        design='Candidate motion repair validation on fixed explored inputs, not policy selection or a new holdout',
        dates=old['dates'], seeds=seeds, steps=STEPS, controllers=old['controllers'],
        base=old['base'], variants=old['variants'], frozen=old['frozen'],
        inputs=len(jobs), runs=len(jobs)*len(STEPS)*len(old['controllers']),
        checks=['same passenger hashes, demand audits and D10 decisions as original',
                'report all service, chain and event-time anomalies without exclusions',
                'report grid shifts and paired policy contrasts without choosing a favourable grid'],
        interpretation='Fixed original development seeds: repair validation, not new policy selection or independent evaluation. Seed-level uncertainty is conditional on the fixed dates/backgrounds; no convergence proof.',
        source_hashes=hashes, original_source_hashes=old['original_source_hashes'],
        script_hashes={p.name:sha(p) for p in scripts},
        prior_protocol_hash=sha(prior/'protocol.json'), prior_raw_hash=sha(prior/'raw.json'),
        prior_coverage_hash=sha(prior/'coverage_private.json'), prior_run_hashes=prior_files)
    out.mkdir(parents=True); (out/'runs').mkdir(); (out/'scripts').mkdir(); (out/'model').mkdir()
    for p in scripts: shutil.copyfile(p, out/'scripts'/p.name)
    for p in REPO.glob('*.py'): shutil.copyfile(p, out/'model'/p.name)
    save(out/'protocol.json', protocol)
    protocol_hash = sha(out/'protocol.json')
    print(f'Frozen repair validation: {len(jobs)} inputs / {protocol["runs"]} runs', flush=True)
    start = time.time(); rows = []; checks = []
    with ProcessPoolExecutor(max_workers=4, initializer=init_worker) as pool:
        futures = [pool.submit(worker, job) for job in jobs]
        for i, future in enumerate(as_completed(futures), 1):
            result = future.result()
            save(out/'runs'/f'{result["date"]}_{result["variant"]}_{result["seed"]}.json', result)
            rows.extend(row for run in result['runs'] for row in run['rows'])
            checks.append({k:v for k,v in result.items() if k != 'runs'})
            print(f'{i}/{len(jobs)} inputs; {9*i}/{protocol["runs"]} runs; {time.time()-start:.0f}s', flush=True)
    rows.sort(key=lambda r:(r['date'], r['variant'], r['seed'], r['dt'], r['controller']))
    checks.sort(key=lambda r:(r['date'],r['variant'],r['seed']))
    save(out/'raw.json', rows); save(out/'pair_checks.json', checks)
    assert hashes == source_hashes()
    assert protocol_hash == sha(out/'protocol.json')
    assert protocol['script_hashes'] == {p.name:sha(p) for p in scripts}
    assert sha(prior/'protocol.json') == protocol['prior_protocol_hash']
    assert sha(prior/'raw.json') == protocol['prior_raw_hash']
    assert sha(prior/'coverage_private.json') == protocol['prior_coverage_hash']
    assert prior_files == {n:sha(prior/'runs'/n) for n in prior_files}
    save(out/'execution.json', dict(runs=len(rows), inputs=len(checks), seconds=time.time()-start,
        protocol_hash=protocol_hash, source_unchanged_during_run=True, prior_archive_unchanged=True,
        anomaly_totals={k:sum(r[k] for r in rows) for k in ANOMALIES},
        anomalous_runs=sum(any(r[k] for k in ANOMALIES) for r in rows)))
    print('FINISHED',len(rows),flush=True)


if __name__ == '__main__': main()
