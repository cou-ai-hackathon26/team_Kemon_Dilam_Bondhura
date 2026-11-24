"""Public competition walkthrough using fixed, fictional inputs and the real engines."""
from datetime import timedelta

from fastapi import APIRouter, Request
from pydantic import Field

from api.app.accounts import today_bd
from api.app import security as sec
from api.app.contracts import Contract
from api.app.services import radar
from api.app.services.finance import validated
from api.app.voice_batch import MAX_TRANSCRIPT, split_note

router = APIRouter(prefix='/api/competition')

NOTE = ('Gotokal dokan bhara 5000 taka dilam. Tarpor transport 300 taka dilam. '
        'Aj Rahim theke 12 hajar taka pelam. Aj biddut bill 1850 taka baki. Karim 2000 taka dey nai.')
WEEK_NOTE = ('Shombar transport e 1200 taka disi, Mongolbar stock kinsi 5000 takar, '
             'Budhbar worker ke 3500 taka salary disi, Brihoshpotibar electricity bill 2100 taka, '
             'Shukrobar delivery te 900 taka, Shonibar dokan vara 8000 taka disi, '
             'Robibar packaging e 1250 taka khoroch hoise.')


def _preview(note, today, week_start=None):
    drafts, dropped, method = split_note(note, today, 'BDT', 2, use_ai=False, week_start=week_start)
    return dict(drafts=drafts, dropped=dropped, method=method)


class DemoNote(Contract):
    transcript: str = Field(min_length=3, max_length=MAX_TRANSCRIPT)


def sample_radar(today):
    def day(offset):
        return today + timedelta(days=offset)

    books = validated(dict(
        opening_source_id='sample-opening', opening_on=today, as_of=today, currency='BDT',
        opening_balance_minor=1_500_000, events=[],
        invoices=[dict(source_record_id='sample-bill', invoice_id='sample-bill', vendor_id='sample-supplier',
                       invoice_number='DEMO-01', issued_on=today, due_on=day(5), amount_minor=3_000_000,
                       currency='BDT', status='open', memo='Fictional supplier bill')],
    ))
    habits, overall = radar.habits_from([
        ('sample-customer', day(-40), day(-33)), ('sample-customer', day(-25), day(-16)),
    ])
    scenarios = {
        'habit': radar.expectations([('sample-invoice', 'sample-customer', day(2), 2_500_000)], habits, overall, today),
        'overdue': radar.expectations([('sample-invoice', 'sample-customer', day(-4), 2_500_000)], {}, None, today),
    }
    for items in scenarios.values():
        items[0]['customer'] = 'Karim Traders'
    return {key: radar.outlook(books, 500_000, today, items) for key, items in scenarios.items()}


@router.get('/demo')
def competition_demo():
    today = today_bd()
    last_monday = today - timedelta(days=today.weekday() + 7)
    return dict(note=NOTE, currency='BDT', voice=_preview(NOTE, today),
                week_note=WEEK_NOTE, week_start=last_monday.isoformat(),
                weekly_voice=_preview(WEEK_NOTE, today, last_monday),
                radar=sample_radar(today), simulation_only=True)


@router.post('/drafts')
def preview_drafts(body: DemoNote, request: Request):
    _, edge = sec.client_keys(request)
    sec.budgets(None, [('competition-draft:' + edge, 12), ('competition-draft:all', 100)])
    return dict(**_preview(body.transcript.strip(), today_bd()), simulation_only=True)
