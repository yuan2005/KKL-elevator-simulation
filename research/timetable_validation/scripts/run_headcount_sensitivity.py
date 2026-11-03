"""One-factor missing-enrolment sensitivity; fixed mixed background and D10.

Existing selection rules and algorithms are unchanged. Only eligible sessions
without an enrolment snapshot get a different room-capacity multiplier.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

from timetable_demand import DATA, read, save, sessions_for_day, select_sessions
from study_paths import OUTPUT, REPO
from run_linked_pilot import init_worker
from run_adaptive import worker as adaptive_worker, BASE

DATES = [f'2026-10-{d}' for d in range(26, 31)]
SEEDS = [6000, 6001, 6002]
FRACTIONS = [.4, .6, .8]
CONTROLLERS = ['C1', 'C7', 'D10']


def with_missing_fraction(selected, fraction):
    if not 0 <= fraction <= 1:
        raise ValueError('Capacity fraction must be in [0, 1]')
    result = []
    for s in selected:
        item = dict(s)
        if s['headcount_snapshot'] is None:
            if s['capacity'] is None:
                raise ValueError('Eligible missing-count session needs capacity')
            item['headcount'] = round(s['capacity'] * fraction)
            item['headcount_basis'] = f'assumed_{fraction * 100:g}pct_room_capacity'
            item['headcount_exceeds_capacity'] = item['headcount'] > s['capacity']
        result.append(item)
    return result


def worker(job):
    day, fraction, seed, sessions, begin, end = job
    result = adaptive_worker((day, 'mixed', seed, sessions, begin, end, CONTROLLERS))
    result['capacity_fraction'] = fraction
    for row in result['rows']:
        row['capacity_fraction'] = fraction
    return result


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=OUTPUT / 'headcount_sensitivity_v1')
    args = parser.parse_args()
    out = args.output.resolve()
    if out.exists():
        raise FileExistsError('Use a new output directory; prior protocol/results are immutable')
    events = read('semester1_kkl_dated_candidates.json')
    rooms = {r['room']: r for r in read('room_registry.json')}
    coverage, public_coverage, jobs = {}, [], []
    for day in DATES:
        sessions = sessions_for_day(events, day)
        selected, excluded = select_sessions(sessions, rooms, 'capacity_assumption')
        begin = (min(s['start_min'] for s in sessions) - 15) * 60
        end = (max(s['end_min'] for s in sessions) + 15) * 60
        coverage[day] = dict(candidates=len(sessions), selected=selected, excluded=excluded, window=[begin, end])
        for fraction in FRACTIONS:
            adjusted = with_missing_fraction(selected, fraction)
            assert [s['session_id'] for s in adjusted] == [s['session_id'] for s in selected]
            assert all(a == b for a, b in zip(adjusted, selected) if b['headcount_snapshot'] is not None)
            if fraction == .6:
                assert adjusted == selected, 'Reference inputs must exactly match the existing selection'
            public_coverage.append(dict(date=day, capacity_fraction=fraction, candidates=len(sessions),
                selected=len(selected), excluded=len(excluded),
                known_count_sessions=sum(s['headcount_snapshot'] is not None for s in selected),
                estimated_count_sessions=sum(s['headcount_snapshot'] is None for s in selected),
                known_headcount_sum=sum(s['headcount'] for s in adjusted if s['headcount_snapshot'] is not None),
                estimated_headcount_sum=sum(s['headcount'] for s in adjusted if s['headcount_snapshot'] is None),
                window=[begin, end]))
            for seed in SEEDS:
                jobs.append((day, fraction, seed, adjusted, begin, end))
    source_hashes = {p.name: sha(p) for p in REPO.glob('*.py')}
    scripts = [Path(__file__).with_name(n) for n in ['run_headcount_sensitivity.py', 'run_adaptive.py',
        'run_linked_pilot.py', 'timetable_demand.py', 'linked_demand.py', 'adaptive_policy.py', 'study_paths.py']]
    protocol = dict(created_utc=datetime.now(timezone.utc).isoformat(), status='predeclared_exploratory_sensitivity',
        dates=DATES, seeds=SEEDS, fractions=FRACTIONS, controllers=CONTROLLERS, inputs=len(jobs), runs=len(jobs)*3,
        changed='Only missing headcounts among already eligible sessions: round(capacity * fraction)',
        fixed=dict(BASE, background_variant='mixed', high_share=.5, d10_threshold=.1, timestep_seconds=.25),
        comparison='Identical generated passengers across controls within each date/fraction/seed; common seeds across fractions, different demand allowed',
        primary='Equal-date/seed mean AWT; paired D10-C1 and C7-C1',
        secondary=['P90 per-run mean','LWT >60s percent','G and non-G AWT','unserved','incomplete','chain conflicts','late transfers','switches'],
        uncertainty='Three seed-level mean paired differences across five fixed dates; t interval is Monte Carlo uncertainty only',
        decision='No retuning or replacement of prior held-out success screen; report ranking and sign stability, including failures',
        scope='Known explored dates, one fixed mixed-background setting; not low/high robustness, missing-class recovery, or real-world calibration',
        source_hashes=source_hashes, data_hashes={n:sha(DATA/n) for n in ['semester1_kkl_dated_candidates.json','room_registry.json']},
        script_hashes={p.name:sha(p) for p in scripts})
    out.mkdir(parents=True)
    (out/'runs').mkdir(); (out/'scripts').mkdir()
    for p in scripts:
        shutil.copyfile(p, out/'scripts'/p.name)
    save(out/'protocol.json', protocol)
    save(out/'coverage_private.json', coverage)
    save(out/'coverage_summary.json', public_coverage)
    protocol_hash = sha(out/'protocol.json')
    print(f'Protocol saved before simulation: {len(jobs)} inputs / {len(jobs)*3} runs', flush=True)
    started=time.time(); rows=[]; audits=[]
    with ProcessPoolExecutor(max_workers=4, initializer=init_worker) as pool:
        futures=[pool.submit(worker, job) for job in jobs]
        for i, f in enumerate(as_completed(futures), 1):
            r=f.result(); rows.extend(r['rows'])
            save(out/'runs'/f"{r['date']}_{r['capacity_fraction']:.1f}_{r['seed']}.json",r)
            audits.append({k:v for k,v in r.items() if k not in ['rows','logs']})
            if i%5==0:
                print(f'{i}/{len(jobs)} inputs; {3*i}/135 runs; {time.time()-started:.0f}s',flush=True)
    rows.sort(key=lambda r:(r['date'],r['capacity_fraction'],r['seed'],r['controller']))
    save(out/'raw.json',rows); save(out/'input_audits_private.json',audits)
    assert source_hashes == {p.name:sha(p) for p in REPO.glob('*.py')}
    assert protocol_hash == sha(out/'protocol.json')
    save(out/'execution.json',dict(runs=len(rows), inputs=len(audits), seconds=time.time()-started,
        protocol_hash=protocol_hash, original_source_unchanged=True, protocol_unchanged=True))
    print('FINISHED',len(rows),flush=True)


if __name__ == '__main__':
    main()
