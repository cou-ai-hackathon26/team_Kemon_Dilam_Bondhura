import {useEffect,useState} from 'react';
import {formatDate} from './format';
import {HomeActions,type HomeNavigate} from './HomeActions';
import {useCurrency} from './Currency';
import {CollectionOptions} from './CollectionOptions';
import {CashChart} from './Charts';
import {Icon} from './Icon';
import type {Audit,CashEvent,Forecast,Invoice,Proposal,Snapshot,Session,NextCollection} from './types';
import {t,T} from './i18n';
export function Dashboard({role,nextCollection,snapshot,week,month,invoices,events,proposals,audit,navigate,openAssistant,onSample}:{role:Session['role'];nextCollection:NextCollection|null;snapshot:Snapshot|null;week:Forecast|null;month:Forecast|null;invoices:Invoice[];events:CashEvent[];proposals:Proposal[];audit:Audit|null;navigate:HomeNavigate;openAssistant:()=>void;onSample?:()=>void}){
 const {money}=useCurrency();
 if(!snapshot)return <section className="panel" role="status"><p>{t('Loading your verified financial overview…')}</p></section>;
 const open=invoices.filter(i=>i.status==='open');const overdue=open.filter(i=>snapshot&&i.due_on<snapshot.as_of);const paid=invoices.filter(i=>i.status==='paid');
 const total=(rows:Invoice[])=>rows.reduce((n,i)=>n+i.amount_minor,0);const recent=events.slice().sort((a,b)=>b.effective_on.localeCompare(a.effective_on)||b.event_id.localeCompare(a.event_id)).slice(0,5);
 const pending=proposals.filter(p=>p.state==='AWAITING_APPROVAL');
 return <><div className="dashboard-intro"><div className="actions">{(role==='owner'||role==='bookkeeper')&&<><button onClick={()=>navigate('Data','quick-text')}><Icon name="Data"/>{t('Add a record')}</button><button className="secondary" onClick={()=>navigate('Data','capture-photo')}><Icon name="camera"/>{t('Scan a bill')}</button></>}<button className="secondary" onClick={openAssistant}><Icon name="spark"/>{t('Ask your assistant')}</button>{onSample&&<button className="secondary" onClick={onSample} title={t('Fills this empty workspace with a fictional exporter so you can try every feature. Nothing real is used.')}><Icon name="spark"/>{t('Try a sample business')}</button>}</div></div>
 <HomeActions asOf={snapshot.as_of} forecast={month} invoices={invoices} proposals={proposals} nextCollection={nextCollection} role={role} navigate={navigate}/>
 <CashTotals snapshot={snapshot} events={events}/>
 <div className="dashboard-grid"><section className="panel dashboard-outlook"><div className="panel-heading"><div><p className="eyebrow">{t('Looking ahead')}</p><h2>{t('Cash flow outlook')}</h2></div><button className="secondary small" onClick={()=>navigate('Cash flow')}>{t('Explore scenarios')} <Icon name="arrow"/></button></div>{week&&month?<><div className="forecast-values"><div><small>{t('In 7 days')}</small><strong>{money(week.closing_balance_minor)}</strong></div><div><small>{t('In 30 days')}</small><strong>{money(month.closing_balance_minor)}</strong></div><span className={'forecast-status '+(month.first_shortfall_on?'warning':'')}><span aria-hidden="true">●</span>{month.first_shortfall_on?t('Reserve review needed'):t('Within forecast reserve')}</span></div><CashChart forecast={month}/></>:<p className="muted">{t('Refresh data to see your outlook.')}</p>}<p className="small muted">{month?.first_shortfall_on?t('First projected reserve shortfall: ')+month.first_shortfall_on+'.':t('Based on scheduled receipts, activity and supplier invoices.')} {t('Forecasts are scenarios, not guarantees.')}</p></section>
 <section className="panel invoice-health"><div className="panel-heading"><div><p className="eyebrow">{t('Supplier obligations')}</p><h2>{t('Invoice overview')}</h2></div><Icon name="Invoices"/></div><div className="invoice-total"><small>{t('Outstanding amount')}</small><strong>{money(total(open))}</strong><span>{t(open.length===1?T('{n} open invoice'):T('{n} open invoices'),{n:open.length})}</span></div><div className="invoice-bars">{[[t('Open'),open,'blue'],[t('Overdue'),overdue,'amber'],[t('Recorded paid'),paid,'green']].map(([label,rows,color])=><div className="invoice-stat" key={String(label)}><span><i className={String(color)}/>{String(label)} <small>{(rows as Invoice[]).length}</small></span><strong>{money(total(rows as Invoice[]))}</strong></div>)}</div><button className="secondary full-width" onClick={()=>navigate('Invoices')}>{t('View invoices')} <Icon name="arrow"/></button></section>
 <section className="panel recent-activity"><div className="panel-heading"><div><p className="eyebrow">{t('Your business in motion')}</p><h2>{t('Recent cash activity')}</h2></div><button className="link" onClick={()=>navigate('Data')}>{t('View all')}</button></div>{recent.length?<ul className="activity-rows">{recent.map(e=><li key={e.event_id}><span className={'activity-icon '+e.direction} aria-hidden="true"><Icon name={e.direction==='inflow'?'down':'up'}/></span><div><strong>{e.note|| (e.invoice_id?'Invoice settlement':e.direction==='inflow'?t('Cash received'):t('Cash spent'))}</strong><small>{e.effective_on} · {e.status}</small></div><strong className={e.direction==='inflow'?'positive':''}>{e.direction==='inflow'?'+':'−'}{money(e.amount_minor)}</strong></li>)}</ul>:<div className="empty-activity"><Icon name="Cash flow"/><h3>{t('Your activity belongs here.')}</h3><p>{t('Add an actual cash record to start your financial timeline.')}</p><button className="secondary" onClick={()=>navigate('Data')}>{t('Add a record')}</button></div>}</section>
 <section className="panel decision-card"><p className="eyebrow">{t('Control at every step')}</p><h2>{t('Review with confidence.')}</h2><div className="review-count"><strong>{pending.length}</strong><span>{t(pending.length===1?T('proposal awaiting review'):T('proposals awaiting review'))}</span></div><p>{t('Every payment is approved before it is recorded; invite an approver to have a second person check each one.')}</p><button className="secondary full-width" onClick={()=>navigate('Approvals')}>{t('Open payment review')} <Icon name="arrow"/></button><button className="audit-status link" onClick={()=>navigate('Audit')}><Icon name="Audit"/>{audit?.chain_verified?t('History verified'):t('See activity')}</button></section></div>
 {month?.collection_plan && <CollectionOptions plan={month.collection_plan} events={events}/>}
 </>;
}

