"""Export paired cross-week contrasts for report use."""
import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from repair_study_utils import load


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args()
    s=load(a.folder/'summary.json')
    groups=[(w,v) for w in ['early','middle','late'] for v in ['low','mixed','high']]
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.hashsalt':'crossweek-v1'})
    fig,axes=plt.subplots(1,2,figsize=(12,7),sharey=True)
    for ax,comparison in zip(axes,['D10-C1','D10-C7']):
        for j,dt in enumerate([.25,.125]):
            rs=[next(r for r in s['contrasts'] if (r['week'],r['variant'])==g and r['dt']==dt and r['comparison']==comparison and r['metric']=='awt') for g in groups]
            ax.errorbar([r['mean'] for r in rs],np.arange(len(groups))+(j-.5)*.18,
                xerr=[[r['mean']-r['low'] for r in rs],[r['high']-r['mean'] for r in rs]],
                fmt='o',capsize=3,label=f'dt = {dt} s',color=['#2878b5','#d55e00'][j])
        ax.axvline(0,color='#555555',lw=1)
        if comparison=='D10-C1':ax.axvline(-.25,color='#8b3333',ls='--',lw=1,label='0.25 s mean-saving target')
        for boundary in [2.5,5.5]:ax.axhline(boundary,color='#999999',lw=.7,alpha=.4)
        ax.grid(axis='x',alpha=.2);ax.set_title(comparison.replace('-',' minus '))
        ax.set_xlabel('Mean waiting difference (s); negative favours D10')
    axes[0].set_yticks(range(len(groups)),[f'{w.capitalize()} / {v}' for w,v in groups]);axes[0].invert_yaxis()
    fig.suptitle('Unchanged D10 across three teaching weeks: 810 runs',fontsize=15)
    handles,labels=axes[0].get_legend_handles_labels()
    fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.5,.935),ncol=3,fontsize=9)
    fig.text(.5,.045,'Early: Sep 21-25 | Middle: Oct 5-9 | Late: Nov 16-20 | Model dd06745; revised classroom input',ha='center',fontsize=9)
    fig.text(.5,.018,'95% t intervals over 3 seed means (5 dates per group); descriptive comparisons under assumed demand',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.08,1,.875])
    for extension in ['png','svg']:
        fig.savefig(a.folder/f'crossweek_contrasts.{extension}',dpi=180,metadata={'Date':None} if extension=='svg' else {})
    svg=a.folder/'crossweek_contrasts.svg'
    svg.write_text('\n'.join(line.rstrip() for line in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8',newline='\n')
    plt.close(fig)
    print('Cross-week PNG/SVG saved.')


if __name__=='__main__':main()
