"""Seeded passenger generation with batch arrivals and staircase diversion."""
from dataclasses import dataclass, field

import numpy as np

import config as C
from building import IDX, ENTRANCE_IDX


@dataclass
class Passenger:
    id: int
    t: float
    o: int
    d: int
    group: int = 0
    t_board: float = None
    t_alight: float = None
    car: int = None

    @property
    def dir(self):
        return 1 if self.d > self.o else -1


@dataclass
class Scenario:
    name: str
    lam5: float
    split: tuple
    batch_mean: float = 1.3
    duration: float = 900.0
    profile: str = "uniform"
    peak_at: float = 0.5
    weights: dict = field(default_factory=lambda: C.FLOOR_WEIGHTS_OFFICE)
    entrances: dict = field(default_factory=lambda: C.ENTRANCE_WEIGHTS)
    stairs: bool = True
    thru: float = C.THRU_SHARE


def _pick(rng, weights_by_idx):
    idxs = list(weights_by_idx)
    weights = np.array([weights_by_idx[i] for i in idxs], dtype=float)
    return idxs[rng.choice(len(idxs), p=weights/weights.sum())]


def _weights_idx(weights):
    return {IDX[f]: w for f, w in weights.items() if w > 0 and IDX[f] not in ENTRANCE_IDX}


def _ent_idx(entrances):
    return {IDX[f]: w for f, w in entrances.items() if w > 0}


def _uses_stairs(rng, origin, destination):
    return rng.random() < C.P_STAIRS.get(abs(origin-destination), 0.0)


def _batch_sizes(rng, n_total, mean):
    if n_total <= 0:
        return []
    sizes = []
    while sum(sizes) < n_total:
        sizes.append(int(rng.geometric(1.0/mean)) if mean > 1 else 1)
    sizes[-1] -= sum(sizes)-n_total
    return [size for size in sizes if size > 0]


def _thru_od(rng):
    lg2, ground = IDX["LG2"], IDX["G"]
    return (lg2, ground) if rng.random() < C.THRU_UP else (ground, lg2)


def generate_scenario(sc: Scenario, seed: int, t0: float = 0.0):
    rng = np.random.default_rng(seed)
    weights = _weights_idx(sc.weights)
    entrances = _ent_idx(sc.entrances)

    def draw_times(count):
        if sc.profile == "burst":
            return rng.triangular(0, sc.peak_at*sc.duration, sc.duration, count)
        return rng.uniform(0, sc.duration, count)

    n_batches = rng.poisson(sc.lam5*(1-sc.thru)/300.0*sc.duration/sc.batch_mean)
    times = draw_times(n_batches)
    kinds = rng.choice(3, size=n_batches, p=np.array(sc.split)/sum(sc.split))
    passengers, stairs, group = [], 0, 0
    for arrival, kind in zip(np.sort(times), kinds):
        size = int(rng.geometric(1.0/sc.batch_mean)) if sc.batch_mean > 1 else 1
        if kind == 0:
            origin, destination = _pick(rng, entrances), _pick(rng, weights)
        elif kind == 1:
            origin, destination = _pick(rng, weights), _pick(rng, entrances)
        else:
            origin = _pick(rng, weights)
            destination = origin
            while destination == origin:
                destination = _pick(rng, weights)
        group += 1
        if sc.stairs and _uses_stairs(rng, origin, destination):
            stairs += size
            continue
        for _ in range(size):
            passengers.append(Passenger(0, t0+arrival+rng.uniform(0, 3.0),
                                        origin, destination, group))
    if sc.thru > 0 and passengers:
        n_thru = int(round(len(passengers)*sc.thru/(1-sc.thru)))
        for size in _batch_sizes(rng, n_thru, sc.batch_mean):
            arrival = draw_times(1)[0]
            origin, destination = _thru_od(rng)
            group += 1
            for _ in range(size):
                passengers.append(Passenger(0, t0+arrival+rng.uniform(0, 3.0),
                                            origin, destination, group))
    passengers.sort(key=lambda p: p.t)
    for identifier, passenger in enumerate(passengers):
        passenger.id = identifier
    return passengers, stairs
