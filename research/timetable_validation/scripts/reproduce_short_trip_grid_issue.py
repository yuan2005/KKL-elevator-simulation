"""Diagnostic: one passenger, one car, one floor; exits 1 if a trip is incomplete.

This exposes a defect on the historical simulator and checks the repair on the
current simulator. It is not part of unittest discovery; no classroom data is needed.
"""
import argparse
import json
import hashlib
from pathlib import Path
from run_linked_pilot import init_worker
from study_paths import REPO


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
    out=parser.parse_args().output
    if out.exists():raise FileExistsError('Preserve previous evidence')
    init_worker()
    import config as C
    from building import IDX,HEIGHT
    from traffic import Passenger
    from sim import Sim
    from controllers import Static
    from strategies import make_combos
    original=C.DT;results=[]
    try:
        for dt in [.25,.125,.0625]:
            C.DT=dt
            p=Passenger(0,0.,IDX['LG2'],IDX['LG1'],0)
            sim=Sim([p],Static(make_combos()['C0']),10,n_cars=1)
            next_target=sim.next_target;drops=[]
            def observe(car):
                before=car.target
                target=next_target(car)
                if car.load and car.state=='moving' and before in car.car_calls and target!=before and len(drops)<5:
                    drops.append(dict(t=sim.t,x=car.x,v=car.v,direction=car.dir,old_target=before,new_target=target,
                        remaining=(HEIGHT[before]-car.x)*car.dir,stopping_distance=car.v*car.v/(2*C.ACCEL),
                        can_stop=car.can_stop_at(before)))
                return target
            sim.next_target=observe;sim.run()
            results.append(dict(dt=dt,board=p.t_board,alight=p.t_alight,simulation_stop=sim.t,
                remaining_load=sim.cars[0].load,target_drops=drops))
    finally:
        C.DT=original
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(dict(case='Single C0 car: LG2 to LG1, 3.6 m, passenger at t=0, 10 s generation horizon plus original 900 s drain',
        source_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in REPO.glob('*.py')},
        results=results),indent=2),encoding='utf-8')
    print(json.dumps(results,indent=2))
    return 1 if any(r['alight'] is None for r in results) else 0


if __name__=='__main__':raise SystemExit(main())
