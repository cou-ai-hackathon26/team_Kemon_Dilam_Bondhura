import {useEffect,useRef,useState,type FormEvent} from 'react';
import {ApiError} from './api';
import {useCurrency} from './Currency';
import {formatDate} from './format';
import {t,T} from './i18n';
import {PaymentProfile} from './WalletProfile';

export type WalletRequest = <T,>(path:string,body?:unknown)=>Promise<T>;
type Candidate={receivable_id:string;customer:string|null;number:string|null;open_minor:number;due_on:string};
type WalletEvent={id:string;provider:string;txn_id:string|null;amount_minor:number;direction:string;counterparty:string;occurred_on:string;flags:string[];status:string;candidates:Candidate[]};
type Feed={events:WalletEvent[];next_cursor:number|null};
const warnings: Record<string,string> = {
  UNKNOWN_PROVIDER:T('Unrecognized wallet format.'), CHECK_TRANSACTION_ID:T('Check the transaction ID in your wallet.'),
  BALANCE_MISMATCH:T('The balances in this message do not reconcile.'), UNVERIFIED_STATUS:T('The message describes an incomplete or reversed payment.'),
  FUTURE_DATE:T('The message has a future date.'), DATE_NOT_IN_MESSAGE:T('No date was found. The date shown is the day you shared the message.'),
};
const labels:Record<string,string>={bkash:'bKash',nagad:'Nagad',rocket:'Rocket'};

const errors:Record<string,string>={
  WALLET_ALREADY_SHARED:T('This wallet transaction is already in your feed.'),
  WALLET_SENSITIVE_MESSAGE:T('Remove verification codes and PINs before sharing a payment message.'),
  WALLET_AMOUNT_UNCLEAR:T('The receipt amount is unclear. Check the wallet notification.'),
  WALLET_DIRECTION_UNCLEAR:T('Choose a message that clearly identifies money received or sent.'),
};
function errorText(error:unknown){
  return error instanceof ApiError&&errors[error.code]?t(errors[error.code]):error instanceof Error?error.message:t('Request failed.');
}

function Review({event,request,onChange}:{event:WalletEvent;request:WalletRequest;onChange:()=>Promise<void>}){
  const {nativeMoney}=useCurrency();
  const [choice,setChoice]=useState('');
  const [checked,setChecked]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const canConfirm=event.direction==='in'&&!event.flags.some(f=>['FUTURE_DATE','BALANCE_MISMATCH','UNVERIFIED_STATUS'].includes(f));
  async function decide(action:'confirm'|'dismiss'){
    if(busy)return;
    setBusy(true);setError('');
    try{
      const body=action==='confirm'?{receivable_id:choice==='cash'?null:choice,checked_wallet:checked}:{};
      await request('/payments/'+event.id+'/'+action,body);
      await onChange();
    }catch(e){setError(errorText(e));}
    finally{setBusy(false);}
  }
  return <article className="panel"><h3>{labels[event.provider]||t('Unknown wallet')} · {nativeMoney(event.amount_minor)}</h3>
    <p>{formatDate(event.occurred_on)}{event.counterparty&&' · '+event.counterparty}{event.txn_id&&' · '+event.txn_id}</p>
    {event.status==='new'?<>
      {event.flags.map(f=><p key={f} className="notice">{t(warnings[f]||'Review message details.')}</p>)}
      {event.direction==='out'&&<p>{t('This is an outgoing payment.')}</p>}
      {canConfirm&&<>
        <label>{t('Apply this receipt to')}<select value={choice} onChange={e=>{setChoice(e.target.value);setChecked(false);}}>
          <option value="">{t('Choose a receivable or cash entry')}</option>
          {event.candidates.map(c=><option key={c.receivable_id} value={c.receivable_id}>{c.customer||t('Customer')} · {c.number||formatDate(c.due_on)} · {nativeMoney(c.open_minor)}</option>)}
          <option value="cash">{t('Cash only (no receivable)')}</option>
        </select></label>
        <label className="check"><input type="checkbox" checked={checked} onChange={e=>setChecked(e.target.checked)}/>{t('I checked the amount, date and transaction in my wallet.')}</label>
        <button type="button" disabled={!checked||!choice||busy} onClick={()=>void decide('confirm')}>{t('Record wallet receipt')}</button>
      </>}
      <button type="button" className="secondary" disabled={busy} onClick={()=>void decide('dismiss')}>{t('Dismiss')}</button>
    </>:<p>{event.status==='confirmed'?t('Receipt recorded.'):t('Dismissed')}</p>}
    {error&&<p role="alert" className="error">{error}</p>}
  </article>;
}

export default function WalletPayments({request,onSaved,businessId,userId}:{request:WalletRequest;onSaved:()=>Promise<void>;businessId:string;userId:string}){
  const [events,setEvents]=useState<WalletEvent[]>([]);
  const [cursor,setCursor]=useState<number|null>(null);
  const [text,setText]=useState('');
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  const [profile,setProfile]=useState(false);
  const stable=useRef(request);
  useEffect(()=>{
    let active=true;
    const load=()=>stable.current<Feed>('/payments/feed').then(r=>{
      if(active){setEvents(r.events);setCursor(r.next_cursor);}
    }).catch(e=>{if(active)setError(e.message);});
    void load();
    window.addEventListener('capzoryx-wallet-updated',load);
    return ()=>{active=false;window.removeEventListener('capzoryx-wallet-updated',load);};
  },[]);
  async function reload(){
    const data=await request<Feed>('/payments/feed');
    setEvents(data.events);setCursor(data.next_cursor);
  }
  async function more(){
    if(busy||!cursor)return;
    setBusy(true);setError('');
    try{
      const data=await request<Feed>('/payments/feed?before='+cursor);
      setEvents(previous=>[...previous,...data.events]);setCursor(data.next_cursor);
    }catch(e){setError(errorText(e));}
    finally{setBusy(false);}
  }
  async function share(e:FormEvent){
    e.preventDefault();if(busy)return;
    setError('');setBusy(true);
    try{
      await request('/payments/share',{text,expected_business_id:businessId,expected_user_id:userId});
      setText('');await reload();
    }catch(e){setError(errorText(e));}
    finally{setBusy(false);}
  }
  return <section className="panel data-entry"><h2>{t('Wallet payments')}</h2>
    <form onSubmit={share}><label>{t('Payment message from your wallet')}<textarea maxLength={2000} required rows={3} value={text} onChange={e=>setText(e.target.value)}/></label>
      <p className="muted">{t('Paste a BDT payment notification. Leave out PINs and verification codes. Check the transaction in your wallet before recording it.')}</p>
      <button disabled={busy||!text.trim()}>{t('Review message')}</button></form>
    {error&&<p role="alert" className="error">{error}</p>}
    {events.map(event=><Review key={event.id} event={event} request={request} onChange={async()=>{await reload();await onSaved();}}/>)}
    {cursor&&<button type="button" className="secondary" disabled={busy} onClick={()=>void more()}>{t('Earlier wallet messages')}</button>}
    <button type="button" className="secondary" aria-expanded={profile} onClick={()=>setProfile(!profile)}>{t('Merchant payment details')}</button>
    {profile&&<PaymentProfile request={request} onSaved={onSaved}/>}
  </section>;
}
