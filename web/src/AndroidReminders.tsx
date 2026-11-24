import {useEffect,useState} from 'react';
import {t} from './i18n';
import {enableNativePush,nativePushStatus,stopNativePush} from './nativePush';

export default function AndroidReminders({scope}:{scope:string}){
  const [status,setStatus]=useState({enabled:false,available:false});
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  useEffect(()=>{
    let current=true;
    void nativePushStatus(scope).then(value=>{if(current)setStatus(value);}).catch(()=>{});
    return ()=>{current=false;};
  },[scope]);
  if(!status.available&&!status.enabled)return null;
  async function toggle(){
    setBusy(true);setMessage('');
    try{
      if(status.enabled)await stopNativePush();else await enableNativePush(scope);
      setStatus(await nativePushStatus(scope));
    }catch{setMessage(t('Android reminders could not be changed. Check notification permission and try again.'));}
    finally{setBusy(false);}
  }
  return <section className="panel"><h2>{t('Android reminders')}</h2>
    <p>{t('Receive workspace alerts on this Android device.')}</p>
    <button disabled={busy} onClick={()=>void toggle()}>{status.enabled?t('Turn off Android reminders'):t('Turn on Android reminders')}</button>
    {message&&<p role="status">{message}</p>}
  </section>;
}
