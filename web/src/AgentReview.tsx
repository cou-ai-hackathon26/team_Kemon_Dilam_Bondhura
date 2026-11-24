import {useEffect,useRef,useState} from 'react';
import {ApiError,money} from './api';
import {formatDate,prettyVendor} from './format';
import type {HomeNavigate} from './HomeActions';
import {t,useI18n} from './i18n';
import {ReminderDraft} from './ReminderDraft';
import type {Forecast,Invoice} from './types';

type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
type Collection={receivable_id:string;customer_name:string;open_minor:number;due_on:string};
type Review={as_of:string;currency:string;collections:Collection[]};

function printDraft(draft:string){
  document.querySelector('body>.agent-print-draft')?.remove();
  const page=document.createElement('article');
  page.className='agent-print-draft';
  page.lang=document.documentElement.lang;
  page.textContent=draft;
  document.body.appendChild(page);
  window.addEventListener('afterprint',()=>page.remove(),{once:true});
  window.print();
}

function SupplierDraft({invoices,asOf,currency}:{invoices:Invoice[];asOf:string;currency:string}){
  const [id,setId]=useState('');
  const [extension,setExtension]=useState('');
  const [notice,setNotice]=useState('');
  const open=invoices.filter(bill=>bill.status==='open').sort((a,b)=>a.due_on.localeCompare(b.due_on));
  const bill=open.find(item=>item.invoice_id===id);
  const base=bill?.due_on&&bill.due_on>asOf?bill.due_on:asOf;
  const next=new Date(base+'T00:00:00Z');
  next.setUTCDate(next.getUTCDate()+1);
  const minimum=next.toISOString().slice(0,10);
  const draft=bill&&extension>=minimum?t('Hello {supplier}, our records show invoice {invoice} for {amount} with a due date of {due}. Could we extend the payment date to {requested}? Please let us know whether this works for you. Thank you.',{
    supplier:prettyVendor(bill.vendor_id),invoice:bill.invoice_number,amount:money(bill.amount_minor,currency),due:formatDate(bill.due_on),requested:formatDate(extension)
  }):'';
  async function copy(){
    try{await navigator.clipboard.writeText(draft);setNotice(t('Draft copied.'));}
    catch{setNotice(t('Select the draft text to copy it.'));}
  }
  return <details><summary>{t('Request a supplier extension')}</summary>
    {open.length===0?<p>{t('There are no open supplier bills.')}</p>:<>
      <label>{t('Supplier invoice')}<select value={id} onChange={e=>{setId(e.target.value);setExtension('');setNotice('');}}>
        <option value="">{t('Choose an invoice')}</option>
        {open.map(item=><option key={item.invoice_id} value={item.invoice_id}>{prettyVendor(item.vendor_id)} · {item.invoice_number} · {money(item.amount_minor,currency)} · {formatDate(item.due_on)}</option>)}
      </select></label>
      {bill&&<label>{t('Requested payment date')}<input type="date" min={minimum} value={extension} onChange={e=>{setExtension(e.target.value);setNotice('');}}/></label>}
      {extension&&extension<minimum&&<p role="alert">{t('Choose {date} or later.',{date:formatDate(minimum)})}</p>}
      {draft&&<><label>{t('Extension request')}<textarea readOnly rows={4} value={draft}/></label>
        <div className="actions"><button type="button" onClick={()=>void copy()}>{t('Copy draft')}</button>
        <button type="button" className="secondary" onClick={()=>printDraft(draft)}>{t('Print draft')}</button></div></>}
      {notice&&<p role="status">{notice}</p>}
    </>}
  </details>;
}

