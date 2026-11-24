import {useEffect,useState} from 'react';
import {t} from './i18n';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;

export function WebPushControl({request,scope}:{request:Request;scope:string}){
  const [available,setAvailable]=useState(false);
  const [identity,setIdentity]=useState('');
  const [publicKey,setPublicKey]=useState<Uint8Array<ArrayBuffer>|null>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const key='capzoryx-push:'+scope;
  useEffect(()=>{
    let active=true;
    if(!('serviceWorker' in navigator)||!('PushManager' in window)||!('Notification' in window))return;
    Promise.all([
      navigator.serviceWorker.getRegistration('/reminders/'),
      request<{webpush:boolean;public_key:string|null}>('/notifications/push-config')
    ]).then(async ([reg,settings])=>{
      const subscription=reg?.scope===new URL('/reminders/',location.href).href?await reg.pushManager.getSubscription():null;
      if(!active)return;
      const id=subscription?localStorage.getItem(key)||'':'';
      setIdentity(id);
      setAvailable(Boolean(id||settings.webpush&&settings.public_key));
      if(settings.webpush&&settings.public_key){
        const encoded=settings.public_key.replace(/-/g,'+').replace(/_/g,'/');
        setPublicKey(Uint8Array.from(atob(encoded.padEnd(Math.ceil(encoded.length/4)*4,'=')),c=>c.charCodeAt(0)));
      }
    }).catch(()=>{});
    return ()=>{active=false;};
  },[key]);
  if(!available)return null;
  async function toggle(){
    setBusy(true);setMessage('');
    try{
      if(identity){
        await request('/notifications/subscriptions/'+identity+'/revoke',{});
        localStorage.removeItem(key);setIdentity('');
        if(!Object.keys(localStorage).some(k=>k.startsWith('capzoryx-push:'))){
          const reg=await navigator.serviceWorker.getRegistration('/reminders/');
          const existing=await reg?.pushManager.getSubscription();
          if(existing)await existing.unsubscribe();
        }
      }else{
        if(!publicKey){setMessage(t('Push delivery is currently unavailable.'));return;}
        const permission=await Notification.requestPermission();
        if(permission!=='granted'){setMessage(t('Push delivery is currently unavailable.'));return;}
        const registration=await navigator.serviceWorker.register('/reminders/worker.js',{scope:'/reminders/'});
        if(!registration.active){
          const worker=registration.installing||registration.waiting;
          if(!worker)throw new Error(t('Push delivery is currently unavailable.'));
          await new Promise<void>((resolve,reject)=>{
            const changed=()=>{
              if(worker.state==='activated'||worker.state==='redundant'){
                worker.removeEventListener('statechange',changed);
                if(worker.state==='activated')resolve();else reject(new Error(t('Push delivery is currently unavailable.')));
              }
            };
            worker.addEventListener('statechange',changed);changed();
          });
        }
        const sub=await registration.pushManager.getSubscription()||await registration.pushManager.subscribe({userVisibleOnly:true,applicationServerKey:publicKey});
        const result=await request<{id:string}>('/notifications/subscriptions',{kind:'webpush',endpoint:sub.endpoint});
        localStorage.setItem(key,result.id);setIdentity(result.id);
      }
    }catch(e){setMessage(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  return <div><button className="secondary" type="button" disabled={busy} onClick={()=>void toggle()}>
    {identity?t('Turn off push on this device'):t('Turn on push on this device')}</button>
    {message&&<p role="status">{message}</p>}</div>;
}
