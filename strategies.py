"""可组合的调度策略。

一个"组合"（Combo）= 调度器 + 停靠策略 + 满载直驶开关 + 运行台数 [+ 分区设置]。
每种策略都对应 Plan 里引用的文献，见各类的说明。
"""
import config as C
from building import HEIGHT, ENTRANCE_IDX, N_FLOORS, IDX, flight_time

T_STOP = C.T_DOOR_OPEN + C.T_DOOR_CLOSE + 2 * C.T_PASSENGER + 2.0   # 每多停一站大约多花的时间（含加减速损失）
INF = float("inf")


# ======================= 路径估计（各调度器共用） =======================
def path_estimate(car, f, d):
    """估计电梯从当前位置到 f 层接 d 方向乘客要走多少层、中途停几站。"""
    pos = car.x / C.FLOOR_HEIGHT
    cd = car.dir
    stops = set(car.car_calls) | {k[0] for k in car.assigned}
    if cd == 0:
        return abs(f - pos), 0
    if (f - pos) * cd >= 0 and d == cd and car.can_stop_at(f):
        between = [s for s in stops if (s - pos) * cd > 0 and (f - s) * cd > 0]
        return abs(f - pos), len(between)
    ahead = [s for s in stops if (s - pos) * cd > 0]
    turn = max(ahead, key=lambda s: abs(s - pos)) if ahead else pos
    dist = abs(turn - pos) + abs(turn - f)
    if d == cd:                                     # 同方向但已错过：要折返两次
        dist += 2 * abs(f - pos)
    return dist, len(ahead) + len(stops) - len(ahead)


def eta(car, f, d):
    if car.state == "out" or not car.active:
        return INF
    dist, nst = path_estimate(car, f, d)
    t = flight_time(dist * C.FLOOR_HEIGHT) + nst * T_STOP
    if car.state == "doors":
        t += car.dwell_left
    return t


# ======================= 调度器 =======================
class Dispatcher:
    name = "base"
    reassign = True

    def candidates(self, sim, call):
        return [c for c in sim.cars if c.active and c.state != "out" and sim.allowed(c, call.key)]

    def cost(self, sim, car, call):
        raise NotImplementedError

    def choose(self, sim, call):
        cands = self.candidates(sim, call)
        if not cands:
            return None
        best = min(cands, key=lambda c: (self.cost(sim, c, call), c.id))
        return best.id


class NearestCar(Dispatcher):
    """最近车：按路径楼层数选车，不考虑中途停站和载客（Peters 2014 §3.1）。"""
    name = "NearestCar"

    def cost(self, sim, car, call):
        f, d, _ = call.key
        return path_estimate(car, f, d)[0]


class NearestCarNoReassign(NearestCar):
    """现状基线：最近车，一旦分配不再改派（对应 Plan 1.2 节在 KKL 的观察）。"""
    name = "NearestNoReassign"
    reassign = False


class ETA(Dispatcher):
    """预计到达时间：计入途中停站；已要停该层的车给加分（Peters 2014 §3.2–3.3）。"""
    name = "ETA"
    coincident_bonus = 10.0

    def cost(self, sim, car, call):
        f, d, _ = call.key
        t = eta(car, f, d)
        if f in car.car_calls and car.dir == d:
            t -= self.coincident_bonus
        if sim.bypass_full(car):
            t += 120.0
        return t


class RSRLite(ETA):
    """简化版 RSR：ETA 基础上加一组奖惩分（Bittar 1982 的思路）。"""
    name = "RSR"

    def cost(self, sim, car, call):
        t = super().cost(sim, car, call)
        t += 4.0 * len(car.assigned)                          # 已分配呼梯多：扣分
        t += 25.0 * max(0.0, car.load / C.CAPACITY - 0.6)     # 车内较满：扣分
        if car.state == "idle" and car.load == 0:
            t -= 3.0                                           # 空闲车：加分
        waited = sim.t - call.t_reg
        t -= min(waited, 60.0) * 0.2                          # 等得久的呼梯让更快的车优先
        return t


class DownSectorETA(ETA):
    """下行分区：下行呼梯只交给负责该楼层分区的车（Barney 2003，见 Peters 2014 §5.2）。"""
    name = "DownSector"

    def candidates(self, sim, call):
        cands = super().candidates(sim, call)
        f, d, _ = call.key
        if d == -1 and f not in ENTRANCE_IDX:
            sec = [c for c in cands if c.down_sector and f in c.down_sector]
            return sec or cands
        return cands


# ======================= 停靠策略 =======================
class NoParking:
    name = "无停靠"

    def target(self, sim, car):
        return None


def _claimed(sim, exclude, same_sector=False):
    """其他空闲车已经停在或正开往的楼层（same_sector=True 时只数同一上行分区的车）。"""
    out = {}
    for c in sim.cars:
        if c is exclude or not c.active or c.load or c.assigned:
            continue
        if same_sector and c.up_sector != exclude.up_sector:
            continue
        f = c.parking if c.parking is not None else (c.floor if c.state in ("idle", "doors") else None)
        if f is not None:
            out[f] = out.get(f, 0) + 1
    return out


class EntranceParking:
    """空梯回入口层待命（Peters 2014 §5.1 的 parking calls；Brand & Nikovski 2004）。"""
    name = "入口层停靠"

    def __init__(self, weights=None):
        self.weights = weights or {IDX[k]: v for k, v in C.ENTRANCE_WEIGHTS.items()}

    def target(self, sim, car):
        # 上行分区时，每个分区的车各自在两个入口层之间分配，避免某入口层没有该分区的车
        cl = _claimed(sim, car, same_sector=car.up_sector is not None)
        return min(self.weights, key=lambda f: (cl.get(f, 0) + 1) / self.weights[f])


