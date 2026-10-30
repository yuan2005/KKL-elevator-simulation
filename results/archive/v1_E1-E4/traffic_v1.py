"""人流生成：参数模式（S1–S6 场景）和课表模式（全天）。

同一个随机种子生成的乘客列表与调度策略无关，所以不同策略可以用"相同随机数"公平比较。
"""
import csv
from dataclasses import dataclass, field
import numpy as np

from . import config as C
from .building import IDX, ENTRANCE_IDX, N_FLOORS


@dataclass
class Passenger:
    id: int
    t: float            # 到达电梯厅的时刻
    o: int              # 出发楼层
    d: int              # 目的楼层
    group: int = 0      # 同一批（结伴）到达的编号
    t_board: float = None
    t_alight: float = None
    car: int = None

    @property
    def dir(self):
        return 1 if self.d > self.o else -1


@dataclass
class Scenario:
    name: str
    lam5: float                 # 每 5 分钟想乘梯的人数（楼梯分流之前）
    split: tuple                # (进楼, 离楼, 楼层间) 比例
    batch_mean: float = 1.3     # 平均每批人数
    duration: float = 900.0     # 产生乘客的时长（秒）
    profile: str = "uniform"    # uniform / burst（课前、课后的集中到达）
    peak_at: float = 0.5        # burst 时到达高峰在时段中的位置（0–1）
    weights: dict = field(default_factory=lambda: C.FLOOR_WEIGHTS_OFFICE)
    entrances: dict = field(default_factory=lambda: C.ENTRANCE_WEIGHTS)
    stairs: bool = True


# S1–S6 预设 [全部为占位值，待实测人流计数后校准]
SCENARIOS = {
    "S1": Scenario("S1 课前进楼", 70, (0.80, 0.05, 0.15), 1.3, 900, "burst", 0.7, C.FLOOR_WEIGHTS_CLASS),
    "S2": Scenario("S2 课后离楼", 80, (0.05, 0.85, 0.10), 1.6, 900, "burst", 0.15, C.FLOOR_WEIGHTS_CLASS),
    "S3": Scenario("S3 课间换课", 90, (0.35, 0.35, 0.30), 1.5, 900, "burst", 0.4, C.FLOOR_WEIGHTS_CLASS),
    "S4": Scenario("S4 饭点", 55, (0.40, 0.40, 0.20), 2.0, 1800, "uniform"),
    "S5": Scenario("S5 平时", 15, (0.30, 0.30, 0.40), 1.2, 1800, "uniform"),
    "S6": Scenario("S6 节假日/夜间", 3, (0.45, 0.45, 0.10), 1.1, 3600, "uniform"),
}


def _pick(rng, weights_by_idx):
    idxs = list(weights_by_idx)
    w = np.array([weights_by_idx[i] for i in idxs], dtype=float)
    return idxs[rng.choice(len(idxs), p=w / w.sum())]


def _weights_idx(weights):
    return {IDX[f]: w for f, w in weights.items() if w > 0 and IDX[f] not in ENTRANCE_IDX}


def _ent_idx(ent):
    return {IDX[f]: w for f, w in ent.items() if w > 0}


def _uses_stairs(rng, o, d):
    p = C.P_STAIRS.get(abs(o - d), 0.0)
    return rng.random() < p


def _batch_sizes(rng, n_total, mean):
    sizes = []
    while sum(sizes) < n_total:
        sizes.append(int(rng.geometric(1.0 / mean)) if mean > 1 else 1)
    sizes[-1] -= sum(sizes) - n_total
    return [s for s in sizes if s > 0]


def generate_scenario(sc: Scenario, seed: int, t0: float = 0.0):
    """按场景参数生成乘客列表。返回 (乘梯乘客列表, 走楼梯人数)。"""
    rng = np.random.default_rng(seed)
    w_up = _weights_idx(sc.weights)
    ent = _ent_idx(sc.entrances)
    n_batches = rng.poisson(sc.lam5 / 300.0 * sc.duration / sc.batch_mean)
    if sc.profile == "burst":
        times = rng.triangular(0, sc.peak_at * sc.duration, sc.duration, n_batches)
    else:
        times = rng.uniform(0, sc.duration, n_batches)
    kinds = rng.choice(3, size=n_batches, p=np.array(sc.split) / sum(sc.split))
    pax, stairs, gid = [], 0, 0
    for t, k in zip(np.sort(times), kinds):
        size = int(rng.geometric(1.0 / sc.batch_mean)) if sc.batch_mean > 1 else 1
        if k == 0:
            o, d = _pick(rng, ent), _pick(rng, w_up)
        elif k == 1:
            o, d = _pick(rng, w_up), _pick(rng, ent)
        else:
            o = _pick(rng, w_up)
            d = o
            while d == o:
                d = _pick(rng, w_up)
        gid += 1
        if sc.stairs and _uses_stairs(rng, o, d):
            stairs += size
            continue
        for _ in range(size):
            pax.append(Passenger(0, t0 + t + rng.uniform(0, 3.0), o, d, gid))
    pax.sort(key=lambda p: p.t)
    for i, p in enumerate(pax):
        p.id = i
    return pax, stairs


