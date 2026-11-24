import {useRef,useState,type FormEvent} from 'react';
import {useCurrency} from './Currency';
import {t} from './i18n';
import type {CashEvent,Session} from './types';

type Props={
  session:Session;
  events:CashEvent[];
  request:<T>(path:string,body?:unknown)=>Promise<T>;
  onSaved:()=>Promise<void>;
};

export default function CashCorrections({session,events,request,onSaved}:Props){
  const {currency,digits,nativeMoney,parseMinor}=useCurrency();
  const [rows,setRows]=useState<CashEvent[]>([]);
  const [selected,setSelected]=useState<CashEvent|null>(null);
  const [loaded,setLoaded]=useState(false);
  const [voided,setVoided]=useState(false);
  const [amount,setAmount]=useState('');
  const [date,setDate]=useState('');
  const [direction,setDirection]=useState<CashEvent['direction']>('inflow');
  const [timing,setTiming]=useState<CashEvent['status']>('cleared');
  const [confirmed,setConfirmed]=useState(false);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  const attempt=useRef<{signature:string;id:string}|null>(null);

  function select(row:CashEvent|null){
    setSelected(row);setVoided(false);setConfirmed(false);
    if(row){
      setAmount((row.amount_minor/10**digits).toFixed(digits));setDate(row.effective_on);
      setDirection(row.direction);setTiming(row.status);
    }
  }
  async function load(){
    select(null);setLoaded(false);setBusy(true);setError('');setMessage('');
    try{
      const result=await request<{rows:CashEvent[];business_id:string;user_id:string}>('/workspace/cash-corrections');
      if(result.business_id!==session.business_id||result.user_id!==session.user_id)throw new Error(t('Refresh this workspace to continue.'));
      setRows(result.rows.sort((a,b)=>b.effective_on.localeCompare(a.effective_on)));setLoaded(true);
    }catch(e){setError(e instanceof Error?e.message:t('Could not load records.'));}
    finally{setBusy(false);}
  }
  async function save(event:FormEvent){
    event.preventDefault();
    if(!selected||!confirmed||busy)return;
    setBusy(true);setError('');setMessage('');
    try{
      const replacement=voided?null:{amount_minor:parseMinor(amount),effective_on:date,direction,status:timing};
      const fields={original_id:selected.event_id,previous_source_id:selected.source_record_id,replacement,
        expected_business_id:session.business_id,expected_user_id:session.user_id,confirmed:true};
      const signature=JSON.stringify(fields);
      const id=attempt.current?.signature===signature?attempt.current.id:crypto.randomUUID();
      attempt.current={signature,id};
      await request('/workspace/cash-corrections',{...fields,request_id:id});
      select(null);setLoaded(false);setMessage(t('Cash correction recorded.'));attempt.current=null;
      await onSaved();
    }catch(e){setError(e instanceof Error?e.message:t('Could not save the correction.'));}
    finally{setBusy(false);}
  }
  const labels=new Map(events.map(e=>[e.event_id,e.note]));
  return <section className="panel"><h2>{t('Correct a cash entry')}</h2>
    <p>{t('Choose a manually entered cash record. The original and each correction are kept in Activity.')}</p>
    <button type="button" className="secondary" onClick={()=>void load()} disabled={busy}>{t('Choose a cash entry')}</button>
    {loaded&&(rows.length?<label>{t('Cash entry')}<select disabled={busy} value={selected?.event_id||''} onChange={e=>select(rows.find(row=>row.event_id===e.target.value)||null)}>
      <option value="">{t('Select a record')}</option>
      {rows.map(row=><option key={row.event_id} value={row.event_id}>{row.effective_on} · {row.direction==='inflow'?t('Money in'):t('Money out')} · {nativeMoney(row.amount_minor)} · {labels.get(row.event_id)} · {row.event_id.slice(-8)}</option>)}
    </select></label>:<p>{t('No manual cash entries are available to correct.')}</p>)}
    {selected&&<form onSubmit={e=>void save(e)}><fieldset disabled={busy}><legend>{t('Correction details')}</legend>
      <label>{t('Action')}<select value={voided?'void':'replace'} onChange={e=>{setVoided(e.target.value==='void');setConfirmed(false);}}>
        <option value="replace">{t('Replace the recorded details')}</option><option value="void">{t('Void this entry')}</option>
      </select></label>
      {voided?<p>{t('Voiding removes this entry from balances and forecasts. Its history stays available.')}</p>:<div className="form-grid" onChange={()=>setConfirmed(false)}>
        <label>{t('Amount ({currency})',{currency})}<input inputMode="decimal" required value={amount} onChange={e=>setAmount(e.target.value)}/></label>
        <label>{t('Activity date')}<input type="date" required value={date} onChange={e=>setDate(e.target.value)}/></label>
        <label>{t('Direction')}<select value={direction} onChange={e=>setDirection(e.target.value as CashEvent['direction'])}><option value="inflow">{t('Money in')}</option><option value="outflow">{t('Money out')}</option></select></label>
        <label>{t('Timing')}<select value={timing} onChange={e=>setTiming(e.target.value as CashEvent['status'])}><option value="cleared">{t('Already received / spent')}</option><option value="scheduled">{t('Planned / expected')}</option></select></label>
      </div>}
      <label className="check"><input type="checkbox" required checked={confirmed} onChange={e=>setConfirmed(e.target.checked)}/>{t('I checked this correction.')}</label>
      <button disabled={!confirmed||busy}>{t('Confirm correction')}</button></fieldset></form>}
    {message&&<p role="status">{message}</p>}{error&&<p role="alert" className="error">{error}</p>}
  </section>;
}
