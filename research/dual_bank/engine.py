"""Two independent button banks sharing one physical passenger queue.

The eight controls change only dispatch, parking and bypass. Dispatch projections
reuse ModuleDispatcher with one synthetic request per illuminated button. Its
destination is the midpoint of the floors in the indicated direction, never a
waiting passenger's destination. Only own-car riders have known actual OD.

Controllers receive a detached view with ``t``, ``policy_names`` and
``observable(bank)``; neither future passengers nor physical queues are exposed.
Physical service uses the historical LOOK/boarding timing and exact motion.
``physical_board/alight`` denote membership transitions; service-record
``board/alight`` include the historical sequential door/passenger offsets.

``board_log`` records every boarding attempt, including additional service while
doors are already open. ``stop_log`` has one row per door session: its boarded,
calls and empty fields accumulate until ``closed`` is set. Only closed empty
sessions contribute to ``metrics()["empty_stops"]``.
"""
from copy import deepcopy
from itertools import product
from types import SimpleNamespace

import numpy as np

import config as C
from building import ENTRANCE_IDX, HEIGHT, N_FLOORS
from research.dual_bank.physics import PhysicalSim
from research.dual_bank.policies import DemandParking, DistributedParking, ModuleDispatcher, WINDOW
from sim import HallCall


BANKS = {0: (0, 1, 2), 1: (3, 4, 5)}
DEFAULT_POLICY = "pickup_uniform_b0"


def configurations():
    """Return fresh settings for exactly the eight predeclared candidates."""
    return {
        f"{dispatch}_{parking}_b{int(bypass)}": {
            "dispatch": dispatch, "parking": parking, "bypass": bypass}
        for dispatch, parking, bypass in product(
            ("pickup", "marginal"), ("uniform", "demand"), (False, True))
    }


class _ControllerView:
    def __init__(self, sim):
        self.t = sim.t
        self.policy_names = dict(sim.policy_names)
        self._observations = {bank: sim.observable(bank) for bank in BANKS}

    def observable(self, bank):
        return deepcopy(self._observations[bank])


class _LocalParking:
    def target(self, sim, car):
        bank = car.id // 3
        view = sim._local_view(bank)
        own = next(c for c in view.cars if c.id == car.id)
        return sim._parkings[bank].target(view, own)


