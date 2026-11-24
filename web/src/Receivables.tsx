import {useEffect,useRef,useState,type FormEvent} from 'react';
import {useCurrency} from './Currency';
import {ApiError} from './api';
import {ReminderDraft} from './ReminderDraft';
import {asciiDigits} from './recordParser';
import {formatDate} from './format';
import {t} from './i18n';

type Customer = {id:string; name:string|null; phone:string|null};
type Debt = {id:string; customer_id:string; number:string|null; issued_on:string; due_on:string; amount_minor:number; open_minor:number};
type Outlook={status:'estimated'|'insufficient_history';reason:string;history_count:number;customer_count:number;within_days_pct?:Record<'7'|'14'|'30',number>};
type Book = {customers:Customer[]; receivables:Debt[]; collection_outlook?:Record<string,Outlook>};
type Request = <T,>(path:string,body?:unknown)=>Promise<T>;
type Props = {request:Request; onSaved:()=>Promise<void>};

const today = () => new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Dhaka',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const emptyBook:Book = {customers:[],receivables:[]};

function PaymentTiming({outlook}:{outlook?:Outlook}){
  if(!outlook)return null;
  return <details><summary>{t('Estimated collection timing')}</summary>
    {outlook.within_days_pct?<><p>{t('Chance of full collection from today')}</p>
      <ul>{(['7','14','30'] as const).map(day=><li key={day}>{t('Within {days} days',{days:day})} · {outlook.within_days_pct![day]}%</li>)}</ul>
      <small>{outlook.customer_count?t('Based on {n} comparable paid bills for this customer, adjusted using your workspace history.',{n:outlook.customer_count}):t('Based on {n} comparable paid bills in this workspace.',{n:outlook.history_count})}</small>
      <small>{t('This estimate uses previous bills that were eventually paid. A bill can remain unpaid.')}</small>
    </>:<small>{t('Enter more completed receipts to estimate collection timing for bills of this age.')}</small>}
  </details>;
}