class DistributedParking:
    """分散停靠：入口层留一台，其余均匀分布在楼内（Peters 2014 §5.1）。"""
    name = "分散停靠"

    def target(self, sim, car):
        n = sum(1 for c in sim.cars if c.active)
        homes = [IDX["G"]]
        if n > 1:
            homes.append(IDX["LG2"])
        k = n - len(homes)
        upper = list(range(IDX["1"], N_FLOORS))
        for i in range(k):
            homes.append(upper[int((i + 0.5) * len(upper) / k)])
        cl = _claimed(sim, car)
        free = [h for h in homes if cl.get(h, 0) == 0] or homes
        return min(free, key=lambda h: abs(h - car.floor))


class SectorTopParking:
    """下行分区：空梯停到自己分区的最高层（Barney 2003）。"""
    name = "分区顶层停靠"

    def target(self, sim, car):
        return max(car.down_sector) if car.down_sector else None


class ForecastParking:
    """按课表预测：下课前把空梯调到即将下课的楼层，按人数比例分配（Brand & Nikovski 2004：
    让空梯分布与乘客出现的分布一致）。没有即将下课的课时退回分散停靠。"""
    name = "课表预测停靠"

    def __init__(self, forecast, lead=180.0):
        self.forecast = forecast        # [(下课时刻, 楼层, 预计人数)]
        self.lead = lead
        self.fallback = DistributedParking()

    def target(self, sim, car):
        soon = {}
        for t_end, f, n in self.forecast:
            if 0 <= t_end - sim.t <= self.lead or 0 <= sim.t - t_end <= 120:
                soon[f] = soon.get(f, 0) + n
        if not soon:
            return self.fallback.target(sim, car)
        cl = _claimed(sim, car)
        return min(soon, key=lambda f: ((cl.get(f, 0) + 1) / soon[f], abs(f - car.floor)))


# ======================= 分区划分 =======================
def demand_sectors(n_sectors, weights, balance="rtt"):
    """把非入口层切成 n 个连续分区。

    balance="demand"：各分区乘客数大致相等（So, Yu & Chan 1999 的"需求匹配"）
    balance="rtt"   ：各分区"乘客数 × 离 G 层距离"大致相等，近似各分区往返时间相等
                      （同文的"均匀 RTT"思路）。KKL 课室集中在低层，按人数切会让高层分区过载。
    """
    g = IDX["G"]
    floors = [f for f in range(N_FLOORS) if f not in ENTRANCE_IDX and weights.get(f, 0) > 0]
    load = {f: weights[f] * (abs(f - g) + 1 if balance == "rtt" else 1) for f in floors}
    total = sum(load.values())
    sectors, cur, acc = [], [], 0.0
    for f in floors:
        cur.append(f)
        acc += load[f]
        if acc >= total * (len(sectors) + 1) / n_sectors and len(sectors) < n_sectors - 1:
            sectors.append(cur)
            cur = []
    sectors.append(cur)
    return [s for s in sectors if s]


# ======================= 组合 =======================
class Combo:
    def __init__(self, name, dispatcher, parking, bypass=True, active_cars=None,
                 up_sectors=0, down_sectors=0, sector_weights=None):
        self.name = name
        self.dispatcher = dispatcher
        self.parking = parking
        self.bypass = bypass
        self.active_cars = active_cars
        self.up_sectors = up_sectors
        self.down_sectors = down_sectors
        w = sector_weights or C.FLOOR_WEIGHTS_CLASS
        self.weights = {IDX[k]: v for k, v in w.items()}
        self._up_map = {}

    def setup(self, sim):
        active = [c for c in sim.cars if c.id not in sim.broken]
        if self.active_cars is not None:
            active = active[:self.active_cars]
        for c in sim.cars:
            c.up_sector = None
            c.down_sector = None
        self._up_map = {}
        if self.up_sectors:
            secs = demand_sectors(self.up_sectors, self.weights)
            for i, s in enumerate(secs):
                for f in s:
                    self._up_map[f] = i
            for j, c in enumerate(active):
                c.up_sector = j % len(secs)
        if self.down_sectors:
            secs = demand_sectors(self.down_sectors, self.weights)
            for j, c in enumerate(active):
                c.down_sector = set(secs[j % len(secs)])

    def tag(self, sim, p):
        """上行分区时，入口层上行乘客按目的楼层分到对应分区的电梯（入口层有分区指示牌）。"""
        if self.up_sectors and p.o in ENTRANCE_IDX and p.d > p.o and p.d not in ENTRANCE_IDX:
            return self._up_map.get(p.d, 0)
        return None


def make_combos(forecast=None):
    """候选组合。名字里的 C0–C6 在实验结果和报告中统一使用。"""
    combos = {
        "C0": Combo("C0 现状基线(最近车,不改派)", NearestCarNoReassign(), NoParking(), bypass=False),
        "C1": Combo("C1 ETA+分散停靠", ETA(), DistributedParking()),
        "C2": Combo("C2 上行分区+入口停靠", ETA(), EntranceParking(), up_sectors=2),
        "C3": Combo("C3 下行分区+分区顶层停靠", DownSectorETA(), SectorTopParking(), down_sectors=3),
        "C4": Combo("C4 RSR+分散停靠", RSRLite(), DistributedParking()),
        "C7": Combo("C7 ETA+入口停靠(不分区,对照)", ETA(), EntranceParking()),
    }
    if forecast is not None:
        combos["C6"] = Combo("C6 ETA+课表预测停靠", ETA(), ForecastParking(forecast))
    return combos
