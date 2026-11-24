import {useEffect,useState} from 'react';
import {money} from './api';
import {t,T} from './i18n';

type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
type Position={id:string;name:string;unit:string;quantity:string|null;cost_minor:number|null;expense_minor:number;missing:string[];status:'ok'|'thin'|'loss'|'incomplete';
  reference:{value_minor:number;basis:'market'|'own_price';source:string;kind:string}|null;gross_minor?:number;net_minor?:number;break_even_minor?:number;
  margin_bp?:number|null;cushion_bp?:number|null;exposure_minor?:number|null;total_net_minor?:number|null};
type Area={key:string;label:string;weight:number;score:number|null;why:string;missing:string};
type Insights={currency:string;positions:Position[];totals:{cost_minor:number;exposure_minor:number;net_minor:number;counted:number;products:number};
  scenarios:{rows:{move_pct:number;total_net_minor:number}[];products:number;market_linked:number};
  growth:{month:string;income_minor:number;expenses_minor:number;net_minor:number;cash_margin_bp:number|null;mom:{pct_bp:number|null;reason:string};yoy:{pct_bp:number|null;reason:string};months_recorded:number};
  capability:{score:number|null;coverage_pct:number;preliminary:boolean;areas:Area[]}};

const MISSING:Record<string,string>={cost:T('purchase cost'),reference:T('a current price'),quantity:T('quantity')};
const STATUS:Record<string,string>={ok:T('In profit'),thin:T('Near break-even'),loss:T('Below break-even'),incomplete:T('Needs data')};
const pct=(bp:number|null|undefined)=>bp==null?'—':`${bp>0?'+':''}${(bp/100).toFixed(1)}%`;
const monthName=(m:string)=>new Date(m+'-01T00:00:00').toLocaleDateString(document.documentElement.lang||undefined,{month:'long',year:'numeric'});

