import unittest
from audit_semester_coverage import analyze, calendar_flags, load
from study_paths import ROOT
from test_timetable_demand import event


class SemesterCoverageTests(unittest.TestCase):
    def test_calendar_boundaries_and_weekend_holiday_overlap(self):
        c=load(ROOT/'semester_calendar_review.json')
        self.assertEqual(calendar_flags('2026-11-30',c),[])
        self.assertIn('after_general_teaching',calendar_flags('2026-12-01',c))
        self.assertEqual(set(calendar_flags('2026-09-26',c)),{'weekend','general_holiday'})
        self.assertIn('general_reading_week',calendar_flags('2026-10-17',c))
        self.assertEqual(calendar_flags('2026-10-26',c),[])

    def test_multireason_exclusion_partitions_and_missing_days(self):
        a=event(1,'09:00','10:50');b=event(2,'10:00','11:50',number=2)
        a['business_headcount_snapshot']=None
        c=event(3,'09:00','10:50',number=3,room='KK201');c['date']='2026-10-28'
        rules=[dict(source_row=i,career='UG',offering_department='TEST') for i in [1,2,3]]
        result,_=analyze([a,b,c],{'KK101':dict(floor=None,capacity=None),'KK201':dict(floor='2',capacity=100)},rules,load(ROOT/'semester_calendar_review.json'))
        t=result['totals']
        self.assertEqual((t['candidate_events'],t['selected_sessions'],t['excluded_sessions']),(3,1,2))
        self.assertEqual(sum(t['exclusion_combinations'].values()),2)
        self.assertGreater(sum(t['exclusion_reasons'].values()),2)
        self.assertEqual(result['daily'][1]['candidate_events'],0)

    def test_holiday_screen_preserves_legacy_selection(self):
        e=event(1,'09:00','10:50');e['date']='2026-10-19';e['business_headcount_snapshot']=None
        result,private=analyze([e],{'KK101':dict(floor='1',capacity=100)},
            [dict(source_row=1,career='TPG',offering_department='TEST')],load(ROOT/'semester_calendar_review.json'))
        self.assertEqual(result['totals']['selected_sessions'],1)
        self.assertEqual(result['totals']['ordinary_selected_sessions'],0)
        self.assertEqual(private['days'][0]['selected'][0]['headcount'],60)
        self.assertEqual(result['totals']['selected_careers'],{'TPG':1})


if __name__=='__main__':unittest.main()