# ---------------- 课表模式 ----------------
DAY_START = 8 * 3600        # 全天仿真从 08:00 开始，时间以秒计（相对 08:00）


def _hm(s):
    h, m = s.split(":")
    return int(h) * 3600 + int(m) * 60 - DAY_START


def load_timetable(path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            rows.append(dict(room=r["room"], floor=IDX[r["floor"]], start=_hm(r["start"]),
                             end=_hm(r["end"]), size=int(r["size"])))
    return rows


def background_rate(t):
    """课表之外的办公、访客客流（每 5 分钟人数, 进/离/楼层间比例）[占位]"""
    hour = 8 + t / 3600
    if 8.5 <= hour < 9.5:
        return 25, (0.75, 0.05, 0.20)       # 上班进楼
    if 12.0 <= hour < 14.0:
        return 45, (0.40, 0.40, 0.20)       # 饭点
    if 17.5 <= hour < 18.5:
        return 25, (0.05, 0.75, 0.20)       # 下班离楼
    return 12, (0.30, 0.30, 0.40)


def generate_day(timetable, seed, attendance=0.8, lift_share_in=1.0, t_end=11 * 3600):
    """课表 + 背景客流生成一整天（08:00 起 t_end 秒）的乘客。

    attendance：出席率；做"需求估计误差"测试时把实际出席率乘以 0.8 / 1.2。
    """
    rng = np.random.default_rng(seed)
    ent = _ent_idx(C.ENTRANCE_WEIGHTS)
    w_office = _weights_idx(C.FLOOR_WEIGHTS_OFFICE)
    raw = []    # (t, o, d, group)
    gid = 0

    def add_group(times_fn, n, o_fn, d_fn, mean_batch):
        nonlocal gid
        for size in _batch_sizes(rng, n, mean_batch):
            t = times_fn()
            o, d = o_fn(), d_fn()
            if o == d:
                continue
            gid += 1
            if _uses_stairs(rng, o, d):
                continue
            for _ in range(size):
                raw.append((t + rng.uniform(0, 3.0), o, d, gid))

    for c in timetable:
        n = int(round(c["size"] * attendance))
        f = c["floor"]
        # 上课：开课前 12 分钟到开课后 2 分钟到达，高峰在开课前 4 分钟；85% 从入口进楼
        add_group(lambda: rng.triangular(c["start"] - 720, c["start"] - 240, c["start"] + 120),
                  n, lambda: _pick(rng, ent) if rng.random() < 0.85 else _pick(rng, w_office),
                  lambda: f, 1.4)
        # 下课：下课后陆续离开（指数分布，均值 90 秒）；80% 离楼
        add_group(lambda: c["end"] + min(rng.exponential(90.0), 480.0), n, lambda: f,
                  lambda: _pick(rng, ent) if rng.random() < 0.8 else _pick(rng, w_office), 1.6)

    # 背景客流：逐分钟按当时的到达率生成
    for m in range(int(t_end // 60)):
        lam5, split = background_rate(m * 60)
        n_b = rng.poisson(lam5 / 5.0 / 1.3)
        for _ in range(n_b):
            k = rng.choice(3, p=split)
            if k == 0:
                o, d = _pick(rng, ent), _pick(rng, w_office)
            elif k == 1:
                o, d = _pick(rng, w_office), _pick(rng, ent)
            else:
                o, d = _pick(rng, w_office), _pick(rng, w_office)
            if o == d:
                continue
            gid += 1
            if _uses_stairs(rng, o, d):
                continue
            t = m * 60 + rng.uniform(0, 60)
            for _ in range(int(rng.geometric(1 / 1.3))):
                raw.append((t + rng.uniform(0, 3.0), o, d, gid))

    raw = [r for r in raw if 0 <= r[0] < t_end]
    raw.sort()
    return [Passenger(i, t, o, d, g) for i, (t, o, d, g) in enumerate(raw)]