/** Counts up to a figure once, so the overview feels alive; the final value is always exact. */
function CountUp({value,format}:{value:number;format:(minor:number)=>string}){
  const [shown,setShown]=useState(()=>{try{return matchMedia('(prefers-reduced-motion: reduce)').matches?value:0;}catch{return value;}});
  useEffect(()=>{
    if(shown===value)return;
    const from=shown,start=performance.now();let raf=0;
    const step=(now:number)=>{const k=Math.min(1,(now-start)/700);const eased=1-Math.pow(1-k,3);
      setShown(k<1?Math.round(from+(value-from)*eased):value);if(k<1)raf=requestAnimationFrame(step);};
    raf=requestAnimationFrame(step);return()=>cancelAnimationFrame(raf);
  },[value]);// eslint-disable-line react-hooks/exhaustive-deps
  return <>{format(shown)}</>;
}

function CashTotals({snapshot,events}:{snapshot:Snapshot;events:CashEvent[]}){
  const {money,approx,nativeMoney}=useCurrency();
  const [selected,setSelected]=useState<number|null>(null);
  const totals=[
    {label:t('Available balance'),value:snapshot.balance_minor,hint:t('From your cleared records'),icon:'Overview'},
    {label:t('Cleared income'),value:snapshot.income_minor,hint:t('Money received'),icon:'Cash flow'},
    {label:t('Cleared expenses'),value:snapshot.expenses_minor,hint:t('Money spent'),icon:'Invoices'}
  ];
  const records=selected===null?[]:events.filter(e=>e.status==='cleared'&&
    (selected===0||(selected===1?e.direction==='inflow':e.direction==='outflow')));
  return <>
    <section className="metrics balance-cards" aria-label={t('Cash summary')}>
      {totals.map(({label,value,hint,icon},i)=><article className={'metric metric-'+i} key={icon}>
        <div className="metric-label"><p>{label}</p><Icon name={icon}/></div>
        <button type="button" className="metric-source" aria-expanded={selected===i} aria-controls="cash-sources"
          aria-label={t('Show records for {figure}',{figure:label})} onClick={()=>setSelected(selected===i?null:i)}>
          <strong><CountUp value={value} format={money}/></strong></button>
        {approx(value)&&<span className="approx">{approx(value)}</span>}<small>{hint}</small>
      </article>)}
    </section>
    <section id="cash-sources" className="panel" hidden={selected===null}>
      {selected!==null&&<><h2>{t('Records for {figure}',{figure:totals[selected].label})}</h2>
        {selected===0&&<p>{t('Opening balance')} · {nativeMoney(snapshot.opening_balance_minor)} <small>{snapshot.source_ids[0]}</small></p>}
        {records.length?<ul className="activity-rows">{records.map(e=><li key={e.event_id}>
          <div><strong>{e.note||(e.direction==='inflow'?t('Cash received'):t('Cash spent'))}</strong>
            <small>{formatDate(e.effective_on)} · {e.source_record_id}</small></div>
          <b>{e.direction==='inflow'?'+':'−'}{nativeMoney(e.amount_minor)}</b>
        </li>)}</ul>:<p>{t('No cleared records contribute to this figure.')}</p>}</>}
    </section>
  </>;
}
