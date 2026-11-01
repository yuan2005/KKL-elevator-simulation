"""Post-hoc aggregate diagnosis of saved decision logs; never retunes the rule.

Run against local evaluate/runs, which is intentionally not published.
Decision fractions cover logged update instants (after warmup, before drain),
not independent windows, passenger shares, or causal switching effects.
"""
import argparse
import json
from pathlib import Path
from statistics import mean


def diagnose(folder):
    groups = {}
    for path in sorted(folder.glob('*.json')):
        run = json.loads(path.read_text(encoding='utf-8'))
        decisions = run['logs']['D10']['decisions']
        assert decisions
        rows = {r['controller']: r for r in run['rows']}
        assert set(rows) == {'C1', 'C7', 'D10'}
        assert len({r['input_sha256'] for r in rows.values()}) == 1
        entry_pass = lambda d: d['n'] >= 10 and d['from_entry'] >= .70 and d['high_endpoint'] <= .10
        item = {
            'C1_awt': rows['C1']['awt'], 'C7_awt': rows['C7']['awt'],
            'D10_awt': rows['D10']['awt'], 'switches': rows['D10']['switches'],
            'C7_decision_fraction': mean(d['choice'] == 'C7' for d in decisions),
            'entry_conditions_pass_fraction': mean(entry_pass(d) for d in decisions),
            'sparse_fraction': mean(d['n'] < 10 for d in decisions),
            'entry_share_below_70_fraction': mean(d['from_entry'] < .70 for d in decisions),
            'high_endpoint_above_10_fraction': mean(d['high_endpoint'] > .10 for d in decisions),
            'entry_pass_but_C1_fraction': mean(entry_pass(d) and d['choice'] == 'C1' for d in decisions),
        }
        groups.setdefault(run['variant'], []).append(item)
    if not groups:
        raise ValueError('No saved evaluation runs found')
    result = {}
    for variant, runs in sorted(groups.items()):
        result[variant] = {k: mean(r[k] for r in runs) for k in runs[0]}
        result[variant]['runs'] = len(runs)
        result[variant]['D10_minus_C1'] = result[variant]['D10_awt'] - result[variant]['C1_awt']
        result[variant]['D10_minus_C7'] = result[variant]['D10_awt'] - result[variant]['C7_awt']
    return {
        'status': 'post_hoc_diagnostic_not_new_validation',
        'unit': 'equal-run means; AWT seconds; fractions of logged decisions',
        'caveat': 'Condition failures overlap. Logs do not identify causal delay costs or optimal switching times. No threshold changed.',
        'by_variant': result,
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runs', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError('Preserve prior diagnosis; select a new output')
    result = diagnose(args.runs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps(result, indent=2))
