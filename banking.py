"""Independent three-car banks with shared people, not shared hall-call lamps.

The legacy C0-C7 controls run within each bank. Passengers press both banks;
only a bank's own service clears its calls. Sector-specific calls remain
separate within a bank. Physical boarding is FIFO among eligible passengers.
Exact motion preserves acceleration limits. Door service has separate service
and closing phases; boarding during closing incurs full reopening and another
closing cycle. Parameter values are unchanged. Sim remains available for legacy
coordinated runs.
"""
from copy import deepcopy
from math import isfinite
from types import SimpleNamespace

import config as C
from building import Car, ENTRANCE_IDX, HEIGHT, advance_motion
from sim import HallCall, Sim


class BankCar(Car):
    def can_stop_at(self, floor):
        if self.state != "moving":
            return True
        remaining = (HEIGHT[floor]-self.x)*self.dir
        return remaining >= -1e-8 and remaining+1e-8 >= self.v*self.v/(2*C.ACCEL)


class DualBankSim(Sim):
    BANKS = ((0, 1, 2), (3, 4, 5))

    def __init__(self, passengers, controller, gen_end, broken_cars=(), n_cars=C.N_CARS):
        if n_cars != 6:
            raise ValueError("Independent-bank model requires exactly two banks of three cars")
        super().__init__(passengers, controller, gen_end, broken_cars, n_cars)
        self.cars = [BankCar(car.id, car.floor) for car in self.cars]
        self.bank_of = {car: bank for bank, cars in enumerate(self.BANKS) for car in cars}
        self.waiting = {}
        self.bank_combos = {}
        self.empty_stops = 0
        self.completed_stops = 0
        self._sessions = {}
        self._door_deadlines = {}
        self.reopenings = 0
        self._cleared = 0
        self._planning = False
        self._motion_plans = {}
        self._pending_arrivals = set()
        self._needs_plan = True
        ids = [p.id for p in passengers]
        if len(set(ids)) != len(ids):
            raise ValueError("Physical passenger IDs must be unique")
        if any(p.t_board is not None or p.t_alight is not None for p in passengers):
            raise ValueError("Passenger inputs must be unserved")

    def local_view(self, bank):
        return SimpleNamespace(
            t=self.t, cars=[self.cars[i] for i in self.BANKS[bank]],
            calls={k: call for k, call in self.calls.items() if k[2][0] == bank},
            broken=self.broken, combo=self.bank_combos[bank], allowed=self.allowed,
            bypass_full=self.bypass_full,
        )

    def run(self):
        last_arrival = self.pax[-1].t if self.pax else 0
        deadline = max(self.gen_end, last_arrival)+C.DRAIN_TIME
        while self.t < deadline:
            if (self.i_next == len(self.pax) and self.t > self.gen_end
                    and not self.waiting and not self.calls and not self._sessions
                    and all(not car.load for car in self.cars)):
                break
            self.step()
        return self

    def step(self):
        if not isfinite(C.DT) or C.DT <= 0:
            raise ValueError("Integration time cap must be positive")
        t = self.t
        control_due = t+1e-9 >= self.next_ctrl
        reassign_due = t+1e-9 >= self.next_reassign
        registered = False
        if control_due:
            new = self.controller.update(self, t)
            if new is not None and new is not self.combo:
                self.set_combo(new)
            self.next_ctrl += self.controller.interval
        while self.i_next < len(self.pax) and self.pax[self.i_next].t <= t+1e-9:
            self._register(self.pax[self.i_next])
            self.i_next += 1
            registered = True
        if reassign_due:
            reassign = self.combo.dispatcher.reassign
            for call in list(self.calls.values()):
                if (call.car is None or
                        (reassign and not self.committed(call)) or
                        not self.cars[call.car].active):
                    self.assign(call)
            self.next_reassign += C.REASSIGN_INTERVAL
        door_due = any(ready <= t+1e-9 for _, ready in self._door_deadlines.values())
        if self._needs_plan or control_due or reassign_due or registered or door_due or self._pending_arrivals:
            # Planning changes state only at the current event time. No car may
            # reserve people at a future arrival while another car is still earlier.
            self._planning = True
            try:
                for car in self.cars:
                    active_time = car.active_time
                    if car.id in self._pending_arrivals:
                        self.arrive(car)
                    self.update_car(car)
                    car.active_time = active_time
            finally:
                self._planning = False
            self._pending_arrivals.clear()
            self._needs_plan = False
            self._motion_plans.clear()
            for car in self.cars:
                if car.state == "moving":
                    remaining = (HEIGHT[car.target]-car.x)*car.dir
                    upper = remaining/C.RATED_SPEED+2*C.RATED_SPEED/C.ACCEL+1
                    _, _, _, duration = advance_motion(remaining, car.v, upper)
                    if duration == 0:
                        car.x, car.v = HEIGHT[car.target], 0.
                        self._pending_arrivals.add(car.id)
                    else:
                        self._motion_plans[car.id] = (t, car.x, car.v, car.dir, car.target, t+duration)
            if self._pending_arrivals:
                return

        events = [t+C.DT, self.next_ctrl, self.next_reassign]
        if self.i_next < len(self.pax):
            events.append(self.pax[self.i_next].t)
        events.extend(ready for _, ready in self._door_deadlines.values())
        events.extend(plan[5] for plan in self._motion_plans.values())
        future = [when for when in events if when > t]
        if not future:
            raise RuntimeError("No future simulation event")
        next_time = min(future)
        for car in self.cars:
            if car.state != "out":
                car.active_time += next_time-t
            if car.state == "moving":
                start, position, speed, direction, target, arrival = self._motion_plans[car.id]
                remaining = (HEIGHT[target]-position)*direction
                # Evaluate from the last event anchor, not by accumulating
                # mesh-dependent roundoff across optional integration slices.
                moved, velocity, arrived, _ = advance_motion(remaining, speed, next_time-start)
                new_position = position+direction*moved
                car.distance += abs(new_position-car.x)
                car.x, car.v = new_position, velocity
                if arrived:
                    if abs(next_time-arrival) > 1e-8:
                        raise AssertionError("Motion arrival was processed out of chronological order")
                    car.x, car.v = HEIGHT[target], 0.
                    self._pending_arrivals.add(car.id)
            elif car.state == "doors":
                phase, ready = self._door_deadlines[car.id]
                car.dwell_left = max(0., ready-next_time)+(C.T_DOOR_CLOSE if phase == "service" else 0)
        self.t = next_time

    def set_combo(self, combo):
        self._needs_plan = True
        self.combo = combo
        self.combo_log.append((self.t, combo.name))
        for bank in range(len(self.BANKS)):
            self.bank_combos[bank] = deepcopy(combo)
            view = self.local_view(bank)
            view.combo.setup(view)
            usable = [car.id for car in view.cars if car.id not in self.broken]
            if combo.active_cars is not None:
                usable = usable[:combo.active_cars]
            for car in view.cars:
                car.active = car.id in usable
                if car.state == "out" and car.active:
                    car.state = "idle"
                car.assigned.clear()
        # Preserve outstanding lamps, including calls whose passengers left
        # in the other bank. Changing strategy is not a remote cancellation.
        for call in self.calls.values():
            call.car = None
            self.assign(call)

    def _press(self, bank, passenger, assign=True):
        tag = self.bank_combos[bank].tag(self.local_view(bank), passenger)
        key = (passenger.o, passenger.dir, (bank, tag))
        if key not in self.calls:
            self.calls[key] = HallCall(key, self.t)
            if assign:
                self.assign(self.calls[key])

    def _register(self, passenger, assign=True):
        self._needs_plan = True
        self.waiting.setdefault((passenger.o, passenger.dir), []).append(passenger)
        for bank in range(len(self.BANKS)):
            self._press(bank, passenger, assign)

    def allowed(self, car, key):
        bank, tag = key[2]
        return (self.bank_of[car.id] == bank and
                (not self.bank_combos[bank].up_sectors or tag is None or tag == car.up_sector))

    def assign(self, call):
        bank = call.key[2][0]
        old = call.car
        best = self.bank_combos[bank].dispatcher.choose(self.local_view(bank), call)
        if old is not None and old != best:
            self.cars[old].assigned.discard(call.key)
        if best is not None and self.bank_of[best] != bank:
            raise AssertionError("Dispatcher attempted a cross-bank assignment")
        call.car = best
        if best is not None:
            self.cars[best].assigned.add(call.key)

    def parking_target(self, car):
        bank = self.bank_of[car.id]
        return self.bank_combos[bank].parking.target(self.local_view(bank), car)

    def move(self, car):
        if self._planning:
            return
        remaining = (HEIGHT[car.target]-car.x)*car.dir
        distance, velocity, arrived, elapsed = advance_motion(remaining, car.v, C.DT)
        car.distance += distance
        car.x += car.dir*distance
        car.v = velocity
        if arrived:
            original_t = self.t
            self.t += elapsed
            try:
                self.arrive(car)
            finally:
                self.t = original_t

    def update_car(self, car):
        if car.state == "doors":
            phase, ready = self._door_deadlines[car.id]
            if phase == "closing" and self.t+1e-9 < ready:
                count = self.board(car, car.floor, extra=True, reopen=True)
                if count:
                    self.reopenings += 1
                    phase, ready = "service", self.t+C.T_DOOR_OPEN+count*C.T_PASSENGER
                    self._door_deadlines[car.id] = (phase, ready)
            if self.t+1e-9 < ready:
                car.dwell_left = ready-self.t+(C.T_DOOR_CLOSE if phase == "service" else 0)
                car.active_time += C.DT
                return
            if phase == "service":
                count = self.board(car, car.floor, extra=True)
                if count:
                    self._door_deadlines[car.id] = ("service", self.t+count*C.T_PASSENGER)
                    car.dwell_left = count*C.T_PASSENGER+C.T_DOOR_CLOSE
                else:
                    self._door_deadlines[car.id] = ("closing", self.t+C.T_DOOR_CLOSE)
                    car.dwell_left = C.T_DOOR_CLOSE
                car.active_time += C.DT
                return
            self.completed_stops += 1
            self.empty_stops += self._sessions.pop(car.id) == 0
            del self._door_deadlines[car.id]
            for key in list(car.assigned):
                if key[0] == car.floor:
                    self.unassign(key)
            car.dwell_left = 0
            car.state = "idle"
        super().update_car(car)

    def board(self, car, floor, extra, offset=0, *, reopen=False):
        here = [key for key in self.calls if key[0] == floor and self.allowed(car, key)]
        if car.dir == 0 and here:
            car.dir = min(here, key=lambda key: self.calls[key].t_reg)[1]
        own_calls = [key for key in here if key[1] == car.dir]
        boarded = []
        bank = self.bank_of[car.id]
        queue_key = (floor, car.dir)
        queue = self.waiting.get(queue_key, [])
        if car.active and own_calls:
            combo = self.bank_combos[bank]
            eligible = [p for p in queue if
                        combo.tag(self.local_view(bank), p) in (None, car.up_sector)]
            room = 0 if self.bypass_full(car) else max(0, C.CAPACITY-car.load)
            boarded = eligible[:room]
            if reopen and not boarded:
                return 0
            t0 = self.t+(C.T_DOOR_OPEN if not extra or reopen else 0)
            for index, passenger in enumerate(boarded, 1):
                if passenger.t_board is not None or passenger.t > self.t:
                    raise AssertionError("Duplicate or pre-arrival physical boarding")
                passenger.t_board = t0+(offset+index)*C.T_PASSENGER
                passenger.car = car.id
                car.passengers.append(passenger)
                car.car_calls.add(passenger.d)
            taken = {p.id for p in boarded}
            remaining = [p for p in queue if p.id not in taken]
            if remaining:
                self.waiting[queue_key] = remaining
            else:
                self.waiting.pop(queue_key, None)
            for key in own_calls:
                self.unassign(key)
                del self.calls[key]
                self._cleared += 1
            # Waiting people can press this bank again. The other bank's
            # lamps and assignments have not been touched by the pickup.
            for passenger in remaining:
                self._press(bank, passenger)
        if boarded and floor in ENTRANCE_IDX and car.dir == 1:
            car.lobby_departures.append((self.t, car.load))
        if extra:
            if car.id in self._sessions:
                self._sessions[car.id] += len(boarded)
            if not boarded and not reopen:
                for key in list(car.assigned):
                    if key[0] == floor:
                        self.unassign(key)
        return len(boarded)

    def stop_here(self, car, forced):
        cleared = self._cleared
        alighted = sum(p.d == car.floor for p in car.passengers)
        before = car.load
        super().stop_here(car, forced)
        boarded = car.load-before+alighted
        if self._cleared != cleared and car.state != "doors":
            car.state = "doors"
            car.dwell_left = C.T_DOOR_OPEN+C.T_DOOR_CLOSE
        if car.state == "doors":
            self._sessions[car.id] = boarded+alighted
            self._door_deadlines[car.id] = (
                "service", self.t+C.T_DOOR_OPEN+(boarded+alighted)*C.T_PASSENGER)
