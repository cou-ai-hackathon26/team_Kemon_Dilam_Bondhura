import pytest
from pydantic import ValidationError

from api.app.services.financing import LenderQuote
from api.app.services.offer_reader import read_offer
from api.tests.test_accounts import world,signup
from api.tests.test_financing import prepare,quote


@pytest.mark.parametrize('labels,rate_line,kind,basis,rate',[
    (('Lender','Principal','Fee','Term'),'Rate: 9% flat','term','flat_per_term',900),
    (('Bank','Loan amount','Upfront fee','Tenor'),'Annual reducing balance rate 12.25%','term','annual_reducing',1225),
    (('Provider','Financed amount','Processing fee','Term'),'Flat rate 8.5%','term','flat_per_term',850),
    (('ঋণদাতা','ঋণের পরিমাণ','প্রসেসিং ফি','মেয়াদ'),'বার্ষিক ক্রমহ্রাসমান হার ১০%','term','annual_reducing',1000),
    (('ব্যাংক','ঋণের পরিমাণ','ফি','মেয়াদ'),'ফ্ল্যাট হার ৭.২৫%','term','flat_per_term',725),
    (('Lender','Asset cost','Fee','Term'),'Murabaha; Fixed markup: BDT 225','murabaha','murabaha_markup',None),
    (('Provider','Cash price','Upfront fee','Tenor'),'Murabaha; Sale price: BDT 1,175','murabaha','murabaha_markup',None),
    (('ব্যাংক','নগদ মূল্য','ফি','মেয়াদ'),'মুরাবাহা; মুনাফা: ১৭৫ টাকা','murabaha','murabaha_markup',None),
    (('Lender','Cash price','Fee','Term'),'Murabaha; Markup rate: 10%','murabaha','murabaha_markup',1000),
    (('Bank','Cash price','Processing fee','Tenor'),'Murabaha; Deferred price: BDT 1,290.25','murabaha','murabaha_markup',None),
    (('Lender','Loan amount','Fee','Term'),'Interest rate 6.75% flat','term','flat_per_term',675),
    (('Bank','Principal','Fee','Term'),'Annual interest rate 11.50% on declining balance','term','annual_reducing',1150),
    (('Provider','Principal','Fee','Tenor'),'Rate: 0% flat','term','flat_per_term',0),
    (('Lender','Financed amount','Processing fee','Term'),'Reducing balance rate: 15% per annum','term','annual_reducing',1500),
    (('Bank','Principal','Upfront fee','Term'),'Flat rate: 12%','term','flat_per_term',1200),
])
def test_fifteen_synthetic_offers(labels,rate_line,kind,basis,rate):
    lender,principal,fee,term=labels
    contents=f'{lender}: Fictional A; {principal}: BDT 1,000; {fee}: BDT 10; {term}: 10 months; {rate_line}; 10 equal monthly installments'
    result=read_offer(contents,'BDT')
    assert result['warnings']==[]
    fields=result['terms']
    assert fields['lender_name']=='Fictional A'
    assert (fields['principal_minor'],fields['upfront_fee_minor'])==(100000,1000)
    assert fields['term']==10 and fields['unit']=='months' and fields['repayment']=='equal_installments'
    assert fields['rate_type']==basis
    if rate is not None:assert fields['rate_bps']==rate
    if kind=='murabaha':
        assert fields['product_type']=='murabaha'
        expected=22500 if '225' in contents else 10000 if '10%' in contents else 29025 if '1,290.25' in contents else 17500
        assert fields['markup_minor']==expected


def test_incomplete_terms_and_conflicting_prices():
    assert 'rate_type' not in read_offer('Annual flat rate: 10%','BDT')['terms']
    assert 'rate_bps' not in read_offer('Offer; Fee: 7%','BDT')['terms']
    assert 'principal_minor' not in read_offer('First offer 100; second offer 110','BDT')['terms']
    assert read_offer('Principal: USD 200; Fee: BDT 0','BDT')['warnings']==['CURRENCY_MISMATCH']
    assert read_offer('Murabaha; asset cost: 100; markup: 5; sale price: 136','USD')['warnings']==['INCONSISTENT_MARKUP']
    assert 'repayment' not in read_offer('Term: 18 months; 12 monthly installments','BDT')['terms']


def test_murabaha_actual_cost_without_unrestricted_cash(world):
    c,_=world;signup(c,'asset_owner');bundle=prepare(c)
    raw=quote(quote_id='asset',product_type='murabaha',principal_minor=250000,upfront_fee_minor=5000,
              rate_type='murabaha_markup',markup_minor=31111,rate_bps=1244)
    result=c.post('/api/financing/match',json=dict(as_of=bundle['snapshot']['as_of'],quotes=[raw]))
    assert result.status_code==200,result.text
    data=result.json();row=data['quotes'][0]
    assert row['status']=='ASSET_FINANCING_ONLY' and data['best_match_quote_id'] is None
    assert row['net_proceeds_minor']==0 and row['lowest_balance_with_financing_minor']<=-205000
    assert row['total_cost_minor']==36111 and row['total_repayment_minor']==281111
    assert row['markup_minor']==31111 and row['interest_minor']==0
    assert row['schedule'][0]['markup_minor']==31111 and row['schedule'][0]['interest_minor']==0
    expected=((281111/245000)**(365/90)-1)*10000
    assert abs(row['effective_annual_rate_bps']-expected)<=1
    assert 'markup_minor' not in LenderQuote.model_validate(quote()).model_dump(mode='json')
    for change in [{'rate_bps':1400},{'markup_minor':None},{'product_type':'term_loan'}]:
        with pytest.raises(ValidationError):
            LenderQuote.model_validate(dict(raw,**change))


def test_reader_is_read_only_and_scoped(world):
    c,_=world;signup(c,'offer_owner')
    b=c.get('/api/workspace').json()
    data=c.post('/api/assist/offer',json=dict(text='Principal: BDT 5000; Flat rate 9%; Term: 12 months')).json()
    assert data['terms']['principal_minor']==500000 and data['terms']['rate_bps']==900
    assert c.get('/api/workspace').json()['dataset_revision']==b['dataset_revision']
    invite=c.post('/api/account/invitations',json=dict(role='viewer')).json()['code']
    signup(c,'offer_viewer');c.post('/api/account/join',json=dict(code=invite))
    assert c.post('/api/assist/offer',json=dict(text='Principal: 5000')).status_code==403
