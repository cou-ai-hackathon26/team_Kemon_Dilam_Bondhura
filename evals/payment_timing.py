"""Chronological synthetic calibration of the workspace-only empirical timing model."""
from collections import defaultdict
from datetime import date,timedelta
import json
import random

from api.app.services.payment_timing import Paid, History, estimate, histories


def run(seed=20261121):
    rng=random.Random(seed)
    first=date(2025,1,1)
    # Each simulated customer has a fixed timing distribution. Every training
    # receipt predates every evaluation observation, including the slowest payer.
    def sample(day):
        customer=rng.randrange(15)
        typical=(4,12,26)[customer%3]
        delay=max(-5,min(90,round(rng.gauss(typical,9))))
        due=first+timedelta(days=day)
        return Paid(str(customer),due,due+timedelta(days=delay),rng.randint(5000,1000000))
    train=[sample(rng.randrange(150)) for _ in range(2000)]
    holdout=[sample(300+rng.randrange(40)) for _ in range(2000)]
    assert max(x.cleared for x in train)<min(x.due for x in holdout)
    book,customers=histories(train); bins=defaultdict(list)
    for row in holdout:
        age=rng.choice((-3,0,5,10))
        if row.delay<=age:continue
        prediction=estimate(book,customers[row.customer],age)
        for h in (7,14,30):
            expected=prediction["within_days_pct"][str(h)]/100
            observed=int(row.delay<=age+h)
            bins[h,min(9,int(expected*10))].append((expected,observed))
    out={}
    for h in (7,14,30):
        groups=[group for (days,_),group in bins.items() if days==h]
        total=sum(map(len,groups))
        ece=sum(abs(sum(p-y for p,y in group)) for group in groups)/total
        out[str(h)]={"observations":total,"calibration_error_points":round(100*ece,2)}
    return {"seed":seed,"training_rows":len(train),"test_rows":len(holdout),"source":"synthetic",
            "horizons":out,"gate_passed":all(v["calibration_error_points"]<=10 for v in out.values())}


if __name__=="__main__":
    report=run()
    print(json.dumps(report,indent=2))
    raise SystemExit(0 if report["gate_passed"] else 1)
