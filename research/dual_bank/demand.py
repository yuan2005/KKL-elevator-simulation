"""Sustained demand inputs for the independent-bank experiment."""
import numpy as np

import config as C
from research.dual_bank.timetable import (
    BATCHES, CLASS_WINDOW_MINUTES, LEVELS, LUNCH_ENTRANCES, RATES, SPLITS,
)
from traffic import Scenario, generate_scenario

REGIMES = ("S1", "S2", "S3", "S5", "S6", "lunch_S3", "lunch_S5")
WARMUP = 600.
MEASURE = 1200.
BOUNDARIES = (450, 527, 543, 587, 603, 690, 707, 723, 810, 1127, 1143, 1350)


def measurement_seconds(regime):
    return 7200. if regime == "S6" else MEASURE


def sustained(level, regime, seed):
    """Uniform sustained mixtures, not copies of short triangular class waves."""
    if regime not in REGIMES or level not in LEVELS:
        raise ValueError("Unknown demand condition")
    rng = np.random.default_rng(seed)
    lunch = regime.startswith("lunch_")
    base = regime.removeprefix("lunch_")
    components = [base] + (["lunch_extra"] if lunch else [])
    daily = float(rng.uniform(.85, 1.15))
    passengers, records = [], []
    group_offset = 0
    for component in components:
        rate = RATES[component]
        if component in ("S1", "S2", "S3"):
            rate *= 10/CLASS_WINDOW_MINUTES
        rate *= LEVELS[level]*daily*float(rng.uniform(.9, 1.1))
        scenario = Scenario(
            name=component, lam5=rate, split=SPLITS[component],
            batch_mean=BATCHES[component], duration=WARMUP+measurement_seconds(regime),
            profile="uniform",
            weights=dict(C.FLOOR_WEIGHTS_CLASS if component in ("S1", "S2", "S3")
                         else C.FLOOR_WEIGHTS_OFFICE),
            entrances=dict(LUNCH_ENTRANCES if lunch else C.ENTRANCE_WEIGHTS),
            stairs=True, thru=C.THRU_SHARE,
        )
        generated, stairs = generate_scenario(scenario, int(rng.integers(0, 2**63-1)))
        for passenger in generated:
            passenger.group += group_offset
        group_offset = max((p.group for p in generated), default=group_offset)+1
        passengers.extend(generated)
        records.append(dict(component=component, lam5=rate, stairs=stairs,
                            generated=len(generated)))
    passengers.sort(key=lambda p: p.t)
    for identifier, passenger in enumerate(passengers):
        passenger.id = identifier
    end = max(WARMUP+measurement_seconds(regime),
              max((p.t for p in passengers), default=0.)+1e-6)
    return passengers, end, records
