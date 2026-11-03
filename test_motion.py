"""Physical invariants and small trip regressions, independent of timetable data."""
import unittest
import config as C
from building import Car, IDX, HEIGHT, flight_time
from traffic import Passenger
from controllers import Static
from strategies import make_combos
from sim import Sim


class MotionTests(unittest.TestCase):
    def setUp(self):self.saved_dt=C.DT
    def tearDown(self):C.DT=self.saved_dt

    def test_one_floor_trip_completes_on_each_grid(self):
        for dt in [.25,.125,.0625,.03125]:
            with self.subTest(dt=dt):
                C.DT=dt;p=Passenger(0,0.,IDX['LG2'],IDX['LG1'],0)
                sim=Sim([p],Static(make_combos()['C0']),10,n_cars=1).run()
                self.assertIsNotNone(p.t_alight)
                self.assertLess(p.t_alight,20)
                self.assertGreaterEqual(sim.t,p.t_alight)

    def test_reject_new_call_inside_physical_stopping_distance(self):
        C.DT=.25
        car=Car(0,IDX['LG2']);car.state='moving';car.dir=1;car.v=2.
        car.x=HEIGHT[IDX['LG1']]-1.8
        self.assertFalse(car.can_stop_at(IDX['LG1']))
        car.x=HEIGHT[IDX['LG1']]-2.0
        self.assertTrue(car.can_stop_at(IDX['LG1']))

    def test_no_target_drop_or_constraint_violation_on_up_down_trips(self):
        for dt in [.25,.125,.0625]:
            for origin,dest in [('LG2','LG1'),('LG1','LG2'),('G','3'),('10','G'),('LG2','16')]:
                with self.subTest(dt=dt,origin=origin,destination=dest):
                    C.DT=dt;p=Passenger(0,0.,IDX[origin],IDX[dest],0)
                    sim=Sim([p],Static(make_combos()['C0']),1,n_cars=1)
                    sim.cars[0]=Car(0,IDX[origin]);car=sim.cars[0]
                    move_begin=[];old_move=sim.move
                    def traced_move(c):
                        if not move_begin:move_begin.append(sim.t)
                        x,v=c.x,c.v
                        old_move(c)
                        self.assertGreaterEqual(c.v,-1e-9)
                        self.assertLessEqual(c.v,C.RATED_SPEED+1e-9)
                        self.assertLessEqual(abs(c.v-v),C.ACCEL*dt+1e-6)
                        self.assertLessEqual(abs(c.x-x),C.RATED_SPEED*dt+1e-6)
                        if c.state=='moving':
                            self.assertEqual(c.target,IDX[dest])
                            self.assertTrue(c.can_stop_at(IDX[dest]))
                    sim.move=traced_move;sim.run()
                    self.assertIsNotNone(p.t_alight)
                    self.assertAlmostEqual(car.x,HEIGHT[IDX[dest]])
                    travel=p.t_alight-C.T_DOOR_OPEN-C.T_PASSENGER-move_begin[0]
                    self.assertAlmostEqual(travel,flight_time(abs(HEIGHT[IDX[dest]]-HEIGHT[IDX[origin]])),places=6)

    def test_new_requests_only_retarget_when_physically_reachable(self):
        for distance,expected in [(1.8,'3'),(2.1,'LG1')]:
            with self.subTest(distance=distance):
                sim=Sim([],Static(make_combos()['C0']),1,n_cars=1)
                sim.set_combo(make_combos()['C0']);car=sim.cars[0]
                car.state='moving';car.dir=1;car.v=2.
                car.x=HEIGHT[IDX['LG1']]-distance
                car.target=IDX['3'];car.car_calls={IDX['3']}
                car.assigned={(IDX['LG1'],1,None)}
                self.assertEqual(sim.next_target(car),IDX[expected])

    def test_many_calls_complete_with_physical_constraints(self):
        import random
        rng=random.Random(912)
        trips=[(i*.7,*rng.sample(range(len(HEIGHT)),2)) for i in range(100)]
        for dt in [.25,.0625]:
            for name in ['C0','C1','C7']:
                with self.subTest(dt=dt,controller=name):
                    C.DT=dt
                    pax=[Passenger(i,t,o,d,i) for i,(t,o,d) in enumerate(trips)]
                    sim=Sim(pax,Static(make_combos()[name]),80)
                    old_move=sim.move
                    def traced_move(c):
                        x,v=c.x,c.v
                        self.assertTrue(c.can_stop_at(c.target))
                        old_move(c)
                        self.assertLessEqual(abs(c.v-v),C.ACCEL*dt+1e-6)
                        self.assertLessEqual(abs(c.x-x),C.RATED_SPEED*dt+1e-6)
                    sim.move=traced_move;sim.run()
                    self.assertTrue(all(p.t_alight is not None for p in pax))
                    self.assertTrue(all(p.t <= p.t_board < p.t_alight <= sim.t for p in pax))


if __name__=='__main__':unittest.main()
