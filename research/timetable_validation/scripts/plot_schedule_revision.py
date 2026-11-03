"""Fixed-policy comparisons after the date correction, all weeks and backgrounds."""
import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from repair_study_utils import load


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args()
    s=load(a.folder/'summary.json')
    fig,axes=plt.subplots(2,3,figsize=(13,7),sharey=True)
    weeks=['all','early','middle','late'];labels=['All 3 weeks','Sep 21–25','Oct 5–9','Nov 16–20']
    colors=['#2878B5','#D55E00']
    for row,comparison in enumerate(['D10-C1','D10-C7']):
        for col,variant in enumerate(['low','mixed','high']):
            ax=axes[row,col]
            for i,dt in enumerate([.25,.125]):
                rs=[next(r for r in s['contrasts'] if r['version']=='revised' and r['week']==w and r['variant']==variant and r['dt']==dt and r['comparison']==comparison and r['metric']=='awt') for w in weeks]
                xs=[r['mean'] for r in rs];ys=[j+(i-.5)*.18 for j in range(4)]
                ax.errorbar(xs,ys,xerr=[[r['mean']-r['low'] for r in rs],[r['high']-r['mean'] for r in rs]],fmt='o',capsize=3,color=colors[i],label=f'dt = {dt} s')
            ax.axvline(0,color='#555555',linewidth=.8)
            ax.set_title(f'{variant} background · {comparison}')
            ax.set_yticks(range(4),labels)
            ax.set_ylim(3.5,-.5)
            ax.set_xlabel('Mean waiting difference (seconds)')
            ax.grid(axis='x',alpha=.2)
    axes[0,0].legend(fontsize=9,loc='best')
    fig.suptitle('Public tutorial dates corrected · C1 / C7 / D10 unchanged',fontsize=14)
    fig.text(.5,.018,'Negative: D10 waits less. Bars: 95% paired intervals over 3 seed means. Headcounts remain assumed; full 15-date comparison.',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.045,1,.95])
    for ext in ['png','svg']:fig.savefig(a.folder/f'schedule_revision.{ext}',dpi=170)
    plt.close(fig)


if __name__=='__main__':main()
