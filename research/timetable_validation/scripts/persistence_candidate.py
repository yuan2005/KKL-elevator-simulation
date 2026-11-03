"""One-factor development candidate; keep the published D10 implementation untouched."""
from adaptive_policy import Gate,PastDemandController


class PersistenceGate(Gate):
    def __init__(self,threshold,required=3):
        super().__init__(threshold)
        if not isinstance(required,int) or required<1:raise ValueError('required must be a positive integer')
        self.required=required

    def update(self,t,n,from_entry,high):
        target=self.current
        if n<10:target='C1'
        elif self.current=='C1' and from_entry>=.70 and high<=self.threshold:target='C7'
        elif self.current=='C7' and (from_entry<.65 or high>self.threshold+.05):target='C1'
        if target==self.current:self.pending=None;self.count=0
        elif self.pending==target:self.count+=1
        else:self.pending=target;self.count=1
        if self.count>=self.required and t-self.last_switch>=600:
            self.current=target;self.last_switch=t;self.pending=None;self.count=0
        return self.current


class PersistenceController(PastDemandController):
    def __init__(self,combos,trips,idx,required=3):
        super().__init__(combos,trips,idx,.10)
        self.gate=PersistenceGate(.10,required)
