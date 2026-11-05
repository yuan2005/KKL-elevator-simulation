"""Exact piecewise acceleration and passenger service accounting."""
import math

import config as C
from building import Car, HEIGHT
from sim import Sim


def advance(distance, speed, dt, acceleration=C.ACCEL, limit=C.RATED_SPEED):
    if distance < -1e-8 or speed < 0 or speed > limit+1e-8 or dt <= 0:
        raise ValueError("Invalid motion state")
    distance = max(0., distance)
    if distance+1e-8 < speed*speed/(2*acceleration):
        raise ValueError("Target is inside the physical stopping distance")
    if distance <= 1e-10:
        return distance, 0., True, 0.
    peak = min(limit, math.sqrt(acceleration*distance+speed*speed/2))
    peak = max(speed, peak)
    accelerating = max(0., (peak-speed)/acceleration)
    used = (peak*peak-speed*speed)/(2*acceleration)+peak*peak/(2*acceleration)
    cruise = max(0., distance-used)/peak
    braking = peak/acceleration
    duration = accelerating+cruise+braking
    if dt >= duration-1e-10:
        return distance, 0., True, duration
    elapsed, moved, velocity = 0., 0., speed
    for length, a in ((accelerating, acceleration), (cruise, 0.), (braking, -acceleration)):
        take = min(length, dt-elapsed)
        moved += velocity*take+.5*a*take*take
        velocity += a*take
        elapsed += take
        if elapsed >= dt-1e-12:
            break
    return min(distance, max(0., moved)), max(0., velocity), False, dt


class PhysicalCar(Car):
    def can_stop_at(self, floor):
        if self.state != "moving":
            return True
        remaining = (HEIGHT[floor]-self.x)*self.dir
        return remaining >= -1e-8 and remaining+1e-8 >= self.v*self.v/(2*C.ACCEL)


class PhysicalSim(Sim):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.cars = [PhysicalCar(c.id, c.floor) for c in self.cars]
        self.registration = {}
        self.physical_board = {}
        self.physical_alight = {}
        self.next_check = 0.
        self.conservation_checks = 0
        self.continuity_checks = 0

    def _register(self, passenger, assign=True):
        self.registration.setdefault(passenger.id, self.t)
        return super()._register(passenger, assign=assign)

    def board(self, car, floor, extra, offset=0):
        previous = {p.id for p in car.passengers}
        count = super().board(car, floor, extra, offset)
        for passenger in car.passengers:
            if passenger.id not in previous:
                if passenger.id in self.physical_board or passenger.t > self.t+1e-8:
                    raise AssertionError("Duplicate or pre-arrival physical boarding")
                self.physical_board[passenger.id] = self.t
        return count

    def stop_here(self, car, forced):
        previous = {p.id for p in car.passengers}
        super().stop_here(car, forced)
        for identifier in previous-{p.id for p in car.passengers}:
            if identifier in self.physical_alight:
                raise AssertionError("Duplicate physical alighting")
            self.physical_alight[identifier] = self.t

    def set_combo(self, combo):
        before = [(c.x, c.v, c.dir, c.target, c.state,
                   tuple(p.id for p in c.passengers), c.dwell_left) for c in self.cars]
        waiting = sorted(p.id for call in self.calls.values() for p in call.queue)
        super().set_combo(combo)
        after = [(c.x, c.v, c.dir, c.target, c.state,
                  tuple(p.id for p in c.passengers), c.dwell_left) for c in self.cars]
        if before != after or waiting != sorted(p.id for call in self.calls.values() for p in call.queue):
            raise AssertionError("Module change reset physical or passenger state")
        self.continuity_checks += 1

    def move(self, car):
        remaining = (HEIGHT[car.target]-car.x)*car.dir
        distance, velocity, arrived, elapsed = advance(remaining, car.v, C.DT)
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
            if car.state == "doors":
                car.dwell_left += elapsed
        elif not car.can_stop_at(car.target):
            raise AssertionError("Committed target lost stopping feasibility")

    def check_conservation(self):
        waiting = [p.id for call in self.calls.values() for p in call.queue]
        onboard = [p.id for car in self.cars for p in car.passengers]
        finished = list(self.physical_alight)
        physical = waiting+onboard+finished
        released = {p.id for p in self.pax[:self.i_next]}
        if len(physical) != len(set(physical)) or set(physical) != released:
            raise AssertionError("Passenger conservation failed")
        for car in self.cars:
            if not 0 <= car.load <= C.CAPACITY:
                raise AssertionError("Capacity invariant failed")
            if not HEIGHT[0]-1e-7 <= car.x <= HEIGHT[-1]+1e-7 or car.v > C.RATED_SPEED+1e-8:
                raise AssertionError("Motion bounds failed")
        self.conservation_checks += 1

    def step(self):
        super().step()
        if self.t >= self.next_check:
            self.check_conservation()
            self.next_check += 60.

    def run(self):
        deadline = max(self.gen_end, self.pax[-1].t if self.pax else 0.)+C.DRAIN_TIME
        while self.t < deadline:
            complete = self.i_next == len(self.pax) and all(
                p.t_alight is not None and p.t_alight <= self.t for p in self.pax)
            if self.t > self.gen_end and complete:
                break
            self.step()
        self.check_conservation()
        return self

    def service_records(self):
        return [dict(id=p.id, arrival=p.t, origin=p.o, destination=p.d,
                     board=p.t_board, alight=p.t_alight, car=p.car,
                     physical_board=self.physical_board.get(p.id),
                     physical_alight=self.physical_alight.get(p.id),
                     registration=self.registration.get(p.id)) for p in self.pax]
