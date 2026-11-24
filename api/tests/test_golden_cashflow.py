from datetime import date,timedelta
from hypothesis import given,settings,strategies as st

from api.app.contracts import ScenarioRequest
from api.app.services.finance import Dataset,project_cashflow,snapshot
from api.app.copilot.gapfix import best_plan


@given(opening=st.integers(0,10000000), gap=st.integers(1,10000000), extra=st.integers(0,5000000),
       spend_day=st.integers(1,6), collect_day=st.integers(7,30))
@settings(max_examples=80,deadline=None)
def test_moving_a_receipt_restores_reserve_and_preserves_total(opening,gap,extra,spend_day,collect_day):
    day=date(2026,11,21)
    def event(source,direction,amount,offset):
        return dict(source_record_id=source,event_id=source,direction=direction,amount_minor=amount,currency='BDT',
                    status='scheduled',effective_on=day+timedelta(days=offset))
    ds=Dataset(opening_source_id='opening',opening_on=day,as_of=day,currency='BDT',opening_balance_minor=opening,
               invoices=[],events=[event('materials','outflow',opening+gap,spend_day),event('customer','inflow',gap+extra,collect_day)])
    request=ScenarioRequest(as_of=day,horizon_days=30,reserve_minor=0)
    forecast=project_cashflow(ds,request)
    plan=best_plan(ds,request,[],forecast)
    assert plan['actions']==[dict(kind='collect_early',source_id='customer',scheduled_on=str(day+timedelta(days=collect_day)),
                                  collect_by=str(day+timedelta(days=spend_day)),amount_minor=gap)]
    assert plan['remaining_gap_minor']==0
    assert all(row['projected_balance_minor']>=0 for row in plan['forecast']['days'])
    assert plan['forecast']['closing_balance_minor']==forecast['closing_balance_minor']==extra
    assert snapshot(ds)['balance_minor']==opening
