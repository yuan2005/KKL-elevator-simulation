"""Current public inventory through low-background missing-headcount sensitivity."""
from pathlib import Path
import subprocess,sys
from study_paths import ROOT
from repair_study_utils import load,sha


def main():
    scripts=Path(__file__).resolve().parent
    print('Historical milestone checks follow; final line gives the latest inventory.',flush=True)
    subprocess.run([sys.executable,str(scripts/'check_headcount_results.py')],check=True)
    folder=ROOT/'published_results/low_headcount_repaired_v1'
    subprocess.run([sys.executable,str(scripts/'report_low_headcount.py'),str(folder),'--check'],check=True)
    publication=load(folder/'publication.json');p=load(folder/'protocol.json')
    assert {f.name for f in folder.iterdir()}==set(publication['files'])|{'publication.json'}
    for name,digest in publication['files'].items():assert sha(folder/name)==digest,name
    for name,digest in (publication['analysis_scripts']|p['script_hashes']).items():assert sha(scripts/name)==digest,name
    assert p['source_hashes']==load(ROOT/'published_results/motion_repair_v1/protocol.json')['source_hashes']
    inventory=load(ROOT/'published_results/archive_inventory_low_headcount_v1.json')
    previous=load(ROOT/'published_results/archive_inventory_headcount_v1.json')
    assert inventory['batches'][:-1]==previous['batches']
    assert inventory['batches'][-1]==dict(path='low_headcount_repaired_v1/raw.json',runs=540,model='repaired')
    assert len({b['path'] for b in inventory['batches']})==len(inventory['batches'])
    totals={}
    for batch in inventory['batches']:
        assert len(load(ROOT/'published_results'/batch['path']))==batch['runs']
        totals[batch['model']]=totals.get(batch['model'],0)+batch['runs']
    assert sum(totals.values())==inventory['unique_runs']==5250 and totals==dict(original=2145,repaired=3105)
    print(f'PASS: latest inventory has 5250 unique archived runs: {totals}; reused rows and analyses add none.')


if __name__=='__main__':main()
