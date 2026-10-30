"""场景切换控制器：决定每个时刻用哪个策略组合。

- Static：全天只用一个组合（对照组）
- TimetableSwitch：按课表和校历提前切换（Plan 的"预知层"）
- ThresholdSwitch：按最近 5 分钟观测到的客流参数和 E2 得出的阈值切换（Plan 的"实时校正层"）
"""
import bisect
from .building import ENTRANCE_IDX, IDX
from .traffic import background_rate


class Static:
    interval = 60.0

    def __init__(self, combo):
        self.combo = combo

    def update(self, sim, t):
        return self.combo


class TimetableSwitch:
    interval = 30.0

    def __init__(self, combos, timetable, mode_map, pre_in=600, post_out=360):
        self.combos = combos
        self.mode_map = mode_map        # {"in": "C2", "out": "C6", "mix": "C4", "lunch": "C1", "normal": "C1"}
        self.windows = []
        for c in timetable:
            self.windows.append((c["start"] - pre_in, c["start"] + 120, "in"))
            self.windows.append((c["end"] - 60, c["end"] + post_out, "out"))
        self.log = []

    def mode(self, t):
        kinds = {k for a, b, k in self.windows if a <= t <= b}
        if "in" in kinds and "out" in kinds:
            return "mix"
        if kinds:
            return kinds.pop()
        lam, split = background_rate(t)
        if split[0] == 0.40 and split[1] == 0.40:
            return "lunch"
        return "normal"

    def update(self, sim, t):
        return self.combos[self.mode_map[self.mode(t)]]


class ThresholdSwitch:
    """只看"观测得到"的量：最近 window 秒内到达电梯厅的乘客（可由人流摄像头统计，
    参考 HKU App 的 MTR Queuing Condition 做法）。"""
    interval = 30.0

    def __init__(self, combos, rules, window=300.0, default="C1"):
        self.combos = combos
        self.rules = rules              # [(条件函数(lam5, s_in, s_out, s_inter, s_high) -> bool, 组合键)]
        self.window = window
        self.default = default

    def observe(self, sim, t):
        """返回 (每 5 分钟到达人数, 进楼比例, 离楼比例, 楼层间比例, 进楼者去 5/F 以上的比例)"""
        if not hasattr(sim, "_arrival_times"):
            sim._arrival_times = [p.t for p in sim.pax]
        lo = bisect.bisect_left(sim._arrival_times, t - self.window)
        recent = sim.pax[lo:sim.i_next]
        n = len(recent)
        if n == 0:
            return 0.0, 0.0, 0.0, 0.0, 0.0
        ins = [p for p in recent if p.o in ENTRANCE_IDX]
        s_in = len(ins) / n
        s_out = sum(1 for p in recent if p.d in ENTRANCE_IDX) / n
        s_high = (sum(1 for p in ins if p.d >= IDX["5"]) / len(ins)) if ins else 0.0
        return n * 300.0 / self.window, s_in, s_out, 1.0 - s_in - s_out, s_high

    def update(self, sim, t):
        obs = self.observe(sim, t)
        for cond, key in self.rules:
            if cond(*obs):
                return self.combos[key]
        return self.combos[self.default]
