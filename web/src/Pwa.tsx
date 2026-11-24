import {ANDROID_APP_URL,androidPhone} from './apiBase';
import {useEffect,useRef,useState,type FormEvent} from 'react';
import {t} from './i18n';
import {ApiError,parseMinor,money} from './api';
import {asciiDigits,slug} from './recordParser';
import type {Owner,QueuedRecord} from './pwaStore';
import type {Session} from './types';

type Request = <T,>(path:string,body?:unknown)=>Promise<T>;
type InstallEvent=Event&{prompt:()=>Promise<void>;userChoice:Promise<{outcome:string}>};
let installEvent:InstallEvent|null=null;
window.addEventListener('beforeinstallprompt',e=>{
  e.preventDefault();installEvent=e as InstallEvent;window.dispatchEvent(new Event('install-available'));
});
window.addEventListener('appinstalled',()=>{installEvent=null;window.dispatchEvent(new Event('install-available'));});

export function InstallControl(){
  const [available,setAvailable]=useState(!!installEvent);
  const [update,setUpdate]=useState<ServiceWorker|null>(null);
  useEffect(()=>{
    const controller=new AbortController();
    const options={signal:controller.signal};
    window.addEventListener('install-available',()=>setAvailable(!!installEvent),options);
    if('serviceWorker' in navigator){
      navigator.serviceWorker.ready.then(reg=>{
        if(controller.signal.aborted)return;
        if(reg.waiting&&navigator.serviceWorker.controller)setUpdate(reg.waiting);
        reg.addEventListener('updatefound',()=>{
          const worker=reg.installing;
          worker?.addEventListener('statechange',()=>{
            if(worker.state==='installed'&&navigator.serviceWorker.controller)setUpdate(worker);
          },options);
        },options);
      }).catch(()=>{});
    }
    return ()=>controller.abort();
  },[]);
  const apple=/iPhone|iPad|iPod/.test(navigator.userAgent)||(navigator.platform==='MacIntel'&&navigator.maxTouchPoints>1);
  return <>{androidPhone&&ANDROID_APP_URL&&<a className="button secondary" href={ANDROID_APP_URL} rel="noopener">{t('Get the Android app (reads payment SMS)')}</a>}{available&&<button className="secondary" onClick={async()=>{const event=installEvent;if(!event)return;
    installEvent=null;setAvailable(false);await event.prompt();await event.userChoice;
  }}>{t('Install Capzoryx')}</button>}
  {apple&&!window.matchMedia('(display-mode: standalone)').matches&&<p>{t('On iPhone, open Share and choose Add to Home Screen.')}</p>}
  {update&&<button className="secondary" onClick={()=>{
    navigator.serviceWorker.addEventListener('controllerchange',()=>location.reload(),{once:true});update.postMessage('ACTIVATE_UPDATE');
  }}>{t('New version available. Reload app')}</button>}</>;
}

const today=()=>new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Dhaka',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
export function OfflineCapture({onClose}:{onClose?:()=>void}={}){
  const [owner,setOwner]=useState<Owner|null>(null);
  const [busy,setBusy]=useState(false),[note,setNote]=useState(''),[count,setCount]=useState(0);
  const [kind,setKind]=useState(new URLSearchParams(location.search).get('open')==='bill'?'invoice':'cash');
  useEffect(()=>{
    let live=true;
    const store=window.capzoryxStore;
    (async()=>{
      const saved=await store.scope();
      if(!saved)return;
      const drafts=await store.drafts(saved);
      if(live){setOwner(saved);setCount(drafts.length);}
    })().catch(()=>{if(live)setNote(t('Device storage is unavailable.'));});
    return ()=>{live=false;};
  },[]);
  async function save(e:FormEvent<HTMLFormElement>){
    e.preventDefault();if(!owner||busy)return;
    const form=e.currentTarget, data=new FormData(form);
    const value=(name:string)=>String(data.get(name)||'').trim();
    setBusy(true);
    try{
      const amount_minor=parseMinor(asciiDigits(value('amount')),owner.currency);
      if(amount_minor<=0)throw new Error(t('Enter a positive amount.'));
      const draft:QueuedRecord={user_id:owner.user_id,business_id:owner.business_id,currency:owner.currency,request_id:crypto.randomUUID(),amount_minor,effective_on:value('date'),
        kind:kind==='invoice'?'invoice':'cash',direction:kind==='cash'&&value('direction')==='outflow'?'outflow':'inflow'};
      if(kind==='invoice'){
        const supplier=value('supplier');
        if(!supplier)throw new Error(t('Check the supplier name and due date.'));
        draft.vendor_id=slug(supplier);
        if(!draft.vendor_id){
          const digest=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(supplier.normalize('NFC')));
          draft.vendor_id='vendor-'+Array.from(new Uint8Array(digest).slice(0,12),n=>n.toString(16).padStart(2,'0')).join('');
        }
        draft.invoice_number=value('number');draft.due_on=value('due');
        if(!draft.vendor_id||draft.due_on<draft.effective_on)throw new Error(t('Check the supplier name and due date.'));
      }
      await window.capzoryxStore.saveDraft(draft);form.reset();setCount(n=>n+1);setNote(t('Draft saved on this device.'));
    }catch(e){setNote(e instanceof Error?e.message:t('Device storage is unavailable.'));}
    finally{setBusy(false);}
  }
  return <main className="panel data-entry"><h1>{t('Offline capture')}</h1>
    {location.pathname==='/share'&&<p>{t('Shared text is on this device. Reconnect to review it.')}</p>}
    {owner?<><p>{t('Saving queues this record for your workspace. It will sync after you reconnect and sign in.')}</p>
    <p>{t('Queued records: {count}',{count})}</p>
    <form onSubmit={e=>void save(e)}><label>{t('Record type')}<select value={kind} onChange={e=>setKind(e.target.value)}>
      <option value="cash">{t('Cash activity')}</option><option value="invoice">{t('Unpaid supplier invoice')}</option></select></label>
    <label>{t('Amount ({currency})',{currency:owner.currency})}<input name="amount" inputMode="decimal" required/></label>
    <label>{t('Activity date')}<input name="date" type="date" max={today()} defaultValue={today()} required/></label>
    {kind==='cash'?<label>{t('Direction')}<select name="direction"><option value="inflow">{t('Money in')}</option><option value="outflow">{t('Money out')}</option></select></label>:<>
    <label>{t('Supplier name')}<input name="supplier" maxLength={80} required/></label>
    <label>{t('Invoice reference')}<input name="number" maxLength={80} required/></label>
    <label>{t('Due date')}<input name="due" type="date" defaultValue={today()} required/></label></>}
    <button disabled={busy}>{t('Save and sync on reconnect')}</button></form></>:<p>{t('Reconnect and open your workspace first.')}</p>}
    <p role="status">{note}</p>{onClose&&<button type="button" className="secondary" onClick={onClose}>{t('Back to the app')}</button>}<button type="button" className="secondary" onClick={()=>location.reload()}>{t('Try again')}</button><InstallControl/></main>;
}