class DualBankSim(PhysicalSim):
    """``DualBankSim(passengers, controller, gen_end)``.

    ``controller.update(view,t)`` returns a configuration string (both banks)
    or a partial ``{bank: name}`` mapping. Its optional ``interval`` defaults to
    30 seconds. Inputs are copied, sorted by arrival/id and must be fresh.
    ``step()`` advances one absolute-time DT; replacing ``controller`` on a
    deepcopy requires no reset. Callback deadlines remain absolute.
    """

    def __init__(self, passengers, controller, gen_end):
        passengers = sorted(deepcopy(list(passengers)), key=lambda p: (p.t, p.id))
        if len({p.id for p in passengers}) != len(passengers):
            raise ValueError("Passenger IDs must be unique")
        for p in passengers:
            if (not np.isfinite(p.t) or p.t < 0 or p.o == p.d
                    or not 0 <= p.o < N_FLOORS or not 0 <= p.d < N_FLOORS):
                raise ValueError("Invalid passenger arrival or OD")
            if p.t_board is not None or p.t_alight is not None or p.car is not None:
                raise ValueError("Passenger inputs must be unserved")
        if not np.isfinite(gen_end) or gen_end < 0:
            raise ValueError("gen_end must be finite and nonnegative")
        super().__init__(passengers, controller, gen_end, n_cars=6)
        self.waiting = {}
        self.policy_names = {}
        self._settings = {}
        self._dispatchers = {}
        self._parkings = {}
        self._parking_dirty = set()
        self.button_history = {bank: [] for bank in BANKS}
        self.latch_log = []
        self.policy_log = []
        self.decision_log = []
        self.board_log = []
        self.stop_log = []
        self._open_stops = {}
        self.next_parking = 0.
        self.combo = SimpleNamespace(parking=_LocalParking())
        self.set_policy(DEFAULT_POLICY)

    def set_combo(self, combo):
        raise TypeError("Use set_policy(name, bank=None), not shared-call set_combo")

    def set_policy(self, name, bank=None):
        """Change control modules without touching calls or physical state."""
        settings = configurations()
        if name not in settings:
            raise ValueError(f"Unknown policy: {name}")
        if bank is not None and bank not in BANKS:
            raise ValueError(f"Unknown bank: {bank}")
        for local in BANKS if bank is None else (bank,):
            previous = self.policy_names.get(local)
            if previous == name:
                continue
            setting = settings[name]
            self._settings[local] = setting
            self.policy_names[local] = name
            self._dispatchers[local] = ModuleDispatcher(setting["dispatch"])
            self._parkings[local] = (
                DemandParking() if setting["parking"] == "demand" else DistributedParking())
            self.policy_log.append(dict(t=self.t, bank=local, previous=previous, policy=name))
            self.combo_log.append((self.t, local, name))
            self._parking_dirty.add(local)
            self.continuity_checks += 1

    def allowed(self, car, key):
        return key[2] == car.id // 3

    def bypass_full(self, car):
        bypass = self._settings[car.id // 3]["bypass"]
        return bypass and car.load >= C.BYPASS_FRACTION * C.CAPACITY

    def observer(self, bank):
        """Detached dispatch input: own cars, binary calls and own presses only."""
        view = deepcopy(self._local_view(bank))
        view.button_history = deepcopy(self.button_history[bank])
        return view

    def _local_view(self, bank):
        if bank not in BANKS:
            raise ValueError(f"Unknown bank: {bank}")
        return SimpleNamespace(
            t=self.t,
            cars=[c for c in self.cars if c.id in BANKS[bank]],
            calls={k: call for k, call in self.calls.items() if k[2] == bank},
            combo=SimpleNamespace(bypass=self._settings[bank]["bypass"]))

    def observable(self, bank):
        """Local policy features; no remote/physical queues, OD or future stream."""
        if bank not in BANKS:
            raise ValueError(f"Unknown bank: {bank}")
        own = [c for c in self.cars if c.id in BANKS[bank]]
        calls = [
            dict(key=k, t_reg=call.t_reg, car=call.car)
            for k, call in sorted(self.calls.items()) if k[2] == bank]
        presses = [event for event in self.button_history[bank]
                   if self.t-WINDOW <= event["t"] <= self.t]
        return deepcopy(dict(
            t=self.t, bank=bank, policy=self.policy_names[bank],
            latched=len(calls), onboard=sum(c.load for c in own),
            idle=sum(c.state == "idle" for c in own),
            moving=sum(c.state == "moving" for c in own),
            doors=sum(c.state == "doors" for c in own),
            latched_calls=calls, button_history=presses,
            cars=[dict(id=c.id, x=c.x, v=c.v, floor=c.floor, direction=c.dir,
                       state=c.state, target=c.target, load=c.load,
                       dwell_left=c.dwell_left, assigned=sorted(c.assigned),
                       destinations=sorted(c.car_calls)) for c in own]))

    def _press(self, bank, floor, direction, source):
        key = (floor, direction, bank)
        self.button_history[bank].append(
            dict(t=self.t, floor=floor, direction=direction, source=source))
        if key in self.calls:
            return
        call = HallCall(key, self.t)
        # A fixed, directional expected OD; no physical passenger object is cloned.
        destination = ((floor+N_FLOORS)//2 if direction > 0 else (floor-1)//2)
        call.queue = [SimpleNamespace(o=floor, d=destination, t=self.t)]
        self.calls[key] = call
        self.latch_log.append(dict(t=self.t, bank=bank, key=key, action="set", source=source))
        self.assign(call)

    def _register(self, passenger, assign=True):
        if passenger.id in self.registration:
            raise AssertionError("A physical arrival was registered twice")
        self.registration[passenger.id] = self.t
        self.waiting.setdefault((passenger.o, passenger.dir), []).append(passenger)
        for bank in BANKS:
            self._press(bank, passenger.o, passenger.dir, "arrival")

    def assign(self, call):
        bank = call.key[2]
        view = self._local_view(bank)
        dispatcher = self._dispatchers[bank]
        # Base projection cache omits rider state, which may change within one DT.
        dispatcher.cache_time = None
        best = dispatcher.choose(view, view.calls[call.key])
        if best is not None and best not in BANKS[bank]:
            raise AssertionError("A call escaped its local bank")
        old = call.car
        if old is not None:
            self.cars[old].assigned.discard(call.key)
        call.car = best
        if best is not None:
            self.cars[best].assigned.add(call.key)
        self.decision_log.append(dict(
            t=self.t, bank=bank, policy=self.policy_names[bank], key=call.key,
            previous=old, car=best))

    def _clear(self, key, car):
        self.unassign(key)
        del self.calls[key]
        self.latch_log.append(
            dict(t=self.t, bank=key[2], key=key, action="clear", car=car.id))

    def _record_board(self, car, floor, extra, offset, passengers=(), cleared=()):
        session = self._open_stops.get(car.id) if extra else None
        self.board_log.append(dict(
            t=self.t, car=car.id, bank=car.id//3, floor=floor,
            direction=car.dir, boarded=len(passengers),
            passenger_ids=[p.id for p in passengers], cleared=list(cleared),
            extra=extra, offset=offset, stop=session))
        if session is not None:
            stop = self.stop_log[session]
            stop["boarded"] += len(passengers)
            stop["calls"].extend(cleared)
            stop["empty"] = not stop["boarded"] and not stop["alighted"]
            if not passengers:
                # Base LOOK ends the session immediately after a zero-board
                # closing attempt; positive additional boarding extends dwell.
                stop["closed"] = self.t
                del self._open_stops[car.id]
        if extra and not passengers and (
                car.load >= C.CAPACITY or self.bypass_full(car)):
            # A repressed call can return to this full or bypassing car.
            # Release only the assignment so LOOK departs instead of reopening.
            for key in list(car.assigned):
                if key[0] == floor:
                    self.unassign(key)
        return len(passengers)

    def board(self, car, floor, extra, offset=0):
        bank = car.id // 3
        here = [k for k in self.calls if k[0] == floor and k[2] == bank]
        if car.dir == 0:
            if not here:
                return self._record_board(car, floor, extra, offset)
            car.dir = min(here, key=lambda k: (self.calls[k].t_reg, k))[1]
        key = (floor, car.dir, bank)
        if not car.active or key not in self.calls:
            return self._record_board(car, floor, extra, offset)
        queue_key = (floor, car.dir)
        queue = self.waiting.get(queue_key, [])
        take = queue[:max(0, C.CAPACITY-car.load)]
        t0 = self.t + (0. if extra else C.T_DOOR_OPEN)
        for index, p in enumerate(take, 1):
            if p.id in self.physical_board or p.t > self.t+1e-8:
                raise AssertionError("Duplicate or pre-arrival physical boarding")
            p.t_board = t0+(offset+index)*C.T_PASSENGER
            p.car = car.id
            car.passengers.append(p)
            car.car_calls.add(p.d)
            self.physical_board[p.id] = self.t
        remaining = queue[len(take):]
        if remaining:
            self.waiting[queue_key] = remaining
        else:
            self.waiting.pop(queue_key, None)
        # Only this bank's own open/stop can clear its lamp, even when empty/full.
        self._clear(key, car)
        if remaining:
            self._press(bank, floor, car.dir, "repress")
        if take and floor in ENTRANCE_IDX and car.dir == 1:
            car.lobby_departures.append((self.t, car.load))
        return self._record_board(car, floor, extra, offset, take, (key,))

    def stop_here(self, car, forced):
        before_board = len(self.physical_board)
        before_alight = len(self.physical_alight)
        before_log = len(self.latch_log)
        before_attempt = len(self.board_log)
        super().stop_here(car, forced)
        serviced = [event["key"] for event in self.latch_log[before_log:]
                    if event["action"] == "clear"]
        boarded = len(self.physical_board)-before_board
        alighted = len(self.physical_alight)-before_alight
        # Historical Sim suppresses a forced empty opening; bank ghost calls must
        # incur the complete open/close cycle, not vanish on a position match.
        if serviced and car.state != "doors":
            car.state = "doors"
            car.dwell_left = C.T_DOOR_OPEN+C.T_DOOR_CLOSE
        if car.state == "doors":
            session = len(self.stop_log)
            self.stop_log.append(dict(
                t=self.t, bank=car.id//3, car=car.id, floor=car.floor,
                direction=car.dir, boarded=boarded, alighted=alighted,
                empty=not boarded and not alighted, calls=serviced,
                initial_boarded=boarded, initial_empty=not boarded and not alighted,
                closed=None))
            self._open_stops[car.id] = session
            for attempt in self.board_log[before_attempt:]:
                attempt["stop"] = session

    def _update_parking(self, banks=None):
        banks = tuple(BANKS if banks is None else banks)
        for bank in banks:
            parking = self._parkings[bank]
            if not isinstance(parking, DemandParking):
                continue
            weights = [0.] * N_FLOORS
            for press in self.button_history[bank]:
                if self.t-WINDOW <= press["t"] <= self.t:
                    weights[press["floor"]] += 1.
            parking.update(self._local_view(bank), weights)
        self._parking_dirty.difference_update(banks)

    def step(self):
        if self.t >= self.next_ctrl:
            if self.controller is not None:
                choice = self.controller.update(_ControllerView(self), self.t)
                if isinstance(choice, str):
                    self.set_policy(choice)
                elif isinstance(choice, dict):
                    for bank, name in sorted(choice.items()):
                        self.set_policy(name, bank=bank)
                else:
                    raise TypeError("controller.update must return a name or {bank: name}")
            interval = getattr(self.controller, "interval", 30.)
            if not np.isfinite(interval) or interval <= 0:
                raise ValueError("Controller interval must be positive and finite")
            self.next_ctrl += interval
        while self.i_next < len(self.pax) and self.pax[self.i_next].t <= self.t:
            self._register(self.pax[self.i_next])
            self.i_next += 1
        if self.t >= self.next_parking:
            self._update_parking()
            self.next_parking = self.t+60.
        elif self._parking_dirty:
            self._update_parking(sorted(self._parking_dirty))
        if self.t >= self.next_reassign:
            for call in list(self.calls.values()):
                if call.key in self.calls and (call.car is None or not self.committed(call)):
                    self.assign(call)
            self.next_reassign = self.t+C.REASSIGN_INTERVAL
        for car in self.cars:
            self.update_car(car)
        self.t += C.DT
        if self.t >= self.next_check:
            self.check_conservation()
            self.next_check = self.t+60.
        return self

    def check_conservation(self):
        waiting = [p.id for queue in self.waiting.values() for p in queue]
        onboard = [p.id for car in self.cars for p in car.passengers]
        physical = waiting+onboard+list(self.physical_alight)
        released = {p.id for p in self.pax[:self.i_next]}
        if len(physical) != len(set(physical)) or set(physical) != released:
            raise AssertionError("Passenger conservation failed")
        if set(self.physical_board) != set(onboard) | set(self.physical_alight):
            raise AssertionError("Board/alight membership failed")
        for car in self.cars:
            if not 0 <= car.load <= C.CAPACITY:
                raise AssertionError("Capacity invariant failed")
            if (not HEIGHT[0]-1e-7 <= car.x <= HEIGHT[-1]+1e-7
                    or not 0 <= car.v <= C.RATED_SPEED+1e-8):
                raise AssertionError("Motion bounds failed")
            if any(k not in self.calls or not self.allowed(car, k)
                   or self.calls[k].car != car.id for k in car.assigned):
                raise AssertionError("Local assignment invariant failed")
        for key, call in self.calls.items():
            if len(call.queue) != 1:
                raise AssertionError("A binary latch must have one synthetic request")
            if call.car is not None and (
                    call.car not in BANKS[key[2]] or key not in self.cars[call.car].assigned):
                raise AssertionError("Local call ownership invariant failed")
        for floor, direction in self.waiting:
            if any((floor, direction, bank) not in self.calls for bank in BANKS):
                raise AssertionError("Waiting passengers must keep both banks repressed")
        self.conservation_checks += 1

    def run(self):
        deadline = max(self.gen_end, self.pax[-1].t if self.pax else 0.)+C.DRAIN_TIME
        while self.t < deadline:
            complete = self.i_next == len(self.pax) and all(
                p.t_alight is not None and p.t_alight <= self.t for p in self.pax)
            if (self.t > 0 and self.t >= self.gen_end and complete and not self.calls and not self.waiting
                    and all(not c.passengers and c.state != "doors" for c in self.cars)):
                break
            self.step()
        self.check_conservation()
        return self

    def snapshot(self):
        """Detached full audit state, never passed to a daily controller."""
        return deepcopy(dict(
            t=self.t, i_next=self.i_next, policy_names=self.policy_names,
            cars=[vars(c) for c in self.cars], calls=self.calls, waiting=self.waiting,
            registration=self.registration, physical_board=self.physical_board,
            physical_alight=self.physical_alight, button_history=self.button_history,
            door_sessions={car: self.stop_log[index] for car, index in self._open_stops.items()},
            next_ctrl=self.next_ctrl, next_reassign=self.next_reassign,
            next_parking=self.next_parking))

    def state_record(self):
        """Canonical JSON-serializable audit record for same-state branching."""
        return deepcopy(dict(
            t=self.t, i_next=self.i_next, gen_end=self.gen_end,
            policy_names=dict(self.policy_names),
            cars=[
                dict(id=c.id, x=c.x, v=c.v, floor=c.floor, direction=c.dir,
                     target=c.target, state=c.state, parking=c.parking,
                     active=c.active, dwell_left=c.dwell_left,
                     distance=c.distance, starts=c.starts, active_time=c.active_time,
                     riders=[dict(id=p.id, origin=p.o, destination=p.d,
                                  board=p.t_board, alight=p.t_alight) for p in c.passengers],
                     car_calls=sorted(c.car_calls), assigned=sorted(c.assigned))
                for c in self.cars],
            waiting=[dict(floor=f, direction=d, ids=[p.id for p in queue])
                     for (f, d), queue in sorted(self.waiting.items())],
            latches=[
                dict(key=key, car=call.car, t_reg=call.t_reg)
                for key, call in sorted(self.calls.items())],
            observations={bank: self.observable(bank) for bank in BANKS},
            button_history=self.button_history,
            physical_board=sorted(self.physical_board.items()),
            physical_alight=sorted(self.physical_alight.items()),
            registration=sorted(self.registration.items()),
            door_sessions=[
                dict(stop=index, **self.stop_log[index])
                for car, index in sorted(self._open_stops.items())],
            parking_targets={bank: deepcopy(getattr(parking, "targets", None))
                             for bank, parking in sorted(self._parkings.items())},
            parking_dirty=sorted(self._parking_dirty),
            next_ctrl=self.next_ctrl, next_reassign=self.next_reassign,
            next_parking=self.next_parking, next_check=self.next_check))

    def metrics(self):
        """Served-cohort seconds; LWT is percent above C.LONG_WAIT.

        ``incomplete`` counts not-yet-alighted inputs, ``unserved`` not-yet-boarded;
        absent sample means/percentiles are None, never a fabricated zero.
        """
        waits = [p.t_board-p.t for p in self.pax
                 if p.t_board is not None and p.t_board <= self.t]
        rides = [p.t_alight-p.t_board for p in self.pax
                 if p.t_alight is not None and p.t_alight <= self.t]
        return dict(
            n=len(self.pax), awt=float(np.mean(waits)) if waits else None,
            p90=float(np.percentile(waits, 90)) if waits else None,
            lwt=float(np.mean(np.asarray(waits) > C.LONG_WAIT)*100) if waits else None,
            att=float(np.mean(rides)) if rides else None,
            incomplete=len(self.pax)-len(rides), unserved=len(self.pax)-len(waits),
            empty_stops=sum(row["empty"] and row["closed"] is not None
                            for row in self.stop_log),
            completed_stops=sum(row["closed"] is not None for row in self.stop_log),
            open_stops=len(self._open_stops),
            stops=len(self.stop_log), ghost_calls=len(self.calls), finished=self.t,
            distance=sum(car.distance for car in self.cars),
            switches=sum(row["previous"] is not None for row in self.policy_log))
