import {useState,type FormEvent} from 'react';
import {useCurrency} from './Currency';
import {prepareDocumentPhoto} from './format';
import {t,T} from './i18n';
import {readKhataRows,type KhataDraft,type Kind} from './khataRows';
import {asciiDigits,slug} from './recordParser';
import type {Session} from './types';

type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
type Book={customers:{id:string;name:string|null}[];receivables:{id:string;number:string|null;customer_id:string;open_minor:number;due_on:string}[]};
const kinds:Record<Exclude<Kind,''>,string>={inflow:T('Cash received'),outflow:T('Cash spent'),customer_credit:T('Sale on credit'),
  customer_receipt:T('Collection against a receivable'),supplier_bill:T('Unpaid supplier bill')};
async function digest(buffer:ArrayBuffer){
  return Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',buffer)),n=>n.toString(16).padStart(2,'0')).join('');
}
const blank=(line:number):KhataDraft=>({line,source:'',kind:'',amount:'',day:'',done:false});

export default function KhataPage({request,session,onSaved}:{request:Request;session:Session;onSaved:()=>Promise<void>}){
  const {currency,parseMinor,nativeMoney}=useCurrency();
  const [photo,setPhoto]=useState(''),[hash,setHash]=useState(''),[busy,setBusy]=useState(false);
  const [drafts,setDrafts]=useState<KhataDraft[]>([]),[active,setActive]=useState<number|null>(null);
  const [book,setBook]=useState<Book|null>(null),[message,setMessage]=useState(''),[error,setError]=useState('');
  const [selected,setSelected]=useState(''),[due,setDue]=useState(''),[ref,setRef]=useState(''),[vendor,setVendor]=useState('');
  const [checked,setChecked]=useState(false);
  const current=drafts.find(d=>d.line===active);
  function choose(draft:KhataDraft){
    setActive(draft.line);setSelected('');setDue('');setRef('');setVendor('');setChecked(false);setError('');
    if(!book)void request<Book>('/workspace/receivables').then(setBook).catch(e=>setError(e.message));
  }
  function edit(fields:Partial<KhataDraft>){
    setDrafts(prior=>prior.map(d=>d.line===active?{...d,...fields}:d));setChecked(false);
  }
  async function scan(file:File){
    if(busy)return;
    setError('');setMessage('');setBusy(true);
    try{
      const image=await prepareDocumentPhoto(file);
      const pageId=await digest(await file.arrayBuffer());
      setPhoto(image);setHash(pageId);setDrafts([]);setActive(null);
      const {readPhotoText}=await import('./billPhoto');
      const raw=await readPhotoText(image,'bn');
      if(raw.split('\n').length>400)throw new Error(t('Photograph a smaller section of this page.'));
      const rows=readKhataRows(raw);
      setDrafts(rows);if(rows.length)choose(rows[0]);
      if(!rows.length)setMessage(t('No rows were read. Add a missing line from the photo.'));
    }catch(e){setError(e instanceof Error?e.message:t('The photo could not be read.'));}
    finally{setBusy(false);}
  }
  function add(){
    const line=Math.max(0,...drafts.map(d=>d.line))+1;
    if(line>400)return;
    const row=blank(line);setDrafts(prior=>[...prior,row]);choose(row);
  }
  async function save(e:FormEvent){
    e.preventDefault();if(!current||current.done||!checked||!current.kind||busy)return;
    setError('');setBusy(true);
    try{
      const amount=parseMinor(asciiDigits(current.amount));
      if(amount<=0)throw new Error(t('Enter a positive amount.'));
      let supplier:string|undefined;
      if(current.kind==='supplier_bill')supplier=slug(vendor)||'vendor-'+(await digest(new TextEncoder().encode(vendor.normalize('NFC').trim()).buffer)).slice(0,24);
      const credit=current.kind==='customer_credit';
      const fields={expected_business_id:session.business_id,expected_user_id:session.user_id,page_hash:hash,line:current.line,
        checked:true,kind:current.kind,currency,amount_minor:amount,day:current.day,
        ...(credit?{customer_id:selected}:{}),...(current.kind==='customer_receipt'?{receivable_id:selected}:{}),
        ...(supplier?{vendor_id:supplier}:{}),...(credit||supplier?{due_on:due,number:ref||null}:{})};
      await request('/workspace/khata/confirm',fields);
      setDrafts(prior=>prior.map(d=>d.line===current.line?{...d,done:true}:d));setActive(null);
      setMessage(t('Line {line} recorded.',{line:current.line}));await onSaved();
      setBook(await request<Book>('/workspace/receivables'));
    }catch(e){setError(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  const names=new Map(book?.customers.map(c=>[c.id,c.name||t('Customer')]));
  return <details className="panel data-entry"><summary>{t('Photograph a khata page')}</summary>
    <p className="muted">{t('The photo is read on this device. Compare each line with the original and confirm it individually. Handwriting may need correction.')}</p>
    <label>{t('Khata page photo')}<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy}
      onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void scan(file);}}/></label>
    {busy&&<p role="status">{t('Working on this page…')}</p>}
    {photo&&<details><summary>{t('View original page')}</summary><img alt={t('Khata page for comparison')} src={photo} width="320"/></details>}
    {hash&&<button type="button" className="secondary" disabled={busy} onClick={add}>{t('Add a missing line')}</button>}
    {drafts.length>0&&<ul className="people" aria-label={t('Notebook lines')}>{drafts.map(d=><li key={d.line}>
      <div><strong>{t('Line {line}',{line:d.line})}</strong><small>{d.source||t('Manually added line')}</small></div>
      {d.done?<span>{t('Recorded')}</span>:<button type="button" className="secondary" disabled={busy} onClick={()=>choose(d)}>{t('Review line {line}',{line:d.line})}</button>}
    </li>)}</ul>}
    {current&&!current.done&&<form onSubmit={save}><h3>{t('Review line {line}',{line:current.line})}</h3>
      <label>{t('Transaction type')}<select required value={current.kind} onChange={e=>{edit({kind:e.target.value as Kind});setSelected('');}}>
        <option value="">{t('Choose transaction type')}</option>{Object.entries(kinds).map(([id,label])=><option key={id} value={id}>{t(label)}</option>)}</select></label>
      <label>{t('Activity date')}<input required type="date" value={current.day} onChange={e=>edit({day:e.target.value})}/></label>
      <label>{t('Amount ({currency})',{currency})}<input required inputMode="decimal" value={current.amount} onChange={e=>edit({amount:e.target.value})}/></label>
      {current.kind==='customer_credit'&&<label>{t('Customer')}<select required value={selected} onChange={e=>{setChecked(false);setSelected(e.target.value);}}>
        <option value="">{t('Select customer')}</option>{book?.customers.map(c=><option key={c.id} value={c.id}>{names.get(c.id)}</option>)}</select></label>}
      {current.kind==='customer_receipt'&&<label>{t('Select receivable')}<select required value={selected} onChange={e=>{setChecked(false);setSelected(e.target.value);}}>
        <option value="">{t('Select receivable')}</option>{book?.receivables.filter(r=>r.open_minor>0).map(r=><option key={r.id} value={r.id}>
          {names.get(r.customer_id)} · {r.number||r.due_on} · {nativeMoney(r.open_minor)}</option>)}</select></label>}
      {current.kind==='customer_credit'&&book&&!book.customers.length&&<p>{t('Add missing customers under Invoices before you scan this page.')}</p>}
      {current.kind==='supplier_bill'&&<label>{t('Supplier name')}<input required maxLength={80} value={vendor} onChange={e=>{setChecked(false);setVendor(e.target.value);}}/></label>}
      {(current.kind==='supplier_bill'||current.kind==='customer_credit')&&<>
        <label>{t('Due date')}<input required type="date" min={current.day} value={due} onChange={e=>{setChecked(false);setDue(e.target.value);}}/></label>
        <label>{t('Invoice reference')}<input required={current.kind==='supplier_bill'} maxLength={80} value={ref} onChange={e=>{setChecked(false);setRef(e.target.value);}}/></label></>}
      <label className="check"><input type="checkbox" checked={checked} onChange={e=>setChecked(e.target.checked)}/>{t('I checked this transaction against the notebook.')}</label>
      <button disabled={busy||!checked}>{t('Record this line')}</button>
    </form>}
    {error&&<p role="alert" className="error">{error}</p>}{message&&<p role="status">{message}</p>}
  </details>;
}