/** Real profit per product after selling costs, how far prices can fall, growth from records, and an explained score. */
export function InsightsPanel({request,openProducts}:{request:Request;openProducts:()=>void}){
  const [data,setData]=useState<Insights|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{void request<Insights>('/insights').then(setData).catch(e=>setError(e instanceof Error?e.message:t('Request failed.')));},[]);// eslint-disable-line react-hooks/exhaustive-deps
  if(!data)return error?<section className="panel"><p className="error">{error}</p></section>:null;
  const m=(v:number)=>money(v,data.currency),g=data.growth,cap=data.capability;
  const signed=(v:number)=><span className={v<0?'negative':v>0?'positive':undefined}>{v<0?'−':''}{m(Math.abs(v))}</span>;
  return <section className="panel insights-panel" aria-labelledby="insights-title">
    <div className="panel-heading"><div><p className="eyebrow">{t('Financial intelligence')}</p><h2 id="insights-title">{t('Your numbers')}</h2></div></div>

    <h3>{t('Profit if you sold today')}</h3>
    {!data.positions.length?<p className="muted">{t('Add products with their purchase cost to see your real profit and break-even price.')} <button className="link" onClick={openProducts}>{t('Add products')}</button></p>:
    <div className="table-wrap"><table className="insights-table"><thead><tr><th>{t('Product')}</th><th>{t('Cost')}</th><th>{t('Price today')}</th><th>{t('Costs to sell')}</th><th>{t('Net per unit')}</th><th>{t('Break-even price')}</th></tr></thead>
      <tbody>{data.positions.map(p=><tr key={p.id} className={'pos-'+p.status}>
        <td><b>{p.name}</b><small><span className={'pill status-'+p.status}>{t(STATUS[p.status])}</span>{p.quantity&&` ${p.quantity} ${p.unit}`}</small></td>
        <td className="amount" data-label={t('Cost')}>{p.cost_minor==null?'—':m(p.cost_minor)}</td>
        <td className="amount" data-label={t('Price today')}>{p.reference?<>{m(p.reference.value_minor)}<small>{p.reference.basis==='market'?p.reference.source:t('Your selling price')}</small></>:'—'}</td>
        {p.status==='incomplete'?<td colSpan={3} className="muted">{t('Add {what} to calculate.',{what:p.missing.map(x=>t(MISSING[x]||x)).join(', ')})}</td>:<>
          <td className="amount" data-label={t('Costs to sell')}>{m(p.expense_minor)}</td>
          <td className="amount" data-label={t('Net per unit')}><b>{signed(p.net_minor!)}</b><small>{t('Gross {gross}',{gross:m(p.gross_minor!)})}</small></td>
          <td className="amount" data-label={t('Break-even price')}>{m(p.break_even_minor!)}<small>{p.cushion_bp!=null&&(p.cushion_bp<0?t('Price is {pct} below it',{pct:pct(-p.cushion_bp).replace('+','')}):t('Price can fall {pct} before a loss',{pct:pct(p.cushion_bp).replace('+','')}))}</small></td></>}
      </tr>)}</tbody></table></div>}
    {data.positions.some(p=>p.reference?.basis==='market')&&<p className="muted small-print">{t('A published market price is not always what a dealer pays back. Put buyback deductions and other selling costs in “Costs to sell one unit” for each product.')}</p>}
    {data.totals.counted>0&&<div className="metrics insights-totals">
      <article className="metric"><p>{t('Stock at cost')}</p><strong>{m(data.totals.cost_minor)}</strong></article>
      <article className="metric"><p>{t('Stock at today\'s price')}</p><strong>{m(data.totals.exposure_minor)}</strong></article>
      <article className="metric"><p>{t('Net if sold today')}</p><strong>{signed(data.totals.net_minor)}</strong><small>{t('{n} of {total} products counted',{n:data.totals.counted,total:data.totals.products})}</small></article>
    </div>}

    {data.scenarios.market_linked>0&&<><h3>{t('If market prices move')}</h3>
      <div className="scenario-row">{data.scenarios.rows.map(r=><div key={r.move_pct} className={r.move_pct===0?'now':undefined}>
        <small>{r.move_pct===0?t('Today'):`${r.move_pct>0?'+':''}${r.move_pct}%`}</small><b>{signed(r.total_net_minor)}</b></div>)}</div>
      <p className="muted small-print">{t('Net result on the stock you entered if every market-linked price moved by the same amount. Scenarios, not forecasts.')}</p></>}

    <h3>{t('Growth from your records')}</h3>
    {g.income_minor===0&&g.expenses_minor===0?<p className="muted">{t('No cleared income or expenses were recorded in {month}. Growth appears once you record a few months.',{month:monthName(g.month)})}</p>:
    <div className="metrics insights-growth">
      <article className="metric"><p>{t('Income in {month}',{month:monthName(g.month)})}</p><strong>{m(g.income_minor)}</strong><small>{t('Cash margin {pct}',{pct:pct(g.cash_margin_bp)})}</small></article>
      <article className="metric"><p>{t('vs. month before')}</p><strong>{pct(g.mom.pct_bp)}</strong>{g.mom.reason&&<small>{t(g.mom.reason)}</small>}</article>
      <article className="metric"><p>{t('vs. same month last year')}</p><strong>{pct(g.yoy.pct_bp)}</strong>{g.yoy.reason&&<small>{t(g.yoy.reason)}</small>}</article>
    </div>}

    <h3>{t('Business capability')}</h3>
    <div className="capability">
      <div className="capability-score"><strong>{cap.preliminary?'—':cap.score??'—'}</strong><small>{cap.score==null||cap.preliminary?t('Not enough data for a score yet. Each area below says what is missing.'):t('out of 100 · based on {pct}% of areas',{pct:cap.coverage_pct})}</small>
        </div>
      <ul>{cap.areas.map(a=><li key={a.key}><div><b>{t(a.label)}</b><small>{a.score==null?t(a.missing):t(a.why)}</small></div>
        {a.score==null?<span className="muted no-data">{t('No data')}</span>:<meter min={0} max={100} low={40} high={70} optimum={100} value={a.score} aria-label={t(a.label)}>{a.score}</meter>}</li>)}</ul>
    </div>
  </section>;
}
