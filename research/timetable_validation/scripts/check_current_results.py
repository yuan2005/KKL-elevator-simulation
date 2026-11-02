"""Current inventory: historical milestone checks plus the room-registry study."""
from pathlib import Path
import subprocess,sys
from study_paths import ROOT
from repair_study_utils import load,sha


def main():
    scripts=Path(__file__).resolve().parent
    print('Historical milestones through background/semester coverage:',flush=True)
    for name in ['check_all_results.py','check_semester_coverage.py']:
        subprocess.run([sys.executable,str(scripts/name)],check=True)
    folder=ROOT/'published_results/room_revision_repaired_v1'
    subprocess.run([sys.executable,str(scripts/'report_room_revision.py'),str(folder),'--check'],check=True)
    pub=load(folder/'publication.json')
    for name,digest in pub['files'].items():assert sha(folder/name)==digest,name
    for name,digest in pub['analysis_scripts'].items():assert sha(scripts/name)==digest,name
    p=load(folder/'protocol.json')
    for name,digest in p['script_hashes'].items():assert sha(scripts/name)==digest,name
    assert p['revision']==load(ROOT/'published_results/semester_coverage_v1/comparison.json')['revision']
    inventory=load(ROOT/'published_results/archive_inventory_room_revision_v1.json')
    previous=load(ROOT/'published_results/archive_inventory.json')
    assert inventory['batches'][:-1]==previous['batches']
    assert len({b['path'] for b in inventory['batches']})==len(inventory['batches'])
    totals={}
    for batch in inventory['batches']:
        rows=load(ROOT/'published_results'/batch['path']);assert len(rows)==batch['runs']
        totals[batch['model']]=totals.get(batch['model'],0)+len(rows)
    assert sum(totals.values())==inventory['unique_runs']==3720
    print(f'PASS: latest inventory has 3720 unique archived runs: {totals}; reused rows and coverage add none.')


if __name__=='__main__':main()