export function Receivables({request,onSaved}:Props){
  const {currency,nativeMoney,parseMinor} = useCurrency();
  const [book,setBook] = useState<Book>(emptyBook);
  const [mode,setMode] = useState<'customer'|'debt'|'collect'|null>(null);
  const [selected,setSelected] = useState<Debt|null>(null);
  const [busy,setBusy] = useState(true);
  const [error,setError] = useState('');
  const [message,setMessage] = useState('');
  const pending = useRef<{signature:string; id:string}|null>(null);
  const lock = useRef(false);
  const initialRequest = useRef(request);

  useEffect(()=>{
    let active = true;
    initialRequest.current<Book>('/workspace/receivables').then(data=>{
      if(active)setBook(data);
    }).catch(e=>{if(active)setError(e.message);}).finally(()=>{if(active)setBusy(false);});
    return ()=>{active=false;};
  },[]);

  async function mutate(path:string,fields:Record<string,unknown>,success:string,withId=true){
    if(lock.current)return;
    lock.current=true;setBusy(true);setError('');setMessage('');
    try{
      const signature=JSON.stringify([path,fields]);
      if(pending.current?.signature!==signature)pending.current={signature,id:crypto.randomUUID()};
      await request(path,withId?{...fields,request_id:pending.current.id}:fields);
      setMode(null);setSelected(null);pending.current=null;setMessage(success);
      await onSaved();
      setBook(await request<Book>('/workspace/receivables'));
    }catch(e){
      if(e instanceof ApiError && e.code==='AMOUNT_EXCEEDS_RECEIVABLE')setError(t('The receipt exceeds the amount still owed.'));
      else if(e instanceof ApiError && e.code==='INVALID_RECEIPT_DATE')setError(t('Choose a receipt date between the issue date and today.'));
      else setError(e instanceof Error?e.message:t('Check your input.'));
    }
    finally{lock.current=false;setBusy(false);}
  }

  function submit(e:FormEvent<HTMLFormElement>){
    e.preventDefault();
    const data=new FormData(e.currentTarget);
    const value=(key:string)=>String(data.get(key)||'').trim();
    try{
      if(mode==='customer'){
        const digits=asciiDigits(value('phone'));
        if(digits&&!/^\+?[0-9][0-9 ()-]{4,23}[0-9]$/.test(digits))throw new Error(t('Enter digits from the phone number, including its country code if needed.'));
        void mutate('/workspace/customers',{name:value('name'),phone:digits||null},t('Customer saved.'));
      }else if(mode==='debt'){
        void mutate('/workspace/receivables',{customer_id:value('customer'),number:value('number')||null,currency,
          amount_minor:parseMinor(asciiDigits(value('amount'))),issued_on:value('issued'),due_on:value('due')},t('Receivable saved.'));
      }else if(mode==='collect'&&selected){
        void mutate('/workspace/receivables/'+selected.id+'/settlements',{amount_minor:parseMinor(asciiDigits(value('amount'))),
          received_on:value('received')},t('Receipt recorded.'));
      }
    }catch(e){setError(e instanceof Error?e.message:t('Check your input.'));}
  }
  const names=new Map(book.customers.map(c=>[c.id,c.name||t('Customer')]));
  return <section className="panel data-entry">
    <h2>{t('Customer receivables')}</h2>
    <p className="muted">{t('Enter each customer debt once. Its unpaid balance appears in cash forecasts until you record a receipt here.')}</p>
    {message&&<p role="status" className="notice">{message}</p>}
    {error&&<p role="alert" className="error">{error}</p>}
    <div className="form-row">
      <button type="button" className="secondary" disabled={busy} onClick={()=>setMode('customer')}>{t('Add customer')}</button>
      <button type="button" disabled={busy||!book.customers.length} onClick={()=>setMode('debt')}>{t('Add receivable')}</button>
    </div>
    {mode&&<form key={mode+(selected?.id||'')} onSubmit={submit}>
      <h3>{mode==='customer'?t('New customer'):mode==='debt'?t('New receivable'):t('Record receipt')}</h3>
      {mode==='customer'&&<>
        <label>{t('Customer name')}<input required name="name" maxLength={100} autoComplete="off"/></label>
        <label>{t('Phone for reminders (optional)')}<input name="phone" type="tel" maxLength={25} autoComplete="off"/></label>
      </>}
      {mode==='debt'&&<>
        <label>{t('Customer')}<select name="customer" required>{book.customers.map(c=><option key={c.id} value={c.id}>{names.get(c.id)}</option>)}</select></label>
        <label>{t('Invoice reference (optional)')}<input name="number" maxLength={80}/></label>
        <label>{t('Issued on')}<input name="issued" type="date" required max={today()} defaultValue={today()}/></label>
        <label>{t('Due date')}<input name="due" type="date" required defaultValue={today()}/></label>
      </>}
      {mode==='collect'&&selected&&<>
        <p>{names.get(selected.customer_id)} · {t('Still owed')} {nativeMoney(selected.open_minor)}</p>
        <label>{t('Received on')}<input name="received" type="date" required min={selected.issued_on} max={today()} defaultValue={today()}/></label>
      </>}
      {mode!=='customer'&&<label>{t('Amount')} ({currency})<input name="amount" required inputMode="decimal"/></label>}
      <div className="form-row"><button disabled={busy}>{mode==='collect'?t('Confirm receipt'):t('Save')}</button>
      <button type="button" className="secondary" disabled={busy} onClick={()=>setMode(null)}>{t('Cancel')}</button></div>
    </form>}
    {!!book.receivables.length&&<ul className="people">{book.receivables.map(debt=><li key={debt.id}>
      <div><strong>{names.get(debt.customer_id)}{debt.number&&' · '+debt.number}</strong>
      <small>{t('Due date')} {formatDate(debt.due_on)} · {t('Still owed')} {nativeMoney(debt.open_minor)} / {nativeMoney(debt.amount_minor)}</small>
      {debt.open_minor>0&&<><PaymentTiming outlook={book.collection_outlook?.[debt.id]}/><ReminderDraft key={debt.open_minor} request={request} id={debt.id}/></>}</div>
      {debt.open_minor>0?<button type="button" className="secondary" disabled={busy} onClick={()=>{setSelected(debt);setMode('collect');}}>{t('Record receipt')}</button>:<span>{t('Paid in full')}</span>}
    </li>)}</ul>}
    {book.customers.some(c=>c.phone)&&<details><summary>{t('Customer contact details')}</summary>
      <ul className="people">{book.customers.filter(c=>c.phone).map(c=><li key={c.id}><div><strong>{c.name}</strong><small>{c.phone}</small></div>
        <button disabled={busy} className="secondary" type="button" onClick={()=>{if(window.confirm(t('Permanently erase this phone number?')))
          void mutate('/workspace/customers/'+c.id+'/forget-phone',{},t('Phone number erased.'),false);}}>{t('Erase phone number')}</button></li>)}</ul></details>}
  </section>;
}
