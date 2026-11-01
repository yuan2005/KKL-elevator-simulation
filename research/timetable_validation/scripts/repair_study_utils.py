"""Audit helpers for separately archived repaired-model experiments."""
import hashlib
import json
from pathlib import Path
import shutil
from statistics import mean, stdev
import numpy as np
from scipy.stats import t

from study_paths import REPO


def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def sha(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def save(path, value):
    def scalar(v):
        if isinstance(v,np.generic): return v.item()
        raise TypeError(f'Unsupported JSON value: {type(v).__name__}')
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False,default=scalar)+'\n', encoding='utf-8')
def sources(): return {p.name:sha(p) for p in REPO.glob('*.py')}


def seed_interval(values):
    values=list(values); n=len(values); m=mean(values)
    half=float(t.ppf(.975,n-1))*stdev(values)/(n**.5) if n>1 else None
    return dict(mean=m, low=m-half if half is not None else None,
                high=m+half if half is not None else None, n_seeds=n)


def freeze(out, protocol, scripts):
    out.mkdir(parents=True, exist_ok=False)
    (out/'runs').mkdir(); (out/'scripts').mkdir(); (out/'model').mkdir()
    protocol['source_hashes']=sources()
    protocol['script_hashes']={p.name:sha(p) for p in scripts}
    for p in scripts: shutil.copyfile(p,out/'scripts'/p.name)
    for p in REPO.glob('*.py'): shutil.copyfile(p,out/'model'/p.name)
    save(out/'protocol.json',protocol)
    return sha(out/'protocol.json')


def verify_frozen(out, protocol, digest, scripts):
    assert sources()==protocol['source_hashes']
    assert sha(out/'protocol.json')==digest
    assert {p.name:sha(p) for p in scripts}==protocol['script_hashes']


def event_violations(sim):
    return sum((p.t_board is not None and p.t_board<p.t) or
        (p.t_alight is not None and (p.t_board is None or p.t_alight<p.t_board or p.t_alight>sim.t+1e-7))
        for p in sim.pax)


def instrument_motion(sim):
    import config as C
    from building import HEIGHT
    original=sim.move
    counts=dict(physical_constraint_violations=0, move_steps=0)
    def checked(car):
        x,v,d=car.x,car.v,car.dir
        reachable=car.can_stop_at(car.target)
        original(car)
        counts['move_steps']+=1
        bad=(not reachable or car.v < -1e-7 or car.v > C.RATED_SPEED+1e-7 or
             abs(car.v-v)>C.ACCEL*C.DT+1e-6 or
             abs(car.x-x)>C.RATED_SPEED*C.DT+1e-6 or (car.x-x)*d < -1e-6 or
             car.x < min(HEIGHT)-1e-6 or car.x>max(HEIGHT)+1e-6)
        counts['physical_constraint_violations']+=int(bad)
    sim.move=checked
    return counts
