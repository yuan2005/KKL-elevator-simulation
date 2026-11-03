from pathlib import Path
import subprocess,sys
from study_paths import ROOT
from repair_study_utils import load,sha

def main():
    scripts=Path(__file__).resolve().parent
    subprocess.run([sys.executable,str(scripts/'check_low_headcount_results.py')],check=True)
    folder=ROOT/'published_results/schedule_repaired_v1'
    subprocess.run([sys.executable,str(scripts/'report_schedule_revision.py'),str(folder),'--check'],check=True)
    p=load(folder/'protocol.json');publication=load(folder/'publication.json')
    assert {f.name for f in folder.iterdir()}==set(publication['files'])|{'publication.json'}
    for n,h in publication['files'].items():assert sha(folder/n)==h,n
    for n,h in (publication['analysis_scripts']|p['script_hashes']).items():assert sha(scripts/n)==h,n
    inventory=load(ROOT/'published_results/archive_inventory_schedule_v1.json')
    previous=load(ROOT/'published_results/archive_inventory_low_headcount_v1.json')
    assert inventory['batches'][:-1]==previous['batches']
    assert inventory['batches'][-1]==dict(path='schedule_repaired_v1/raw.json',runs=486,model='repaired')
    totals={}
    for batch in inventory['batches']:
        assert len(load(ROOT/'published_results'/batch['path']))==batch['runs']
        totals[batch['model']]=totals.get(batch['model'],0)+batch['runs']
    assert sum(totals.values())==inventory['unique_runs']==5736 and totals==dict(original=2145,repaired=3591)
    print('PASS: 5736 unique archived runs (2145 original, 3591 repaired).')

if __name__=='__main__':main()
