import {useEffect,useState} from 'react';
import {t} from './i18n';
import {formatDate} from './format';
import {ANDROID_APP_URL} from './apiBase';

const taka=(minor:number)=>'৳'+(minor/100).toLocaleString('en-IN',{maximumFractionDigits:2});

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Draft={id:string;source:string;amount_minor:number;direction:'inflow'|'outflow';party:string;day:string};
type Status={enabled:boolean;last_seen:number|null;pending:Draft[]};

export function SmsInbox({request}:{request:Request}){
  const [status,setStatus]=useState<Status|null>(null);
  const [url,setUrl]=useState('');
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const path='/inbox/sms';
  async function run(action:()=>Promise<void>){
    setBusy(true);setMessage('');
    try{await action();}catch(e){setMessage(e instanceof Error?e.message:t('Request failed.'));}finally{setBusy(false);}
  }
  const load=async()=>setStatus(await request<Status>(path));
  useEffect(()=>{void run(load);},[]);// eslint-disable-line react-hooks/exhaustive-deps
  const act=(id:string,action:'save'|'skip')=>run(async()=>{await request(`${path}/drafts/${id}/${action}`,{});await load();});
  return <section id="payment-sms" className="panel channel"><h2>{t('Payment SMS')}</h2>
    <p className="muted">{t('Forward bKash, Nagad, Rocket and bank SMS from your Android phone. Capzoryx reads each payment and asks before saving it. OTP and PIN messages are ignored.')}</p>
    {url?<>
      <ol className="steps">
        <li>{t('Install a free SMS forwarding app, for example “SMS to URL Forwarder”.')}</li>
        <li>{t('Add a rule that forwards messages from bKash, Nagad, Rocket and your bank to this link:')}
          <input readOnly value={url} onFocus={e=>e.currentTarget.select()} aria-label={t('Forwarding link')}/></li>
        <li>{t('Keep this link private. Anyone with it can send drafts to your inbox.')}</li>
      </ol>
      <div className="actions"><button className="secondary" onClick={()=>void navigator.clipboard?.writeText(url)}>{t('Copy link')}</button>
        <button className="ghost" onClick={()=>setUrl('')}>{t('Done')}</button></div></>:
    status?.enabled?<p className="channel-state"><span className="dot on" aria-hidden="true"/>{status.last_seen?t('Receiving SMS'):t('Waiting for the first SMS')}</p>:null}
    {!!status?.pending.length&&<ul className="sms-list">{status.pending.map(d=><li key={d.id}>
      <div><b>{d.source}</b> · {d.direction==='inflow'?t('Received'):t('Paid')} <b>{taka(d.amount_minor)}</b>
        {d.party&&<> · {d.party}</>}<small>{formatDate(d.day)}</small></div>
      <div className="actions"><button disabled={busy} onClick={()=>void act(d.id,'save')}>{t('Save')}</button>
        <button className="ghost" disabled={busy} onClick={()=>void act(d.id,'skip')}>{t('Skip')}</button></div></li>)}</ul>}
    {!url&&<div className="actions">
      <button className={status?.enabled?'secondary':''} disabled={busy} onClick={()=>void run(async()=>{setUrl((await request<{url:string}>(path+'/link',{})).url);await load();})}>{status?.enabled?t('Make a new link'):t('Set up SMS forwarding')}</button>
      {status?.enabled&&<button className="ghost" disabled={busy} onClick={()=>void run(async()=>{await request(path+'/unlink',{});await load();})}>{t('Turn off')}</button>}
    </div>}
    {message&&<p role="status">{message}</p>}
  </section>;
}

/** Android app: read bKash, Nagad, Rocket and bank SMS on this phone, with the owner's permission. */
export function PhoneSms({request,scope,onAdded}:{request:Request;scope:{business_id:string;user_id:string};onAdded?:(count:number)=>void}){
  const [on,setOn]=useState<boolean|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  useEffect(()=>{void import('./nativeSms').then(async m=>setOn(m.smsSettings(scope).enabled&&await m.smsAccess()));},[scope.business_id,scope.user_id]);// eslint-disable-line react-hooks/exhaustive-deps
  async function turnOn(){
    setBusy(true);setMessage('');
    try{
      const m=await import('./nativeSms');
      if(!await m.enableSms(scope)){setMessage(t('SMS permission was not given. You can allow it later in Android Settings → Apps → Capzoryx → Permissions.'));return;}
      setOn(true);
      const added=await m.syncSms(request,scope);
      setMessage(added?t('{count} payments found in your SMS. Review them under Invoices.',{count:added}):t('Done. New bKash, Nagad and bank payments will appear for review by themselves.'));
      if(added)onAdded?.(added);
    }catch(e){setMessage(e instanceof Error?e.message:t('Request failed.'));}finally{setBusy(false);}
  }
  async function turnOff(){(await import('./nativeSms')).disableSms(scope);setOn(false);setMessage(t('SMS reading is off. Nothing more is read.'));}
  return <section className="panel channel phone-sms"><h2>{t('Read payment SMS on this phone')}</h2>
    <p className="muted">{t('Capzoryx finds bKash, Nagad, Rocket and bank payment messages on this phone and adds them for you to review. Other messages are skipped, and OTP or PIN messages are never saved.')}</p>
    {on?<p className="channel-state"><span className="dot on" aria-hidden="true"/>{t('On: new payment SMS are added for review')}</p>:null}
    <div className="actions">{on?<button className="ghost" onClick={()=>void turnOff()}>{t('Turn off')}</button>
      :<button disabled={busy||on===null} onClick={()=>void turnOn()}>{busy?t('Reading SMS…'):t('Turn on SMS reading')}</button>}</div>
    {message&&<p role="status">{message}</p>}
  </section>;
}

/** Browsers cannot read SMS. On the website (also when installed), payment SMS reading comes from the Android app. */
export function GetAndroidApp(){
  return <section className="panel channel phone-sms"><h2>{t('Read payment SMS automatically')}</h2>
    <p className="muted">{t('A website cannot read your SMS, even when installed. The Capzoryx Android app can: it finds bKash, Nagad, Rocket and bank payments on your phone and adds them for review, with your permission.')}</p>
    {ANDROID_APP_URL?<><ol className="steps"><li>{t('Download the app and open the file.')}</li><li>{t('If Android asks, allow installing apps from this source.')}</li><li>{t('Sign in with the same account, open Add, and turn on SMS reading.')}</li></ol>
      <div className="actions"><a className="button" href={ANDROID_APP_URL} rel="noopener">{t('Download the Android app')}</a></div></>
      :<p>{t('The Android download is pending. You can paste a payment receipt in the wallet feed.')}</p>}
  </section>;
}