function Collections({rows,currency,request}:{rows:Collection[];currency:string;request:Request}){
  const {code}=useI18n();
  const [busy,setBusy]=useState(false);
  const [drafts,setDrafts]=useState<Record<string,string>|null>(null);
  const [language,setLanguage]=useState(code==='bn'?'bn':'en');
  async function prepare(){
    setBusy(true);
    const results=await Promise.allSettled(rows.map(row=>request<{message:string}>('/copilot/message',{receivable_id:row.receivable_id,language})));
    const messages:Record<string,string>={};
    results.forEach((result,i)=>{if(result.status==='fulfilled')messages[rows[i].receivable_id]=result.value.message;});
    setDrafts(messages);setBusy(false);
  }
  return <div><h3>{t('Overdue collections')}</h3>
    {rows.length===0?<p>{t('There are no overdue customer balances to follow up.')}</p>:<>
      <ol>{rows.map(row=><li key={row.receivable_id}><strong>{row.customer_name||t('Customer')}</strong>
        {' · '}{t('{amount} due {date}',{amount:money(row.open_minor,currency),date:formatDate(row.due_on)})}
        {drafts&&<ReminderDraft request={request} id={row.receivable_id} initial={{language,message:drafts[row.receivable_id]||''}}/>}
      </li>)}</ol>
      {drafts&&Object.keys(drafts).length<rows.length&&<p role="status">{t('Some reminders could not be prepared. Retry each one under its customer.')}</p>}
      {!drafts&&<><label>{t('Reminder language')}<select value={language} disabled={busy} onChange={e=>setLanguage(e.target.value)}><option value="bn">বাংলা</option><option value="en">English</option></select></label>
        <button type="button" disabled={busy} onClick={()=>void prepare()}>{busy?t('Preparing reminders…'):t('Review reminders ({count})',{count:rows.length})}</button></>}
    </>}
  </div>;
}

export function AgentReview({request,invoices,forecast,navigate,initiallyOpen}:{initiallyOpen:boolean;request:Request;invoices:Invoice[];forecast:Forecast|null;navigate:HomeNavigate}){
  const [review,setReview]=useState<Review|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  async function load(){
    setBusy(true);setError('');
    try{setReview(await request<Review>('/copilot/agent-review'));}
    catch(e){setError(e instanceof ApiError&&e.code==='FEATURE_PAUSED'?t('Agentic Mode is turned off for this workspace.'):e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  const initialLoad=useRef(initiallyOpen);
  useEffect(()=>{
    if(initialLoad.current){initialLoad.current=false;void load();}
  },[]);
  return <section id="agent-mode" tabIndex={-1} className="panel agent-review"  aria-labelledby="agent-review-title">
    <h2 id="agent-review-title">{t('Agentic Mode')}</h2>
    {!review&&<><p>{t('Review overdue balances, cash gaps and payments that need matching.')}</p>
      <button type="button" className="secondary" disabled={busy} onClick={()=>void load()}>{busy?t('Loading suggestions…'):t('Review suggestions')}</button></>}
    {error&&<p className="error" role="alert">{error}</p>}
    {review&&<>
      <p className="muted">{t('Review for {date}',{date:formatDate(review.as_of)})}</p>
      <Collections rows={review.collections.slice(0,3)} currency={review.currency} request={request}/>
      <div><h3>{t('Cash reserve')}</h3>{forecast?.first_shortfall_on?<>
        <p>{t('Cash falls below your reserve on {date}.',{date:formatDate(forecast.first_shortfall_on)})}</p>
        <button type="button" className="secondary" onClick={()=>navigate('Cash flow','collection-planner')}>{t('Review collection plans')}</button>
      </>:<p>{forecast?t('Your forecast stays within the reserve.'):t('Your forecast is unavailable.')}</p>}</div>
      <div><h3>{t('Match incoming payments')}</h3><div className="actions">
        {review.currency==='BDT'&&<button type="button" className="secondary" onClick={()=>navigate('Invoices','wallet-review')}>{t('Review wallet receipts')}</button>}
        <button type="button" className="secondary" onClick={()=>navigate('Data','statement-review')}>{t('Review a statement')}</button>
      </div></div>
      <SupplierDraft invoices={invoices} asOf={review.as_of} currency={review.currency}/>
    </>}
  </section>;
}
