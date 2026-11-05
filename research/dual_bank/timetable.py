"""Daily demand schedule, with absolute seconds measured from midnight."""
from dataclasses import asdict, dataclass

import numpy as np

import config as C
from traffic import Passenger, Scenario, generate_scenario

LEVELS = {"low": 0.5, "medium": 1.0, "high": 1.5}
DAY_END = 24 * 3600.
LUNCH_START, LUNCH_END = 690, 810
LUNCH_ENTRANCES = {"LG2": 0.6, "G": 0.4}
CLASS_MARGIN_MINUTES = 3
CLASS_WINDOW_MINUTES = 10 + 2*CLASS_MARGIN_MINUTES
CLASS_WINDOWS = (
    (530-CLASS_MARGIN_MINUTES, 540+CLASS_MARGIN_MINUTES, "S1"),
    *((hour*60+50-CLASS_MARGIN_MINUTES, (hour+1)*60+CLASS_MARGIN_MINUTES, "S3")
      for hour in range(9, 18)),
    (1130-CLASS_MARGIN_MINUTES, 1140+CLASS_MARGIN_MINUTES, "S2"),
)
# Rates are assumed pre-stairs attempts per five minutes, not observed counts.
RATES = {"S1": 70., "S2": 80., "S3": 90., "S5": 15., "S6": 3.,
         "lunch_extra": 120.}
SPLITS = {"S1": (0.80, 0.05, 0.15), "S2": (0.05, 0.85, 0.10),
          "S3": (0.35, 0.35, 0.30), "S5": (0.30, 0.30, 0.40),
          "S6": (0.45, 0.45, 0.10), "lunch_extra": (0.25, 0.55, 0.20)}
BATCHES = {"S1": 1.3, "S2": 1.6, "S3": 1.5, "S5": 1.2, "S6": 1.1,
           "lunch_extra": 2.0}


@dataclass(frozen=True)
class Segment:
    start_minute: int
    end_minute: int
    regime: str
    lunch: bool

    @property
    def base_rate(self) -> float:
        rate = RATES[self.regime]
        return rate*10/CLASS_WINDOW_MINUTES if self.regime in ("S1", "S2", "S3") else rate

    @property
    def total_rate(self) -> float:
        return self.base_rate + (RATES["lunch_extra"] if self.lunch else 0.)


def regime_at(minute: float) -> str:
    if not 0 <= minute < 1440:
        raise ValueError("Clock must be in [00:00,24:00)")
    if minute < 450 or minute >= 1350:
        return "S6"
    for start, end, regime in CLASS_WINDOWS:
        if start <= minute < end:
            return regime
    return "S5"


def schedule() -> list[Segment]:
    cuts = {0, 450, LUNCH_START, LUNCH_END, 1350, 1440}
    for start, end, _ in CLASS_WINDOWS:
        cuts.update({start, end})
    ordered = sorted(cuts)
    return [Segment(start, end, regime_at(start), LUNCH_START <= start < LUNCH_END)
            for start, end in zip(ordered, ordered[1:])]


def clock_regime(seconds: float) -> str:
    minute = min(max(seconds/60., 0.), 1440.-1e-8)
    regime = regime_at(minute)
    return f"lunch_{regime}" if LUNCH_START <= minute < LUNCH_END else regime


def make_plan(level: str = "medium") -> dict:
    if level not in LEVELS:
        raise ValueError(f"Unknown load level: {level}")
    return dict(
        clock="Seconds from midnight; 00:00=0.",
        generation_end=DAY_END, level=level, load_multiplier=LEVELS[level],
        lessons=[dict(start_minute=h*60, end_minute=h*60+50) for h in range(9, 19)],
        class_arrival_margin_minutes=CLASS_MARGIN_MINUTES,
        class_volume_normalization="Preserve each former10min class wave's expected volume; "
                                   "multiply its lam5 by10/16 over the expanded16min window.",
        lunch=dict(start_minute=LUNCH_START, end_minute=LUNCH_END,
                   entrance_weights=LUNCH_ENTRANCES.copy()),
        other_entrance_weights=dict(C.ENTRANCE_WEIGHTS),
        pre_stairs_lam5=RATES.copy(), split={k: list(v) for k, v in SPLITS.items()},
        batch_mean=BATCHES.copy(),
        actual_uncertainty=dict(daily_rate_multiplier=[0.85, 1.15],
                                component_rate_multiplier=[0.90, 1.10],
                                class_peak_shift_seconds=[-60., 60.]),
        segments=[dict(asdict(s), base_lam5=s.base_rate*LEVELS[level],
                       total_lam5=s.total_rate*LEVELS[level]) for s in schedule()],
        interpretation="Rates, splits, batch sizes and uncertainty ranges are assumptions; "
                       "lunch60/40 is entrance choice in both directions, not total traffic share.",
    )


def generate(level: str, seed: int, nominal: bool = False) -> tuple[list[Passenger], float, list[dict]]:
    plan = make_plan(level)
    rng = np.random.default_rng(seed)
    daily = 1. if nominal else float(rng.uniform(.85, 1.15))
    passengers: list[Passenger] = []
    records: list[dict] = []
    next_group = 0
    for index, segment in enumerate(schedule()):
        components = [segment.regime] + (["lunch_extra"] if segment.lunch else [])
        for component_index, component in enumerate(components):
            component_seed = int(rng.integers(0, 2**63-1))
            factor = 1. if nominal else float(rng.uniform(.90, 1.10))
            shift = 0. if nominal else float(rng.uniform(-60., 60.))
            class_wave = component in ("S1", "S2", "S3")
            peak = {"S1": .7, "S2": .15, "S3": .4}.get(component, .5)
            duration = (segment.end_minute-segment.start_minute)*60.
            if class_wave:
                # Extend support without moving the nominal absolute peak or adding students.
                peak = (CLASS_MARGIN_MINUTES+10*peak)/CLASS_WINDOW_MINUTES
                peak = min(.9, max(.1, peak+shift/duration))
            rate = segment.base_rate if component == segment.regime else RATES[component]
            scenario = Scenario(
                name=component, lam5=rate*plan["load_multiplier"]*daily*factor,
                split=SPLITS[component], batch_mean=BATCHES[component], duration=duration,
                profile="burst" if class_wave else "uniform", peak_at=peak,
                weights=dict(C.FLOOR_WEIGHTS_CLASS if class_wave else C.FLOOR_WEIGHTS_OFFICE),
                entrances=dict(LUNCH_ENTRANCES if segment.lunch else C.ENTRANCE_WEIGHTS),
                stairs=True, thru=C.THRU_SHARE,
            )
            generated, stairs = generate_scenario(
                scenario, component_seed, t0=segment.start_minute*60.)
            offset = next_group
            for p in generated:
                p.group += offset
                next_group = max(next_group, p.group)
            passengers.extend(generated)
            records.append(dict(segment=index, component_index=component_index,
                                start=segment.start_minute*60., end=segment.end_minute*60.,
                                component=component, lam5=scenario.lam5, peak_at=peak,
                                entrances=scenario.entrances, component_seed=component_seed,
                                stairs=stairs, generated=len(generated)))
    passengers.sort(key=lambda p: p.t)
    for identifier, passenger in enumerate(passengers):
        passenger.id = identifier
    generation_end = max(DAY_END, passengers[-1].t+1e-6 if passengers else DAY_END)
    return passengers, generation_end, records

