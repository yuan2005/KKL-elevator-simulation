import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from repair_study_utils import load


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();s=load(a.folder/'summary.json')
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.hashsalt':'headcount-repaired-v1'})
    fig,axes=plt.subplots(1,2,figsize=(11,4.8),sharey=True)
    for ax,comparison in zip(axes,['D10-C1','C7-C1']):
        for j,dt in enumerate([.25,.125]):
            rs=[next(r for r in s['contrasts'] if r['capacity_fraction']==f and r['dt']==dt and r['comparison']==comparison and r['metric']=='awt') for f in [.4,.6,.8]]
            ax.errorbar([x+(j-.5)*.08 for x in range(3)],[r['mean'] for r in rs],
                yerr=[[r['mean']-r['low'] for r in rs],[r['high']-r['mean'] for r in rs]],fmt='o-',capsize=4,label=f'dt={dt} s')
        ax.axhline(0,color='gray',lw=1);ax.set_xticks(range(3),['40%','60% (reused)','80%'])
        ax.set_xlabel('Assumed headcount / room capacity');ax.set_title(comparison.replace('-',' minus '));ax.grid(axis='y',alpha=.2)
    axes[0].set_ylabel('Mean waiting difference (s)')
    handles,labels=axes[0].get_legend_handles_labels();fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.5,.90),ncol=2)
    fig.suptitle('Missing-headcount sensitivity: 180 new + 90 reused runs')
    fig.text(.5,.045,'Repaired model dd06745; revised rooms; Oct 26-30; mixed background; unchanged controls',ha='center',fontsize=9)
    fig.text(.5,.012,'95% t intervals over 3 seed means; percentages are exploratory assumptions before attendance sampling',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.10,1,.84])
    for ext in ['png','svg']:fig.savefig(a.folder/f'headcount_contrasts.{ext}',dpi=180,metadata={'Date':None} if ext=='svg' else {})
    svg=a.folder/'headcount_contrasts.svg';svg.write_text('\n'.join(l.rstrip() for l in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8',newline='\n')
    plt.close(fig);print('Headcount figures saved.')


if __name__=='__main__':main()