export function CaptureSync({session,currency,request,onSaved,onInvoices}:{session:Session;currency:string;request:Request;onSaved:()=>Promise<void>;onInvoices:()=>void}){
  const [note,setNote]=useState('');
  const [shared,setShared]=useState<{id:string;text:string}|null>(null);
  const [blocked,setBlocked]=useState<QueuedRecord|null>(null);
  const [retry,setRetry]=useState(0),[sending,setSending]=useState(false);
  const actions=useRef({request,onSaved,onInvoices});actions.current={request,onSaved,onInvoices};
  useEffect(()=>{
    let live=true,running=false;
    const store=window.capzoryxStore;
    const owner={business_id:session.business_id,user_id:session.user_id,currency};
    const id=location.pathname==='/share'?new URLSearchParams(location.search).get('id'):null;
    async function sync(){
      if(running||!live)return;
      running=true;
      try{
        if(session.role!=='owner'&&session.role!=='bookkeeper'){await store.setScope(null);return;}
        await store.setScope({...owner,role:session.role});
        navigator.serviceWorker?.controller?.postMessage('RESTORE_SHELL');
        if(session.role==='owner'&&id&&/^[a-f0-9-]{36}$/.test(id)){
          const item=await store.getShare(id);
          if(live&&item&&(!item.owner||store.same(item.owner,owner)))setShared({id:item.id,text:item.text});
          else if(live)setNote(t('This shared item is unavailable in this workspace.'));
        }
        let changed=false;
        for(const draft of await store.drafts(owner)){
          if(!live||!navigator.onLine)break;
          try{
            await actions.current.request('/workspace/offline-records',draft);
          }catch(e){
            if(live){setNote(e instanceof Error?e.message:t('Draft is waiting to sync.'));setBlocked(draft);}
            break;
          }
          await store.removeDraft(draft.request_id);
          changed=true;if(live)setBlocked(null);
        }
        if(changed&&live){await actions.current.onSaved();if(live)setNote(t('Offline records synced.'));}
      }catch{if(live)setNote(t('Device storage is unavailable.'));}
      finally{running=false;}
    }
    void sync();window.addEventListener('online',sync);
    return ()=>{live=false;window.removeEventListener('online',sync);};
  },[session.business_id,session.user_id,session.role,currency,retry]);
  async function finishShare(discard=false){
    if(!shared||sending||session.role!=='owner')return;
    setSending(true);
    try{
      if(!discard)try{
        await request('/payments/share',{text:shared.text,expected_business_id:session.business_id,expected_user_id:session.user_id});
      }catch(e){if(!(e instanceof ApiError&&e.code==='WALLET_ALREADY_SHARED'))throw e;}
      await window.capzoryxStore.removeShare(shared.id);setShared(null);history.replaceState(null,'','/');
      setNote(discard?'':t('Review the payment in Wallet payments.'));
      if(!discard){onInvoices();window.dispatchEvent(new Event('capzoryx-wallet-updated'));}
    }catch(e){setNote(e instanceof Error?e.message:t('Draft is waiting to sync.'));}
    finally{setSending(false);}
  }
  return <>{session.role==='owner'&&shared&&<section className="panel"><h2>{t('Review shared payment text')}</h2><p>{shared.text}</p>
    <button disabled={sending} onClick={()=>void finishShare()}>{t('Review in Wallet payments')}</button>
    <button disabled={sending} className="secondary" onClick={()=>void finishShare(true)}>{t('Discard')}</button></section>}
    {note&&<p role="status">{note}</p>}
    {blocked&&<section className="panel"><p>{t('Queued record')} · {blocked.effective_on} · {money(blocked.amount_minor,blocked.currency)}</p>
    <button onClick={()=>{setBlocked(null);setRetry(n=>n+1);}}>{t('Retry sync')}</button>
    <button className="secondary" onClick={async()=>{if(window.confirm(t('Discard this unsynced record?'))){await window.capzoryxStore.removeDraft(blocked.request_id);setBlocked(null);setNote('');setRetry(n=>n+1);}}}>{t('Discard')}</button></section>}</>;
}
