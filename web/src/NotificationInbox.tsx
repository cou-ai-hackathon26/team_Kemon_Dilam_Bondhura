import {useEffect,useRef,useState} from 'react';
import {EmailAlerts} from './EmailAlerts';
import {WebPushControl} from './WebPushControl';
import {formatTime} from './format';
import {t,T} from './i18n';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Item={id:string;kind:string;page:string;sent_at:number;read:boolean};
type Inbox={items:Item[];enabled:boolean;quiet_start_min:number;quiet_end_min:number};
const labels:Record<string,string>={
  collections_review:T('Your overdue collections are ready to review'),
  boishakh_planning:T('Review your cash plan for Pohela Boishakh in 30 days'),
  eid_planning:T('Review your cash plan for the Eid planning date in 30 days'),
  approval_needed:T('A payment needs approval'),new_device:T('Sign-in from a new browser'),
  payment_shared:T('A shared payment is ready for review'),payment_profile_changed:T('Payment details changed'),
  duplicate_bill:T('Review possible duplicate bills'),bill_due:T('A supplier bill is due soon'),
  customer_overdue:T('A customer payment is overdue'),cash_breach:T('Your cash reserve needs attention'),morning_briefing:T('Your morning cash briefing is ready'),
  market_risk:T('A market price is putting your profit at risk')
};
const timeText=(minutes:number)=>[Math.floor(minutes/60),minutes%60].map(n=>String(n).padStart(2,'0')).join(':');
const minutes=(time:string)=>Number(time.slice(0,2))*60+Number(time.slice(3));

export function NotificationInbox({request,navigate,scope}:{request:Request;navigate:(page:string,kind:string)=>void;scope:string}){
  const [inbox,setInbox]=useState<Inbox|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [start,setStart]=useState('21:00');
  const [end,setEnd]=useState('08:00');
  const [enabled,setEnabled]=useState(true);
  const initial=useRef(request);
  function show(value:Inbox){
    setInbox(value);setEnabled(value.enabled);setStart(timeText(value.quiet_start_min));setEnd(timeText(value.quiet_end_min));
  }
  useEffect(()=>{let active=true;initial.current<Inbox>('/notifications').then(x=>{if(active)show(x);}).catch(e=>{if(active)setMessage(e.message);});return ()=>{active=false;};},[]);
  async function run(action:()=>Promise<void>){
    setBusy(true);setMessage('');
    try{await action();}catch(e){setMessage(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  return <section className="panel">
    <div className="panel-heading"><h2>{t('Notification inbox')}</h2>
      <button type="button" className="secondary" disabled={busy} onClick={()=>void run(async()=>show(await request<Inbox>('/notifications/refresh',{})))}>{t('Refresh alerts')}</button></div>
    {inbox&&inbox.items.length===0&&<p>{t('Your inbox is empty.')}</p>}
    {inbox&&inbox.items.length>0&&<ul className="people">{inbox.items.map(item=><li key={item.id}>
      <div><strong>{t(labels[item.kind]||T('Workspace update'))}</strong>
        <small>{formatTime(item.sent_at)} · {item.read?t('Read'):t('Unread')}</small></div>
      <button type="button" disabled={busy} onClick={()=>void run(async()=>{await request('/notifications/'+item.id+'/read',{});navigate(item.page,item.kind);})}>{t('Review')}</button>
    </li>)}</ul>}
    <EmailAlerts request={request} scope={scope}/>
    <WebPushControl request={request} scope={scope}/>
    {inbox&&<details><summary>{t('Notification preferences')}</summary>
      <form onSubmit={e=>{e.preventDefault();void run(async()=>{
        await request('/notifications/preferences',{enabled,quiet_start_min:minutes(start),quiet_end_min:minutes(end)});
        setMessage(t('Notification preferences saved.'));
      });}}>
        <label className="check"><input type="checkbox" checked={enabled} onChange={e=>setEnabled(e.target.checked)}/>{t('Receive in-app notifications')}</label>
        <div className="form-row"><label>{t('Quiet hours start (Dhaka time)')}<input type="time" required value={start} onChange={e=>setStart(e.target.value)}/></label>
          <label>{t('Quiet hours end (Dhaka time)')}<input type="time" required value={end} onChange={e=>setEnd(e.target.value)}/></label></div>
        <button type="submit" disabled={busy}>{t('Save preferences')}</button>
      </form></details>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
