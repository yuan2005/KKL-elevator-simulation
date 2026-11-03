"""Standalone figures with model, sample and uncertainty labels."""
import argparse
from pathlib import Path
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from repair_study_utils import load


def main():
    ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['grids','scenarios','evaluation']);ap.add_argument('folder',type=Path)
    args=ap.parse_args();data=load(args.folder/'summary.json')
    plt.rcParams.update({'font.size':10,'axes.spines.top':False,'axes.spines.right':False})
    colors={'C0':'#8c8c8c','C1':'#2878b5','C2':'#59a14f','C3':'#9467bd','C4':'#e69f00','C7':'#d55e00','D10':'#009e73'}
    if args.phase=='grids':
        fig,axes=plt.subplots(1,2,figsize=(11,4.6));steps=[.25,.125,.0625];xx=np.arange(3)
        for c in ['C1','C7','D10']:
            vals=[next(r['awt'] for r in data['means'] if r['dt']==dt and r['controller']==c) for dt in steps]
            axes[0].plot(xx,vals,'o-',label=c,color=colors[c])
        axes[0].set_ylabel('Mean waiting time (s)');axes[0].legend()
        cs=[next(r for r in data['contrasts'] if r['dt']==dt and r['variant']=='all' and r['comparison']=='D10-C1') for dt in steps]
        axes[1].errorbar(xx,[r['mean'] for r in cs],yerr=[r['high']-r['mean'] for r in cs],fmt='o-',capsize=5,color=colors['D10'])
        axes[1].axhline(0,color='gray',lw=1);axes[1].set_ylabel('D10 - C1 waiting difference (s)')
        for ax in axes:
            ax.set_xticks(xx,[str(s) for s in steps]);ax.set_xlabel('Time step (s)');ax.grid(axis='y',alpha=.2)
        fig.suptitle('Repaired motion: 405 paired development runs')
        footer='Model dd06745 | 5 dates x 3 backgrounds x 3 seeds | right: 95% seed-level t intervals; no convergence claim'
    elif args.phase=='scenarios':
        fig,ax=plt.subplots(figsize=(11,4.6));scenarios=[f'S{i}' for i in range(1,7)];xx=np.arange(6);width=.13
        for j,c in enumerate(['C0','C1','C2','C3','C4','C7']):
            vals=[next(r['awt'] for r in data['summaries'] if r['scenario']==s and r['controller']==c) for s in scenarios]
            ax.bar(xx+(j-2.5)*width,vals,width,label=c,color=colors[c])
        ax.set_xticks(xx,scenarios);ax.set_ylabel('Mean waiting time (s)');ax.legend(ncol=6);ax.grid(axis='y',alpha=.2)
        fig.suptitle('Repaired S1-S6 baselines: 360 runs')
        footer='Model dd06745 | original LG3-16 placeholder settings | dt=0.25 s | 10 seeds; means do not prove superiority'
    else:
        fig,ax=plt.subplots(figsize=(10,4.6));variants=['all','low','mixed','high'];xx=np.arange(4)
        for j,dt in enumerate([.25,.125]):
            cs=[next(r for r in data['contrasts'] if r['dt']==dt and r['variant']==v and r['comparison']=='D10-C1') for v in variants]
            ax.errorbar(xx+(j-.5)*.14,[r['mean'] for r in cs],yerr=[r['high']-r['mean'] for r in cs],fmt='o',capsize=5,label=f'dt={dt} s')
        ax.axhline(0,color='gray',lw=1);ax.axhline(-.25,color='#aa3333',ls='--',label='Predeclared 0.25 s saving screen')
        ax.set_xticks(xx,['Equal-weight overall','Low-floor background','Mixed background','High-floor background'])
        ax.set_ylabel('D10 - C1 waiting difference (s)');ax.grid(axis='y',alpha=.2);ax.legend(loc='lower right')
        fig.suptitle('Frozen D10 re-evaluation: 450 runs on previously used dates')
        footer='Model dd06745 | 5 dates x 3 backgrounds x 5 seeds | 95% seed-level t intervals; not real-data uncertainty'
    fig.text(.5,.02,footer,ha='center',fontsize=9)
    fig.tight_layout(rect=[0,.06,1,.94]);fig.savefig(args.folder/f'{args.phase}_repaired.png',dpi=180);plt.close(fig)
    print(args.folder/f'{args.phase}_repaired.png')


if __name__=='__main__':main()
