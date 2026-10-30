"""场景切换控制器：决定每个时刻用哪个策略组合。

- Static：全天只用一个组合（对照组）
- LabelSwitch：按"时段名称"切换（上课窗口就用 S1 的最优组合……）。第一版的做法，保留作反例
- MapSwitch：用最近 5 分钟**观测到**的人流参数查 E1 策略地图（决策树）
- ForecastMapSwitch：用课表**预测**接下来 5 分钟的人流参数，再查 E1 策略地图
"""
import bisect
import json

from .building import ENTRANCE_IDX, IDX
from .traffic import background_rate

FEATURES = ["lam5", "s_in", "s_out", "s_high"]
HIGH_FLOOR = IDX["5"]


def features(pax, seconds):
    """一组乘梯乘客的人流参数（全部可以由人流摄像头 / 呼梯记录统计）：
    lam5   每 5 分钟乘梯人数
    s_in   进楼比例（从入口层出发）
    s_out  离楼比例（去入口层）
    s_high 进楼乘客中去 5/F 及以上的比例
    """
    n = len(pax)
    if n == 0:
        return dict(lam5=0.0, s_in=0.0, s_out=0.0, s_high=0.0)
    ins = [p for p in pax if p.o in ENTRANCE_IDX]
    return dict(lam5=n * 300.0 / seconds,
                s_in=len(ins) / n,
                s_out=sum(1 for p in pax if p.d in ENTRANCE_IDX) / n,
                s_high=(sum(1 for p in ins if p.d >= HIGH_FLOOR) / len(ins)) if ins else 0.0)


class TreePolicy:
    """E1 训练出的两步地图（存成 JSON，不依赖 sklearn 也能用）：
    第 1 步节能判断树：True = 只开 2 台也能接受 → C5；第 2 步等待最优树：选 C1/C2/C3/C4/C7。"""

    def __init__(self, path):
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
        self.features = d["features"]
        self.eco, self.wait = d["eco"], d["wait"]

    def _walk(self, nodes, feat):
        i = 0
        while nodes[i]["f"] >= 0:
            nd = nodes[i]
            i = nd["l"] if feat[self.features[nd["f"]]] <= nd["thr"] else nd["r"]
        return nodes[i]["label"]

    def predict(self, feat, eco=True):
        if eco and self._walk(self.eco, feat) == "True":
            return "C5"
        return self._walk(self.wait, feat)


class Static:
    interval = 60.0

    def __init__(self, combo):
        self.combo = combo

    def update(self, sim, t):
        return self.combo


class LabelSwitch:
    """按时段名称切换：开课前 10 分钟到开课后 2 分钟 = 上课，下课前 1 分钟到后 6 分钟 = 下课，
    重叠 = 换课，12:00–14:00 = 饭点，其他 = 平时；每类时段固定用一个组合。"""
    interval = 30.0

    def __init__(self, combos, timetable, mode_map, pre_in=600, post_out=360):
        self.combos = combos
        self.mode_map = mode_map
        self.windows = []
        for c in timetable:
            self.windows.append((c["start"] - pre_in, c["start"] + 120, "in"))
            self.windows.append((c["end"] - 60, c["end"] + post_out, "out"))

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


class MapSwitch:
    """实时：看最近 window 秒内到达电梯厅的乘客（参考 HKU App 的 MTR Queuing Condition 做法）。"""
    interval = 30.0

    def __init__(self, combos, policy, window=300.0, eco=True):
        self.combos = combos
        self.policy = policy
        self.window = window
        self.eco = eco                  # False：跳过节能判断（例如上课日不进节能模式）

    def choose(self, feat):
        return self.policy.predict(feat, eco=self.eco)

    def update(self, sim, t):
        if not hasattr(sim, "_arrival_times"):
            sim._arrival_times = [p.t for p in sim.pax]
        lo = bisect.bisect_left(sim._arrival_times, t - self.window)
        span = min(self.window, max(t, 1.0))
        return self.combos[self.choose(features(sim.pax[lo:sim.i_next], span))]


class ForecastMapSwitch(MapSwitch):
    """预测：forecast 是按课表 + 名义出席率生成的"预计乘客"（多次抽样合并），
    在每个时刻看接下来 [t - back, t + ahead] 的预计人流参数，再查地图。"""

    def __init__(self, combos, policy, forecast, n_samples, back=60.0, ahead=240.0, eco=True):
        super().__init__(combos, policy, eco=eco)
        self.fc = sorted(forecast, key=lambda p: p.t)
        self.fc_t = [p.t for p in self.fc]
        self.k = n_samples
        self.back, self.ahead = back, ahead

    def update(self, sim, t):
        lo = bisect.bisect_left(self.fc_t, t - self.back)
        hi = bisect.bisect_right(self.fc_t, t + self.ahead)
        feat = features(self.fc[lo:hi], (self.back + self.ahead) * self.k)
        return self.combos[self.choose(feat)]
