"""Claims made in the submitted idea document, checked against the engine."""
from api.app.services import financing as f


def test_nine_percent_flat_is_about_seventeen_point_seven_percent_a_year():
    quote = f.LenderQuote(quote_id='flat9', lender_name='Flat lender', product_type='term_loan', currency='BDT',
                          principal_minor=100_000_00, upfront_fee_minor=0, rate_type='flat_per_term', rate_bps=900,
                          repayment='equal_installments', installments=12, term_days=360, quote_source='idea document check',
                          quoted_on='2026-11-24')
    schedule = f.repayment_schedule(quote)
    assert len(schedule) == 12 and abs(sum(p for _, p, _ in schedule) - 109_000_00) <= 12  # Tk 9,000 interest on Tk 100,000
    assert 1765 <= f.effective_annual_rate_bps(quote.principal_minor, schedule) <= 1780  # ≈17.7% a year
