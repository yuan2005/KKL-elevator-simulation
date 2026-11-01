"""Recalculate central claims from public per-run metrics; no private data needed."""
import hashlib
import json
from collections import defaultdict
from statistics import mean
from study_paths import ROOT


def load(relative):
    return json.loads((ROOT / 'published_results' / relative).read_text(encoding='utf-8'))


def main():
    batches = {
        's1_s6_original_2026-11-01': 360,
        'linked_pilot_2026-10-26_v1': 120,
        'multiday_2026-10-26_30_v1': 675,
        'adaptive_v1/develop': 225,
        'adaptive_v1/evaluate': 225,
    }
    for folder, expected in batches.items():
        assert len(load(folder + '/raw.json')) == expected
    replication = load('s1_s6_original_2026-11-01/verification.json')
    assert replication['metric_comparisons'] == 6120
    assert replication['differences'] == []
    frozen = load('adaptive_v1/frozen_selection.json')
    for filename, field in [('raw.json', 'development_raw_hash'), ('protocol.json', 'protocol_hash')]:
        actual = hashlib.sha256((ROOT / 'published_results/adaptive_v1/develop' / filename).read_bytes()).hexdigest()
        assert actual == frozen[field]
    develop = load('adaptive_v1/develop/raw.json')
    means = {c: mean(r['awt'] for r in develop if r['controller'] == c) for c in ['C1','C7','D10','D25','D40']}
    assert min(['D10','D25','D40'], key=means.get) == frozen['candidate'] == 'D10'
    assert min(['C1','C7'], key=means.get) == frozen['fixed_baseline'] == 'C1'
    rows = load('adaptive_v1/evaluate/raw.json')
    groups = defaultdict(dict)
    for r in rows:
        key = r['date'], r['variant'], r['seed']
        assert r['controller'] not in groups[key]
        groups[key][r['controller']] = r
    assert len(groups) == 75
    for group in groups.values():
        assert set(group) == {'C1','C7','D10'}
        assert len({r['input_sha256'] for r in group.values()}) == 1
        assert len({r['n'] for r in group.values()}) == 1
        assert all(r[k] == 0 for r in group.values() for k in ['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count'])
    delta = mean(g['D10']['awt'] - g['C1']['awt'] for g in groups.values())
    summary = load('adaptive_v1/evaluation_summary.json')
    assert abs(delta - next(x['delta'] for x in summary['overall'] if x['base'] == 'C1')) < 1e-12
    assert -.25 < delta < 0 and summary['exploratory_success'] is False
    print(f'PASS: {sum(batches.values())} archived runs; 75 paired evaluation inputs; D10-C1={delta:.6f}s; frozen selection hashes match.')
    sensitivity = load('headcount_sensitivity_v1/raw.json')
    reported = load('headcount_sensitivity_v1/summary.json')
    assert len(sensitivity) == 135
    paired = defaultdict(dict)
    for r in sensitivity:
        key = r['date'], r['capacity_fraction'], r['seed']
        assert r['controller'] not in paired[key]
        paired[key][r['controller']] = r
    assert len(paired) == 45
    for group in paired.values():
        assert set(group) == {'C1', 'C7', 'D10'}
        assert len({r['input_sha256'] for r in group.values()}) == 1
        assert len({r['n'] for r in group.values()}) == 1
    for result in reported['comparisons']:
        control = result['comparison'].split('-')[0]
        diffs = [g[control]['awt']-g['C1']['awt'] for key,g in paired.items() if key[1] == result['capacity_fraction']]
        assert abs(mean(diffs)-result['delta']) < 1e-12
    assert all(r[k] == 0 for r in sensitivity for k in ['unserved','not_alighted','chain_availability_violations','late_transfer_lift_count'])
    print('PASS: additional 135 missing-headcount runs; 45 paired inputs; reported deltas match; no service anomalies.')


if __name__ == '__main__':
    main()
