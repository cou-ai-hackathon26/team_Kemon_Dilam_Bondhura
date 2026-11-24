import {useEffect,useState} from 'react';
import {money} from './api';
import {formatDate} from './format';
import {t,T} from './i18n';

type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
type Item={receivable_id:string;customer_id:string;customer:string;due_on:string;amount_minor:number;habit_days:number;basis:'customer'|'business'|'due_date';expected_on:string;overdue_days:number;status:'on_time'|'usually_late'|'later_than_usual'|'doubtful'};
type Outlook={closing_minor:number;lowest_minor:number;first_shortfall_on:string|null;shortfall_minor:number};
export type Radar={level:'clear'|'watch'|'alert';breach_on:string|null;shortfall_minor:number;reserve_minor:number;currency:string;
  outlooks:{on_time:Outlook;realistic:Outlook;stress:Outlook};confirmed?:Outlook;available_cash_minor?:number;receivables:Item[];depends_on:Item[];doubtful_minor:number;late_minor:number};

const LEVEL={clear:T('All clear'),watch:T('Watch'),alert:T('Alert')};
const STATUS={on_time:T('On track'),usually_late:T('Usually pays late'),later_than_usual:T('Later than usual'),doubtful:T('Doubtful')};

function habit(i:Item){
  if(i.basis==='due_date')return t('No payment history yet: the due date is used.');
  const who=i.basis==='customer'?t('Usually pays'):t('Your customers usually pay');
  return i.habit_days===0?t('{who} on time.',{who}):t('{who} {days} days after the due date.',{who,days:i.habit_days});
}

/** Challenge 2: the realistic outlook, built from how each customer actually pays, and why the alert is (or is not) on. */
export function CashRadar({request,revision}:{request:Request;revision:string}){
  const [data,setData]=useState<Radar|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{let live=true;void request<Radar>('/radar').then(d=>{if(live){setData(d);setError('');}}).catch(e=>{if(live)setError(e instanceof Error?e.message:t('Request failed.'));});return()=>{live=false;};},[request,revision]);
  if(error)return <section className="panel cash-radar"><h2>{t('Cash radar')}</h2><p className="error">{error}</p></section>;
  return data?<CashRadarView data={data}/>:null;
}

export function CashRadarView({data}:{data:Radar}){
  const m=(minor:number)=>money(minor,data.currency);
  const o=data.outlooks;
  const headline=data.level==='alert'
    ?t('On {date} your cash may fall {amount} below your safety reserve, even counting how your customers usually pay.',{date:formatDate(data.breach_on!),amount:m(data.shortfall_minor)})
    :data.level==='watch'
    ?t('No shortfall expected. But if late money does not come, you would be {amount} short on {date}.',{amount:m(data.shortfall_minor),date:formatDate(data.breach_on!)})
    :t('No shortfall in the next 14 days, counting how your customers usually pay.');
  const row=(label:string,x:Outlook)=><li><span>{label}</span><span className="radar-figures"><small>{t('Lowest balance')}</small><b>{m(x.lowest_minor)}</b></span><span className="radar-figures"><small>{t('First shortfall')}</small>{x.first_shortfall_on?<b className="negative">{formatDate(x.first_shortfall_on)} · {m(x.shortfall_minor)}</b>:<b>{t('None')}</b>}</span></li>;
  return <section className={'panel cash-radar radar-'+data.level} aria-labelledby="radar-title">
    <div className="panel-heading"><div><p className="eyebrow">{t('Honest cash radar')}</p><h2 id="radar-title">{t('Cash radar')}</h2></div>
      <span className={'pill radar-pill level-'+data.level}>{t(LEVEL[data.level])}</span></div>
    <p className="radar-headline">{headline}</p>
    {data.available_cash_minor!==undefined&&<p className="muted">{t('Cash already recorded: {amount}. Open customer invoices are projected receipts.',{amount:m(data.available_cash_minor)})}</p>}
    {data.level==='watch'&&<p className="muted small">{t('This is a quiet watch, not an alert: you get a notification only if your realistic outlook falls short.')}</p>}
    <h3 className="radar-sub">{t('Lowest balance in the next 30 days')}</h3>
    <ul className="radar-outlooks">
      {data.confirmed&&row(t('Confirmed cash only, after known expenses'),data.confirmed)}
      {row(t('Everyone pays on the due date'),o.on_time)}
      {row(t('Realistic: as each customer usually pays'),o.realistic)}
      {row(t('Late money does not come'),o.stress)}
    </ul>
    {data.depends_on.length>0&&<><h3>{t('Customer money this depends on')}</h3>
      <ul className="radar-list">{data.depends_on.map(i=><li key={i.receivable_id}>
        <div><b>{i.customer||t('Customer')}</b> <span className={'pill radar-status status-'+i.status}>{t(STATUS[i.status])}</span>
          <small>{habit(i)}</small></div>
        <div className="radar-amount"><strong>{m(i.amount_minor)}</strong>
          <small>{t('Due {due}',{due:formatDate(i.due_on)})}{i.overdue_days>0?' · '+t('{days} days overdue',{days:i.overdue_days}):''}</small>
          <small>{i.status==='doubtful'?t('Not counted: too long overdue'):t('Expected {date}',{date:formatDate(i.expected_on)})}</small></div>
      </li>)}</ul></>}
    <details className="how-it-works"><summary>{t('How the radar decides')}</summary><ul className="notes">
      <li>{t('It learns from each customer’s past payments how many days after the due date they usually pay, and expects their money then.')}</li>
      <li>{t('Money more than 30 days overdue and two weeks past the customer’s habit is treated as doubtful and not counted.')}</li>
      <li>{t('An alert is sent only when the realistic outlook falls below your reserve within 14 days, once per problem, and again only if it gets clearly worse.')}</li>
      <li>{t('Overdue reminders start only after a customer is later than they usually are.')}</li></ul></details>
  </section>;
}
