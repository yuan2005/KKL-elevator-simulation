"""Verify the public archive and count each simulation batch once."""
import json
from pathlib import Path
import subprocess
import sys

from study_paths import ROOT
from repair_study_utils import sha


def main():
    scripts = Path(__file__).resolve().parent
    for filename in ['check_published_results.py', 'check_repaired_results.py', 'check_diagnosis_candidate.py']:
        print(f'Checking archived milestone: {filename}', flush=True)
        subprocess.run([sys.executable, str(scripts / filename)], check=True)
    folder = ROOT / 'published_results/background_rate_repaired_v1'
    subprocess.run([sys.executable, str(scripts / 'report_background_sensitivity.py'), str(folder), '--check'], check=True)
    manifest = json.loads((ROOT / 'published_results/archive_inventory.json').read_text(encoding='utf-8'))
    totals = {}
    for batch in manifest['batches']:
        rows = json.loads((ROOT / 'published_results' / batch['path']).read_text(encoding='utf-8'))
        assert len(rows) == batch['runs'], batch['path']
        totals[batch['model']] = totals.get(batch['model'], 0) + len(rows)
    assert len({b['path'] for b in manifest['batches']}) == len(manifest['batches'])
    assert sum(totals.values()) == manifest['unique_runs'] == 3630
    publication = json.loads((folder / 'publication.json').read_text(encoding='utf-8'))
    for name, digest in publication['files'].items():
        assert sha(folder / name) == digest, name
    for name, digest in publication['analysis_scripts'].items():
        assert sha(scripts / name) == digest, name
    print(f'PASS: current archive contains {sum(totals.values())} unique runs: {totals}.')
    print('Reused rows, combined analyses and incomplete archival attempts add no runs.')


if __name__ == '__main__':
    main()
