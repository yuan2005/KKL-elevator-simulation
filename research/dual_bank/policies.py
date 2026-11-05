"""Bank-local dispatch projections and idle-car parking."""
import config as C
from building import HEIGHT, IDX, N_FLOORS, flight_time

WINDOW = 300.
SERVICE_CYCLE = C.T_DOOR_OPEN+C.T_DOOR_CLOSE+2*C.T_PASSENGER


def overflow_cycles(count, free):
    remainder = max(0, count-free)
    complete, rest = divmod(remainder, C.CAPACITY)
    return remainder+C.CAPACITY*complete*(complete-1)/2+complete*rest


def project(sim, car, keys):
    pending = {key: sim.calls[key] for key in keys if key in sim.calls}
    riders = [p.d for p in car.passengers]
    locations = [car.x, *(HEIGHT[p] for p in riders)]
    locations.extend(HEIGHT[key[0]] for key in pending)
    locations.extend(HEIGHT[p.d] for call in pending.values() for p in call.queue[:C.CAPACITY])
    low, high = min(locations), max(locations)
    direction = car.dir
    if direction == 0:
        destinations = [key[0] for key in pending]+riders
        if not destinations:
            return {}
        closest = min(destinations, key=lambda f: (abs(HEIGHT[f]-car.x), f))
        direction = 1 if HEIGHT[closest] >= car.x else -1
        if HEIGHT[closest] == car.x:
            direction = next((key[1] for key in pending if key[0] == closest), 1)
    position = car.x
    elapsed = car.dwell_left if car.state == "doors" else 0.
    estimates = {}
    roundtrip = 2*flight_time(HEIGHT[-1]-HEIGHT[0])+4*SERVICE_CYCLE+2*C.CAPACITY*C.T_PASSENGER
    for _ in range(3):
        end = high if direction > 0 else low
        floors = [f for f in range(N_FLOORS)
                  if (HEIGHT[f]-position)*direction >= -1e-8
                  and (end-HEIGHT[f])*direction >= -1e-8]
        floors.sort(reverse=direction < 0)
        for floor in floors:
            alight = riders.count(floor)
            calls = [key for key in pending if key[0] == floor and key[1] == direction]
            if not alight and not calls:
                continue
            elapsed += flight_time(abs(HEIGHT[floor]-position))
            position = HEIGHT[floor]
            riders = [d for d in riders if d != floor]
            boarded = 0
            for key in calls:
                call = pending.pop(key)
                n = len(call.queue)
                free = C.CAPACITY-len(riders)
                if sim.combo.bypass and len(riders) >= C.BYPASS_FRACTION*C.CAPACITY:
                    free = 0
                taken = min(n, free)
                service = (taken*(taken+1)/2
                           + max(0, n-taken)*(C.CAPACITY+1)/2)*C.T_PASSENGER/max(n, 1)
                estimates[key] = (elapsed+C.T_DOOR_OPEN+alight*C.T_PASSENGER+service
                                  + roundtrip*overflow_cycles(n, free)/max(n, 1))
                riders.extend(p.d for p in call.queue[:taken])
                boarded += taken
            elapsed += C.T_DOOR_OPEN+C.T_DOOR_CLOSE+(alight+boarded)*C.T_PASSENGER
        elapsed += flight_time(abs(end-position))
        position = end
        direction *= -1
    if pending:
        raise AssertionError("Projection failed to visit an assigned pickup")
    return estimates


class ModuleDispatcher:
    reassign = True

    def __init__(self, objective):
        self.objective = objective
        self.cache_time = None
        self.cache = {}

    def choose(self, sim, call):
        if sim.t != self.cache_time:
            self.cache_time, self.cache = sim.t, {}
        candidates = [car for car in sim.cars if car.active and car.state != "out"]
        if not candidates:
            return None
        scores = {}
        for car in candidates:
            base_keys = tuple(sorted(key for key in car.assigned if key != call.key))
            for keys in (base_keys, tuple(sorted((*base_keys, call.key)))):
                signature = (car.id, keys, tuple(len(sim.calls[k].queue) for k in keys))
                if signature not in self.cache:
                    self.cache[signature] = project(sim, car, keys)
            before = self.cache[(car.id, base_keys, tuple(len(sim.calls[k].queue) for k in base_keys))]
            new_keys = tuple(sorted((*base_keys, call.key)))
            after = self.cache[(car.id, new_keys, tuple(len(sim.calls[k].queue) for k in new_keys))]
            scores[car.id] = (
                after[call.key] if self.objective == "pickup" else
                len(call.queue)*after[call.key]+sum(
                    len(sim.calls[key].queue)*(after[key]-value) for key, value in before.items())
            )
        best = min(scores, key=lambda key: (scores[key], key))
        if call.car in scores and best != call.car:
            unit = len(call.queue) if self.objective == "marginal" else 1
            if (scores[call.car]-scores[best])/max(unit, 1) <= C.T_PASSENGER:
                best = call.car
        return best


class DistributedParking:
    def target(self, sim, car):
        n = sum(1 for c in sim.cars if c.active)
        homes = [IDX["G"]]
        if n > 1:
            homes.append(IDX["LG2"])
        k = n-len(homes)
        upper = list(range(IDX["1"], N_FLOORS))
        for i in range(k):
            homes.append(upper[int((i+.5)*len(upper)/k)])
        claimed = {}
        for other in sim.cars:
            if other is car or not other.active or other.load or other.assigned:
                continue
            floor = (other.parking if other.parking is not None else
                     other.floor if other.state in ("idle", "doors") else None)
            if floor is not None:
                claimed[floor] = claimed.get(floor, 0)+1
        free = [h for h in homes if claimed.get(h, 0) == 0] or homes
        return min(free, key=lambda h: abs(h-car.floor))


class DemandParking:
    def __init__(self):
        self.targets = None

    def target(self, sim, car):
        if self.targets is None:
            return DistributedParking().target(sim, car)
        return self.targets.get(car.id, car.floor)

    def update(self, sim, weights):
        free = [car for car in sim.cars if car.active and not car.passengers and not car.assigned]
        homes = {car.id: car.floor for car in free}
        total = sum(weights)
        if not total:
            self.targets = homes
            return
        for car in free:
            def cost(floor):
                locations = [floor, *(value for key, value in homes.items() if key != car.id)]
                return sum(weight*min(flight_time(abs(HEIGHT[origin]-HEIGHT[f])) for f in locations)
                           for origin, weight in enumerate(weights))/total
            best = min(range(N_FLOORS), key=lambda f: (cost(f), abs(HEIGHT[f]-car.x), f))
            if cost(car.floor)-cost(best) > C.T_PASSENGER:
                homes[car.id] = best
        self.targets = homes
