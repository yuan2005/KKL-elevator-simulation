"""建筑和电梯运动学：楼层高度、运行时间、电梯状态。"""
import math
import config as C

N_FLOORS = len(C.FLOORS)
IDX = {name: i for i, name in enumerate(C.FLOORS)}
HEIGHT = [i * C.FLOOR_HEIGHT for i in range(N_FLOORS)]
ENTRANCE_IDX = [IDX[f] for f in C.ENTRANCES]
MOTION_TOL = 1e-7            # metres, floating-point tolerance, not a timestep allowance


def flight_time(distance, v=C.RATED_SPEED, a=C.ACCEL):
    """从静止到静止跑完 distance 米所需时间（梯形或三角形速度曲线）。"""
    if distance <= 0:
        return 0.0
    d_acc = v * v / a                  # 加速 + 减速所需距离
    if distance <= d_acc:              # 达不到额定速度：三角形曲线
        return 2.0 * math.sqrt(distance / a)
    return 2.0 * v / a + (distance - d_acc) / v


def floors_flight_time(f1, f2):
    return flight_time(abs(HEIGHT[f2] - HEIGHT[f1]))


def advance_to_stop(distance, speed, dt):
    """Advance along a feasible accelerate/cruise/brake profile for up to dt.

    Integrate phase boundaries inside the tick instead of accelerating past the
    braking point and asking the dispatcher to abandon the destination later.
    Returns (travelled distance, new speed, elapsed time, reached target).
    """
    a, vmax = C.ACCEL, C.RATED_SPEED
    if dt <= 0 or a <= 0 or vmax <= 0 or speed < 0 or speed > vmax + MOTION_TOL:
        raise ValueError('Invalid motion state or configuration')
    stop_distance = speed * speed / (2 * a)
    if distance < -MOTION_TOL or distance + MOTION_TOL < stop_distance:
        raise ValueError('Selected target is inside the physical stopping distance')
    distance = max(0.0, distance)
    peak = min(vmax, max(speed, math.sqrt(a * distance + speed * speed / 2)))
    accelerate = max(0.0, (peak - speed) / a)
    brake = peak / a
    accel_distance = (peak * peak - speed * speed) / (2 * a)
    brake_distance = peak * peak / (2 * a)
    cruise = max(0.0, (distance - accel_distance - brake_distance) / peak) if peak else 0.0
    duration = accelerate + cruise + brake
    if dt >= duration:
        return distance, 0.0, duration, True
    left, travelled, velocity = dt, 0.0, speed
    for phase_time, acceleration in ((accelerate, a), (cruise, 0.0), (brake, -a)):
        elapsed = min(left, phase_time)
        travelled += velocity * elapsed + .5 * acceleration * elapsed * elapsed
        velocity += acceleration * elapsed
        left -= elapsed
        if left <= 0:
            break
    return travelled, max(0.0, velocity), dt, False


class Car:
    """一台电梯。状态：idle（停着没事）/ moving（运行）/ doors（开门上下客）/ out（停用）。"""

    def __init__(self, cid, floor):
        self.id = cid
        self.x = HEIGHT[floor]          # 位置（米）
        self.v = 0.0                    # 速率（>=0），方向看 self.dir
        self.floor = floor              # 最近一次停靠/经过的楼层
        self.dir = 0                    # +1 上，-1 下，0 无方向
        self.state = "idle"
        self.target = None              # 当前要去的楼层
        self.parking = None             # 停靠指令的目标楼层（无乘客时）
        self.passengers = []            # 车内乘客
        self.car_calls = set()          # 车内乘客按下的目的楼层
        self.assigned = set()           # 分到这台车的厅外呼梯 key
        self.dwell_left = 0.0
        self.up_sector = None           # 上行分区：入口层只接去这个分区的乘客（分区编号）
        self.down_sector = None         # 下行分区：这台车负责的楼层集合
        self.active = True
        # 统计
        self.distance = 0.0
        self.starts = 0
        self.active_time = 0.0
        self.lobby_departures = []      # 校验 RTT 用：每次从入口层载客上行的时刻和载客数

    @property
    def load(self):
        return len(self.passengers)

    def position_floor(self):
        """当前位置换算成（可能非整数的）楼层。"""
        return self.x / C.FLOOR_HEIGHT

    def can_stop_at(self, f):
        """以当前速度和最大减速度，能否在 f 层停下（f 必须在运行方向前方）。"""
        if self.state != "moving":
            return True
        dist = (HEIGHT[f] - self.x) * self.dir
        if dist < -MOTION_TOL:
            return False
        # A new call cannot rely on a grid-sized allowance to exceed the
        # configured deceleration. Motion integration uses the same constraint.
        return dist + MOTION_TOL >= self.v * self.v / (2 * C.ACCEL)
