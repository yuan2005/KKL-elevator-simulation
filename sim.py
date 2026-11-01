"""仿真主循环（固定步长的离散时间仿真）。

每个时间步：
  1. 场景控制器决定是否切换策略组合
  2. 新到达的乘客登记厅外呼梯，交给调度器分配电梯
  3. 允许重新分配的调度器定期重算分配
  4. 推进每台电梯：开门上下客 / 选择下一站（集选 + LOOK 逻辑）/ 运动学
"""
import config as C
from building import Car, HEIGHT, ENTRANCE_IDX, N_FLOORS, advance_to_stop


class HallCall:
    __slots__ = ("key", "queue", "car", "t_reg")

    def __init__(self, key, t):
        self.key = key          # (楼层, 方向, 分区标签或 None)
        self.queue = []
        self.car = None         # 分配到的电梯编号
        self.t_reg = t


class Sim:
    def __init__(self, passengers, controller, gen_end, broken_cars=(), n_cars=C.N_CARS):
        self.pax = passengers
        self.i_next = 0
        self.gen_end = gen_end
        self.cars = [Car(i, ENTRANCE_IDX[i % len(ENTRANCE_IDX)]) for i in range(n_cars)]
        self.broken = set(broken_cars)
        self.calls = {}
        self.t = 0.0
        self.controller = controller
        self.combo = None
        self.next_ctrl = 0.0
        self.next_reassign = 0.0
        self.combo_log = []         # (时刻, 组合名)
        self.last_alight_time = 0.0

    # ---------- 策略组合 ----------
    def set_combo(self, combo):
        self.combo = combo
        self.combo_log.append((self.t, combo.name))
        combo.setup(self)
        usable = [c.id for c in self.cars if c.id not in self.broken]
        if combo.active_cars is not None:
            usable = usable[:combo.active_cars]
        for car in self.cars:
            car.active = car.id in usable
            if car.state == "out" and car.active:
                car.state = "idle"
            car.assigned.clear()
        # 分区标签可能变了：按新规则重新登记所有排队乘客
        waiting = [p for call in self.calls.values() for p in call.queue]
        self.calls = {}
        for p in sorted(waiting, key=lambda p: p.t):
            self._register(p, assign=False)
        for call in self.calls.values():
            self.assign(call)

    def _register(self, p, assign=True):
        key = (p.o, p.dir, self.combo.tag(self, p))
        call = self.calls.get(key)
        new = call is None
        if new:
            call = self.calls[key] = HallCall(key, self.t)
        call.queue.append(p)
        if new and assign:
            self.assign(call)

    def allowed(self, car, key):
        """这台车能不能接这个呼梯（分区标签要匹配）。"""
        return key[2] is None or key[2] == car.up_sector

    def assign(self, call):
        old = call.car
        best = self.combo.dispatcher.choose(self, call)
        if old is not None and old != best:
            self.cars[old].assigned.discard(call.key)
        call.car = best
        if best is not None:
            self.cars[best].assigned.add(call.key)

    def unassign(self, key):
        call = self.calls.get(key)
        if call is not None and call.car is not None:
            self.cars[call.car].assigned.discard(key)
            call.car = None

    def committed(self, call):
        """电梯已经在减速停靠或已开门，就不再改派。"""
        if call.car is None:
            return False
        car = self.cars[call.car]
        f = call.key[0]
        if car.state == "doors" and car.floor == f:
            return True
        if car.state == "moving" and car.target == f:
            rem = abs(HEIGHT[f] - car.x)
            return rem <= car.v * car.v / (2 * C.ACCEL) * 1.5 + 0.5
        return False

    # ---------- 主循环 ----------
    def run(self):
        last_arrival = self.pax[-1].t if self.pax else 0.0
        t_stop = max(self.gen_end, last_arrival) + C.DRAIN_TIME
        while self.t < t_stop:
            if (self.i_next >= len(self.pax) and self.t > self.gen_end and not self.calls
                    and all(c.load == 0 for c in self.cars)
                    and self.t >= self.last_alight_time):
                break
            self.step()
        return self

    def step(self):
        t = self.t
        if t >= self.next_ctrl:
            new = self.controller.update(self, t)
            if new is not None and new is not self.combo:
                self.set_combo(new)
            self.next_ctrl += self.controller.interval
        while self.i_next < len(self.pax) and self.pax[self.i_next].t <= t:
            self._register(self.pax[self.i_next])
            self.i_next += 1
        if t >= self.next_reassign:
            reassign = self.combo.dispatcher.reassign
            for call in list(self.calls.values()):
                if call.car is None:
                    self.assign(call)
                elif reassign and not self.committed(call):
                    self.assign(call)
                elif not self.cars[call.car].active:
                    self.assign(call)
            self.next_reassign += C.REASSIGN_INTERVAL
        for car in self.cars:
            self.update_car(car)
        self.t += C.DT

    # ---------- 电梯行为 ----------
    def bypass_full(self, car):
        return self.combo.bypass and car.load >= C.BYPASS_FRACTION * C.CAPACITY

    def update_car(self, car):
        if car.state == "out":
            return
        car.active_time += C.DT
        if car.state == "doors":
            car.dwell_left -= C.DT
            if car.dwell_left > 0:
                return
            n = self.board(car, car.floor, extra=True)      # 关门前又来的人
            if n:
                car.dwell_left = n * C.T_PASSENGER
                return
            car.state = "idle"
        if not car.active and car.load == 0 and car.state == "idle":
            for k in list(car.assigned):
                self.unassign(k)
            car.state = "out"
            return
        if self.bypass_full(car):
            for k in list(car.assigned):
                if k[1] == car.dir:
                    self.unassign(k)
        if car.state == "idle":
            if any(k[0] == car.floor and k in self.calls for k in car.assigned):
                self.stop_here(car, forced=True)
                return
            tgt = self.next_target(car)
            if tgt is None:
                park = self.combo.parking.target(self, car) if (car.load == 0 and car.active) else None
                if park is None or park == car.floor:
                    car.dir = 0
                    return
                car.parking = park
                tgt = park
            else:
                car.parking = None
            car.dir = 1 if tgt > car.floor else -1
            car.target = tgt
            car.state = "moving"
            car.starts += 1
        if car.state == "moving":
            tgt = self.next_target(car)
            if tgt is None:
                if (car.parking is not None and car.load == 0 and not car.assigned and
                        (HEIGHT[car.parking] - car.x) * car.dir > 0 and car.can_stop_at(car.parking)):
                    tgt = car.parking
                else:
                    tgt = self.nearest_stoppable(car)
            else:
                car.parking = None
            car.target = tgt
            self.move(car)

    def nearest_stoppable(self, car):
        rng = range(N_FLOORS) if car.dir > 0 else range(N_FLOORS - 1, -1, -1)
        for f in rng:
            if (HEIGHT[f] - car.x) * car.dir > -1e-6 and car.can_stop_at(f):
                return f
        return car.target

    def next_target(self, car):
        moving = car.state == "moving"
        d = car.dir
        if not moving and d == 0:
            reqs = set(car.car_calls) | {k[0] for k in car.assigned}
            if not reqs:
                return None
            near = min(reqs, key=lambda f: abs(f - car.floor))
            if near == car.floor:
                return None
            d = 1 if near > car.floor else -1

        def ahead(f, dd):
            if moving:
                return (HEIGHT[f] - car.x) * dd > 1e-6 and car.can_stop_at(f)
            return (f - car.floor) * dd > 0

        skip_hall = self.bypass_full(car)
        for attempt in range(2):
            same = [f for f in car.car_calls if ahead(f, d)]
            if not skip_hall:
                same += [k[0] for k in car.assigned if k[1] == d and ahead(k[0], d)]
            if same:
                car.dir = d
                return min(same, key=lambda f: abs(HEIGHT[f] - car.x))
            opp = [k[0] for k in car.assigned if k[1] == -d and ahead(k[0], d)]
            if opp:
                car.dir = d
                return max(opp, key=lambda f: abs(HEIGHT[f] - car.x))
            if moving:
                return None
            d = -d
        return None

    def move(self, car):
        d = car.dir
        tgt_h = HEIGHT[car.target]
        rem = (tgt_h - car.x) * d
        distance, velocity, elapsed, arrived = advance_to_stop(rem, car.v, C.DT)
        car.distance += distance
        if arrived:
            car.x = tgt_h
            self.arrive(car, elapsed)
            return
        car.x += d * distance
        car.v = velocity

    def arrive(self, car, elapsed=0.0):
        car.v = 0.0
        car.floor = car.target
        car.x = HEIGHT[car.floor]
        car.state = "idle"
        self.stop_here(car, forced=False, event_time=self.t + elapsed)

    def requests_ahead(self, car, f, d):
        if any((c - f) * d > 0 for c in car.car_calls):
            return True
        return any((k[0] - f) * d > 0 for k in car.assigned)

    def stop_here(self, car, forced, event_time=None):
        """电梯停在 car.floor：先下客，再决定方向，再上客。"""
        f = car.floor
        event_time = self.t if event_time is None else event_time
        alight = [p for p in car.passengers if p.d == f]
        t_open = event_time + C.T_DOOR_OPEN
        for k, p in enumerate(alight):
            p.t_alight = t_open + (k + 1) * C.T_PASSENGER
        if alight:
            self.last_alight_time = max(self.last_alight_time, alight[-1].t_alight)
            car.passengers = [p for p in car.passengers if p.d != f]
        car.car_calls.discard(f)
        had_call_here = forced or any(k[0] == f for k in car.assigned)

        d = car.dir
        here = [k for k in self.calls if k[0] == f and self.allowed(car, k)]
        if car.car_calls:
            d = 1 if any(c > f for c in car.car_calls) else -1
        elif d != 0 and self.requests_ahead(car, f, d):
            pass
        elif d != 0 and any(k[1] == d for k in here):
            pass
        elif here:
            mine = [k for k in here if k in car.assigned]
            d = (mine or here)[0][1]
        elif d != 0 and self.requests_ahead(car, f, -d):
            d = -d
        else:
            d = 0
        car.dir = d
        n_board = self.board(car, f, extra=False, offset=len(alight), event_time=event_time)
        for k in [k for k in car.assigned if k[0] == f]:
            self.unassign(k)                # 停了但没接走（方向不对或满载）：交回重新分配
        if forced and not alight and not n_board:
            had_call_here = False
        if alight or n_board or had_call_here:
            car.state = "doors"
            car.dwell_left = (C.T_DOOR_OPEN + (len(alight) + n_board) * C.T_PASSENGER
                              + C.T_DOOR_CLOSE + event_time - self.t)
        if car.parking == f:
            car.parking = None

    def board(self, car, f, extra, offset=0, event_time=None):
        if car.dir == 0:
            cand = [k for k in self.calls if k[0] == f and self.allowed(car, k)]
            if not cand:
                return 0
            car.dir = min(cand, key=lambda k: self.calls[k].t_reg)[1]
        if not car.active:
            return 0
        event_time = self.t if event_time is None else event_time
        t0 = event_time + (0.0 if extra else C.T_DOOR_OPEN)
        n = 0
        for key in [k for k in self.calls if k[0] == f and k[1] == car.dir and self.allowed(car, k)]:
            call = self.calls[key]
            room = C.CAPACITY - car.load
            take = call.queue[:room]
            for p in take:
                n += 1
                p.t_board = t0 + (offset + n) * C.T_PASSENGER
                p.car = car.id
                car.passengers.append(p)
                car.car_calls.add(p.d)
            call.queue = call.queue[len(take):]
            if not call.queue:
                if call.car is not None:
                    self.cars[call.car].assigned.discard(key)
                del self.calls[key]
            else:
                self.unassign(key)          # 坐不下：按钮重新亮起，等待重新分配
            car.assigned.discard(key)
        if n and f in ENTRANCE_IDX and car.dir == 1:
            car.lobby_departures.append((self.t, car.load))
        return n
