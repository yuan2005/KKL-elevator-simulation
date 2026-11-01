"""Past-arrivals-only candidate rules. Destination visibility is an explicit assumption."""
import bisect

class Gate:
    def __init__(self,threshold):
        self.threshold=threshold;self.current='C1';self.pending=None;self.count=0;self.last_switch=0

    def update(self,t,n,from_entry,high):
        target=self.current
        if n<10:target='C1'
        elif self.current=='C1' and from_entry>=.70 and high<=self.threshold:target='C7'
        elif self.current=='C7' and (from_entry<.65 or high>self.threshold+.05):target='C1'
        if target==self.current:self.pending=None;self.count=0
        elif self.pending==target:self.count+=1
        else:self.pending=target;self.count=1
        if self.count>=2 and t-self.last_switch>=600:
            self.current=target;self.last_switch=t;self.pending=None;self.count=0
        return self.current

class PastDemandController:
    interval=60.
    def __init__(self,combos,trips,idx,threshold):
        self.combos=combos;self.trips=trips;self.times=[p.t for p in trips];self.gate=Gate(threshold)
        self.entrances={idx['G'],idx['LG2']};self.high=idx['4'];self.log=[]
    def update(self,sim,t):
        if t>=sim.gen_end:return self.combos[self.gate.current]
        if t<300:return self.combos['C1']
        lo=bisect.bisect_left(self.times,t-300);hi=bisect.bisect_left(self.times,t)
        window=self.trips[lo:hi];n=len(window)
        entry=sum(p.o in self.entrances for p in window)/n if n else 0.
        high=sum(p.o>=self.high or p.d>=self.high for p in window)/n if n else 0.
        choice=self.gate.update(t,n,entry,high)
        self.log.append(dict(t=t,n=n,from_entry=entry,high_endpoint=high,choice=choice))
        return self.combos[choice]
