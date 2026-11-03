"""Plot released weekly coverage without private classroom records."""
import argparse
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from audit_semester_coverage import load
from study_paths import ROOT


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--output',required=True);a=ap.parse_args()
    folder=ROOT/'published_results/semester_coverage_v1'
    new=load(folder/'revised_summary.json');old=load(folder/'original_summary.json')
    rows=[r for r in new['weekly'] if r['merged_sessions']]
    oldweeks={r['week_start']:r for r in old['weekly']}
    x=np.arange(len(rows));fig,ax=plt.subplots(figsize=(12,5))
    known=np.array([r['selected_known_headcount'] for r in rows])
    assumed=np.array([r['selected_assumed_headcount'] for r in rows])
    ax.bar(x,known,label='Selected: headcount snapshot',color='#277da8')
    ax.bar(x,assumed,bottom=known,label='Selected: capacity assumption',color='#f3b44f')
    ax.bar(x,[r['excluded_sessions'] for r in rows],bottom=known+assumed,label='Excluded pending information',color='#bfc5ca')
    ax.plot(x,[oldweeks[r['week_start']]['selected_sessions'] for r in rows],color='#8d3268',marker='o',ms=3,label='Original registry selected')
    ax.set_xticks(x,[r['week_start'][5:] for r in rows],rotation=45,ha='right')
    ax.set(xlabel='Monday of source week (2026; final 01-04 is 2027)',ylabel='Merged class meetings',
        title='KKL timetable coverage after two verified room-floor corrections',ylim=(0,max(r['merged_sessions'] for r in rows)*1.26))
    ax.legend(fontsize=8,ncol=2);ax.grid(axis='y',alpha=.2)
    fig.text(.5,.015,'Calendar exceptions retained for review | class meetings, not unique students | no simulations added',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.04,1,1]);fig.savefig(a.output,dpi=160);plt.close(fig)


if __name__=='__main__':main()
