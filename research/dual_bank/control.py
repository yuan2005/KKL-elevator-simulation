"""Public-clock controls; state gating reads each bank's local observation only."""
from research.dual_bank.timetable import clock_regime


def state_bin(observation):
    if observation["latched"] == 0 and observation["onboard"] == 0:
        return "clear"
    if observation["latched"] >= 3 or observation["onboard"] >= 16:
        return "busy"
    return "occupied"


def rule_key(regime, policy, observation):
    return "|".join((regime, policy, state_bin(observation)))


class FixedControl:
    interval = 30.

    def __init__(self, policy):
        self.policy = policy

    def update(self, sim, t):
        return self.policy


class DayControl:
    interval = 30.

    def __init__(self, initial, mapping, kind="naive", rules=None):
        if kind not in ("naive", "gated"):
            raise ValueError("Unknown daily control")
        self.initial, self.mapping, self.kind = initial, dict(mapping), kind
        self.rules = dict(rules or {})
        self.previous = None
        self.choices = {0: initial, 1: initial}
        self.log = []

    def update(self, sim, t):
        regime = clock_regime(t)
        if self.previous != regime and t < 86400:
            for bank in (0, 1):
                observation = sim.observable(bank)
                incumbent = self.choices[bank]
                key = rule_key(regime, incumbent, observation)
                choice = (self.mapping[regime] if self.kind == "naive"
                          else self.rules.get(key, incumbent))
                self.choices[bank] = choice
                self.log.append(dict(t=t, bank=bank, regime=regime, before=incumbent,
                                     choice=choice, observation=observation, key=key))
            self.previous = regime
        return self.choices.copy()


class BranchControl:
    interval = 30.

    def __init__(self, choices):
        self.choices = dict(choices)

    def update(self, sim, t):
        return self.choices.copy()
