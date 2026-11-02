import unittest
from report_low_headcount import summarize_scopes,METRICS


class LowHeadcountTests(unittest.TestCase):
    def test_week_and_fraction_contrasts_use_paired_seed_means(self):
        p=dict(dates=['a','b'],weeks=[dict(role='early',dates=['a']),dict(role='late',dates=['b'])],
            steps=[.25],fractions=[.4,.6,.8],controllers=['C1','C7','D10'],seeds=[1,2,3])
        rows=[];checks=[]
        for d in p['dates']:
            for seed in p['seeds']:
                for f in p['fractions']:
                    interaction=(f-.6)*10*seed
                    value={'C1':10,'C7':8,'D10':9+(2 if d=='b' else 0)+interaction}
                    checks.append(dict(date=d,seed=seed,capacity_fraction=f,n=100,attendance_slots=100,
                        transfer_links=10,known_headcount_sum=40,assumed_headcount_sum=60))
                    for c in p['controllers']:
                        rows.append(dict(date=d,seed=seed,capacity_fraction=f,dt=.25,controller=c,n=100,switches=0,
                            **{metric:value[c] for metric in METRICS}))
        index={(r['date'],r['seed'],r['capacity_fraction'],r['dt'],r['controller']):r for r in rows}
        s=summarize_scopes(index,rows,checks,p)
        pick=lambda rows,week,f:next(r for r in rows if r['week']==week and r['capacity_fraction']==f and r['comparison']=='D10-C1' and r['metric']=='awt')
        self.assertEqual(pick(s['contrasts'],'all',.6)['mean'],0)
        self.assertEqual(pick(s['contrasts'],'early',.6)['mean'],-1)
        self.assertEqual(pick(s['contrasts'],'late',.6)['mean'],1)
        for week in ['all','early','late']:
            r=pick(s['contrast_changes'],week,.8)
            for actual,expected in zip(r['seed_means'],[2,4,6]):self.assertAlmostEqual(actual,expected)
            self.assertEqual(r['n_seeds'],3)
            self.assertAlmostEqual(r['mean'],4)


if __name__=='__main__':unittest.main()
