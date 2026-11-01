import unittest
from timetable_demand import sessions_for_day,select_sessions,generate

def event(i,start,end,number=1,room='KK101'):
    return dict(date='2026-10-26',class_number=number,room=room,start_time=start,end_time=end,
                event_id=str(i),source_rows=[i],flags=[],course_code='TEST',class_section=str(number),
                business_headcount_snapshot=20)

class DemandTests(unittest.TestCase):
    def test_hourly_merge_keeps_evidence_but_not_later_session(self):
        ss=sessions_for_day([event(1,'09:00','09:50'),event(2,'10:00','10:50'),event(3,'14:00','14:50')],'2026-10-26')
        self.assertEqual(len(ss),2)
        self.assertEqual(ss[0]['source_events'],['1','2'])
        self.assertEqual(ss[0]['end_min'],650)

    def test_overlap_not_hidden_by_missing_headcount(self):
        a=event(1,'09:00','10:50');b=event(2,'10:00','11:50',number=2)
        b['business_headcount_snapshot']=None
        ss=sessions_for_day([a,b],'2026-10-26')
        selected,excluded=select_sessions(ss,{'KK101':dict(floor='1',capacity=120)},'known_headcounts')
        self.assertEqual(selected,[])
        self.assertTrue(all('unresolved_shared_room' in e['reasons'] for e in excluded))

    def test_reproducible_subsets_evening_window_and_flow_conservation(self):
        ss=sessions_for_day([event(1,'19:00','21:50'),event(2,'09:00','10:50',number=2,room='KK201')],'2026-10-26')
        selected,_=select_sessions(ss,{'KK101':dict(floor='1',capacity=120),'KK201':dict(floor='2',capacity=120)},'known_headcounts')
        base=generate(selected,2026,8*3600,23*3600)
        extended=generate(selected,2026,8*3600,23*3600,True)
        self.assertEqual(base,generate(selected,2026,8*3600,23*3600))
        clean=lambda ps:[{k:v for k,v in p.items() if k!='id'} for p in ps]
        self.assertEqual(clean(base[0]),clean([p for p in extended[0] if p['layer']=='class']))
        one=generate(selected[-1:],2026,8*3600,23*3600)
        self.assertEqual(clean(one[0]),clean([p for p in base[0] if p['session_id']==selected[-1]['session_id']]))
        self.assertTrue(any(p['t_seconds']>11*3600 for p in base[0]))
        self.assertEqual(base[1]['class_intent'],base[1]['class_stairs']+base[1]['class_lift'])
        self.assertTrue(all(0<=p['t_seconds']<15*3600 and p['origin']!=p['destination'] for p in extended[0]))

if __name__=='__main__':unittest.main()
