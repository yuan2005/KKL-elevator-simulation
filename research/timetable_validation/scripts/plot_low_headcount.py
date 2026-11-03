"""Report-ready low-background sensitivity contrasts, all weeks retained."""
import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from repair_study_utils import load


def main():
    ap=argparse.ArgumentParser();ap.add_argument('folder',type=Path);a=ap.parse_args();s=load(a.folder/'summary.json')
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False,'svg.hashsalt':'low-headcount-v1'})
    fig,axes=plt.subplots(2,3,figsize=(13,8),sharex=True,sharey='row')
    for j,week in enumerate(['early','middle','late']):
        for i,comparison in enumerate(['D10-C1','D10-C7']):
            ax=axes[i,j]
            for k,dt in enumerate([.25,.125]):
                rs=[next(r for r in s['contrasts'] if r['week']==week and r['capacity_fraction']==f and r['dt']==dt and r['comparison']==comparison and r['metric']=='awt') for f in [.4,.6,.8]]
                ax.errorbar([x+(k-.5)*.08 for x in range(3)],[r['mean'] for r in rs],
                    yerr=[[r['mean']-r['low'] for r in rs],[r['high']-r['mean'] for r in rs]],fmt='o-',capsize=4,label=f'dt={dt} s')
            ax.axhline(0,color='gray',lw=1);ax.set_xticks(range(3),['40%','60%','80%']);ax.grid(axis='y',alpha=.2)
            if i==0:ax.set_title(week.capitalize())
            else:ax.set_xlabel('Assumed headcount / room capacity')
            if j==0:ax.set_ylabel(comparison.replace('-',' minus ')+' wait (s)')
    handles,labels=axes[0,0].get_legend_handles_labels();fig.legend(handles,labels,loc='upper center',bbox_to_anchor=(.5,.93),ncol=2)
    fig.suptitle('Low-background headcount sensitivity: 540 new + 270 reused runs',fontsize=15)
    fig.text(.5,.045,'Model dd06745; revised rooms; 3 teaching weeks; unchanged C1/C7/D10; 60% baselines reused',ha='center',fontsize=9)
    fig.text(.5,.018,'95% t intervals over 3 seed means (5 dates per panel); exploratory assumptions before attendance sampling',ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.08,1,.87])
    for ext in ['png','svg']:fig.savefig(a.folder/f'low_headcount_contrasts.{ext}',dpi=180,metadata={'Date':None} if ext=='svg' else {})
    svg=a.folder/'low_headcount_contrasts.svg';svg.write_text('\n'.join(l.rstrip() for l in svg.read_text(encoding='utf-8').splitlines())+'\n',encoding='utf-8',newline='\n')
    plt.close(fig);print('Low-headcount figures saved.')


if __name__=='__main__':main()
