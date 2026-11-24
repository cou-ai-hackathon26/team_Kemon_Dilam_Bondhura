import {useEffect,useRef,useState} from 'react';
import {accountRequest} from './api';
import {t} from './i18n';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Preferences={configured:boolean;verified:boolean;enabled:boolean;user_id:string;business_id:string};

export function EmailAlerts({request,scope}:{request:Request;scope:string}){
  const [settings,setSettings]=useState<Preferences|null>(null);
  const [busy,setBusy]=useState(false),[error,setError]=useState('');
  const latest=useRef(request);
  latest.current=request;
  useEffect(()=>{
    let active=true;
    setSettings(null);setError('');
    latest.current<Preferences>('/notifications/bill-email').then(value=>{
      if(active&&value.business_id+':'+value.user_id===scope)setSettings(value);
    }).catch(e=>{if(active)setError(e instanceof Error?e.message:t('Request failed.'));});
    return ()=>{active=false;};
  },[scope]);
  async function toggle(){
    if(!settings||busy)return;
    setBusy(true);setError('');
    try{
      const next=await request<Preferences>('/notifications/bill-email',{enabled:!settings.enabled,
        expected_business_id:settings.business_id,expected_user_id:settings.user_id});
      if(next.business_id+':'+next.user_id===scope)setSettings(next);
    }catch(e){setError(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  return <details><summary>{t('Email bill reminders')}</summary>
    {settings&&<><p>{t('Get a count of upcoming supplier bills at your verified email address. Your quiet hours apply.')}</p>
      {!settings.configured&&<p>{t('Email reminders are currently unavailable.')}</p>}
      {!settings.verified&&<p>{t('Verify your email in Settings to receive reminders.')}</p>}
      <button className="secondary" type="button" disabled={busy||(!settings.enabled&&(!settings.configured||!settings.verified))}
        aria-pressed={settings.enabled} onClick={()=>void toggle()}>{settings.enabled?t('Turn off email bill reminders'):t('Turn on email bill reminders')}</button></>}
    {error&&<p role="alert">{error}</p>}
  </details>;
}

export function EmailUnsubscribe(){
  const [token]=useState(()=>location.hash.slice(1));
  const [busy,setBusy]=useState(false),[done,setDone]=useState(false),[error,setError]=useState('');
  useEffect(()=>{history.replaceState(null,'',location.pathname);},[]);
  async function stop(){
    setBusy(true);setError('');
    try{await accountRequest('/notifications/bill-email/unsubscribe',{token});setDone(true);}
    catch(e){setError(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  return <main className="panel"><h1>{t('Email bill reminders')}</h1>
    {done?<p role="status">{t('These bill reminder emails are turned off.')}</p>:
      /^[a-f0-9]{32}\.[a-f0-9]{64}$/.test(token)?<button type="button" disabled={busy} onClick={()=>void stop()}>{t('Unsubscribe from these bill reminders')}</button>:
        <p>{t('Open the unsubscribe link in your reminder email.')}</p>}
    {error&&<p role="alert">{error}</p>}</main>;
}
