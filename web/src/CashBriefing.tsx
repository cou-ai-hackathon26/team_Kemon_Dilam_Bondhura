import {useEffect,useState} from 'react';
import {money} from './api';
import {formatDate} from './format';
import {t} from './i18n';

type Briefing={
  as_of:string; currency:string; balance_minor:number; amount_owed_minor:number;
  closing_7_day_minor:number; first_shortfall_on:string|null;
};
type Request=<T,>(path:string)=>Promise<T>;

export function CashBriefing({request,initiallyOpen}:{request:Request;initiallyOpen:boolean}){
  const [open,setOpen]=useState(initiallyOpen);
  const [value,setValue]=useState<Briefing|null>(null);
  const [error,setError]=useState('');
  const [attempt,setAttempt]=useState(0);
  useEffect(()=>{
    if(!open)return;
    let active=true;
    setValue(null);setError('');
    request<Briefing>('/copilot/briefing').then(data=>{if(active)setValue(data);})
      .catch(reason=>{if(active)setError(reason instanceof Error?reason.message:t('Request failed.'));});
    return ()=>{active=false;};
  },[open,attempt,request]);
  return <section className="panel">
    <button id="cash-briefing" type="button" className="secondary" aria-expanded={open}
      aria-controls="briefing-report" onClick={()=>setOpen(!open)}>{t('Cash briefing')}</button>
    <div id="briefing-report" hidden={!open} aria-live="polite">
      {error?<p role="alert">{error} <button type="button" className="link" onClick={()=>setAttempt(n=>n+1)}>{t('Try again')}</button></p>
        :value?<><h3>{t('Cash position on {date}',{date:formatDate(value.as_of)})}</h3>
          <p>{t('Available cash is {cash}. Customers owe {owed}.',{
            cash:money(value.balance_minor,value.currency),owed:money(value.amount_owed_minor,value.currency)})}</p>
          <p>{t('Projected balance in seven days is {amount}.',{amount:money(value.closing_7_day_minor,value.currency)})}
            {' '}{value.first_shortfall_on?t('Cash falls below your reserve on {date}.',{date:formatDate(value.first_shortfall_on)})
              :t('Cash stays at or above your reserve for the next seven days.')}</p></>
        :open&&<p>{t('Loading cash briefing…')}</p>}
    </div>
  </section>;
}
