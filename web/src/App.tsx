import {apiFetch,installedApp,nativeBuild} from './apiBase';
import {OfflineCapture,InstallControl} from './Pwa';
import {t,useI18n} from './i18n';
import {LazyBoundary} from './LazyBoundary';
import {lazy,Suspense,useEffect,useRef,useState} from 'react';
import {accountRequest} from './api';
import type {Session} from './types';
import {Auth} from './Auth';
import {cacheScope,forgetAll,keepSession,savedSession} from './offlineCache';
import {EmployeeJoin,EmployeePortal} from './EmployeePortal';
const Workspace=lazy(()=>import('./Workspace'));
const JOIN_KEY='safeledger-join-code';

function takeJoinCode():string{
  try{
    const url=new URL(window.location.href);const fromUrl=url.searchParams.get('join');
    if(fromUrl&&/^[A-Za-z0-9_-]{20,100}$/.test(fromUrl)){sessionStorage.setItem(JOIN_KEY,fromUrl);url.searchParams.delete('join');window.history.replaceState(null,'',url.pathname+(url.search||'')+url.hash);}
    return sessionStorage.getItem(JOIN_KEY)||'';
  }catch{return '';}
}

function employeeCode():string{
  try{const code=new URL(window.location.href).searchParams.get('employee');return code&&/^[A-Za-z0-9_-]{20,100}$/.test(code)?code:'';}catch{return '';}
}

export default function App() {
  useI18n();
  // Being offline never blocks the app: sign-in and the workspace still open, with offline recording as an option.
  const [online,setOnline]=useState(navigator.onLine && !document.querySelector('meta[name="capzoryx-offline"]'));
  const [capture,setCapture]=useState(false);
  useEffect(()=>{
    const connected=()=>{if(!installedApp)setAuth(null);setOnline(true);setCapture(false);}, disconnected=()=>setOnline(false);  // back online: return to the app, which syncs queued records
    window.addEventListener('online',connected);window.addEventListener('offline',disconnected);
    return ()=>{window.removeEventListener('online',connected);window.removeEventListener('offline',disconnected);};
  },[]);

  // The Android app opens the last account straight away (also offline); the server check below confirms or ends it.
  const [auth,setAuthState]=useState<{session:Session}|null>(()=>{const s=savedSession();if(s)cacheScope(s);return s?{session:s}:null;});
  const setAuth=(value:{session:Session}|null)=>{if(value){keepSession(value.session);cacheScope(value.session);}setAuthState(value);};
  const [recoveryCode,setRecoveryCode]=useState('');
  const [error,setError]=useState('');const [notice,setNotice]=useState('');
  const [joinCode,setJoinCode]=useState(takeJoinCode);
  const [inviteCode,setInviteCode]=useState(employeeCode);
  // People who signed up through an employer's payroll invite see only their payslips.
  const [portal,setPortal]=useState<{user:string;employee:boolean}|null>(null);
  const userId=auth?.session.user_id;
  useEffect(()=>{
    if(!userId)return;
    let live=true;
    void accountRequest<{employee_only:boolean}>('/payroll/me').then(r=>{if(live)setPortal({user:userId,employee:r.employee_only});}).catch(()=>{if(live)setPortal({user:userId,employee:false});});
    return ()=>{live=false;};
  },[userId]);
  const portalReady=!!auth&&portal?.user===auth.session.user_id;
  const restoreRequest=useRef<AbortController|null>(null);
  // Use the browser's connectivity event, with a foreground retry for networks that leave navigator.onLine true.
  useEffect(()=>{
    if(online)return;
    const abort=new AbortController();let timer:number;let delay=1500;
    async function reconnect(){
      if(abort.signal.aborted)return;
      if(navigator.onLine && document.visibilityState==='visible'){
        try{
          const response=await apiFetch('/api/health',{cache:'no-store',signal:AbortSignal.any([abort.signal,AbortSignal.timeout(8000)])});
          if(response.ok && (await response.json()).status==='ok' && !abort.signal.aborted){
            if(!installedApp)setAuth(null);setOnline(true);setCapture(false);return;
          }
        }catch{/* Offline or a sleeping service. Keep the draft available locally. */}
      }
      delay=Math.min(60000,delay*2);timer=window.setTimeout(reconnect,delay);
    }
    timer=window.setTimeout(reconnect,delay);
    return ()=>{abort.abort();clearTimeout(timer);};
  },[online]);
  // Public content is available while the server checks its HttpOnly session cookie.
  useEffect(()=>{
    if(!online)return;
    const controller=new AbortController();
    restoreRequest.current=controller;
    async function restore(){
      try{
        const response=await apiFetch('/api/session',{
          credentials:'same-origin',
          signal:AbortSignal.any([controller.signal,AbortSignal.timeout(90000)])
        });
        if(response.status===401&&!controller.signal.aborted){forgetAll();setAuth(null);await window.capzoryxStore.setScope(null);}
        if(!response.ok)return;
        const session=await response.json() as Session;
        if(!controller.signal.aborted&&!session.guest&&session.workspace_kind!=='demo'){
          setAuth({session});
        }
      }catch{/* Visitors can still browse and sign in when the session check fails. */}
    }
    void restore();
    return ()=>controller.abort();
  },[online]);
  // An invitation link joins the shared workspace as soon as the person is signed in.
  useEffect(()=>{
    if(!auth||!joinCode)return;
    const code=joinCode;setJoinCode('');try{sessionStorage.removeItem(JOIN_KEY);}catch{/* optional */}
    void accountRequest<Session>('/account/join',{code}).then(session=>{setAuth({session});setNotice(`You joined ${session.workspace_name||'the workspace'} as ${session.role}.`);}).catch(e=>setError(e instanceof Error?e.message:'The invitation could not be used.'));
  },[auth,joinCode]);
  function enter(session:Session){restoreRequest.current?.abort();setRecoveryCode(session.recovery_code||'');setError('');setAuth({session:{...session,recovery_code:undefined}});}
  async function signout(){try{await accountRequest('/auth/logout',{});}catch{setError('Could not sign out. Please retry.');return;}try{Object.keys(sessionStorage).filter(k=>k.startsWith('safeledger-financing-quotes-v1:')).forEach(k=>sessionStorage.removeItem(k));}catch{/* Storage may be unavailable. */}forgetAll();setAuth(null);setError('');setNotice('');setRecoveryCode('');
    try{
      for(const key of Object.keys(localStorage))if(key.startsWith('capzoryx-push:'))localStorage.removeItem(key);
      const reg=await navigator.serviceWorker?.getRegistration();
      const subscription=await reg?.pushManager?.getSubscription();
      if(subscription)await subscription.unsubscribe();
      const reminders=await navigator.serviceWorker?.getRegistration('/reminders/');
      if(reminders?.scope===new URL('/reminders/',location.href).href){
        await (await reminders.pushManager.getSubscription())?.unsubscribe();
      }
    }catch{/* The push service may already have removed this subscription. */}
    try{await window.capzoryxStore.clear();if('BroadcastChannel' in window){const channel=new BroadcastChannel('capzoryx-account');channel.postMessage('logout');channel.close();}}catch{setError('Device storage could not be cleared.');}
  }
  useEffect(()=>{
    if(!('BroadcastChannel' in window))return;
    const channel=new BroadcastChannel('capzoryx-account');
    channel.onmessage=event=>{if(event.data==='logout'){forgetAll();setAuth(null);setRecoveryCode('');}};
    return ()=>channel.close();
  },[]);
  function expired(){forgetAll();void window.capzoryxStore.setScope(null).catch(()=>{});setAuth(null);setError('Your session ended. Sign in again to continue.');}
  if(capture)return <OfflineCapture onClose={()=>setCapture(false)}/>;
  return <>
    {!online&&<p className="notice banner offline-banner" role="status">{t('You are offline or the server is waking up. Sign-in and live data return by themselves when the connection is back.')} <button className="link" onClick={()=>setCapture(true)}>{t('Record a payment offline')}</button></p>}
    {auth&&error&&<p className="error banner" role="alert">{error} <button className="link" onClick={()=>setError('')}>Dismiss</button></p>}
    {auth&&notice&&<p className="success banner" role="status">{notice} <button className="link" onClick={()=>setNotice('')}>Dismiss</button></p>}
    {auth&&recoveryCode&&<RecoveryNotice code={recoveryCode} done={()=>setRecoveryCode('')}/>}
    {!auth&&inviteCode?<EmployeeJoin code={inviteCode} onSession={session=>{setInviteCode('');enter(session);}}/>
    :auth&&!portalReady?<main className="workspace-loading" role="status"><span className="loading-ring"/>Opening your workspace…</main>
    :auth&&portal?.employee?<EmployeePortal logout={()=>void signout()}/>
    :auth?<LazyBoundary><Suspense fallback={<main className="workspace-loading" role="status"><span className="loading-ring"/>Opening your workspace…</main>}><Workspace holdSetup={!!recoveryCode} key={auth.session.business_id+auth.session.user_id} token="" session={auth.session} logout={()=>void signout()} expired={expired} changeSession={session=>setAuth({session})}/></Suspense></LazyBoundary>
      :<><Auth onAuthStart={()=>restoreRequest.current?.abort()} onSession={enter} joinCode={joinCode} notice={error||''}/>{!nativeBuild && <InstallControl/>}</>}
  </>;
}

function RecoveryNotice({code,done}:{code:string;done:()=>void}) {
 const [copied,setCopied]=useState(false);
 return <section className="recovery-notice panel" role="region" aria-label={t('Save your backup recovery key')}><span className="badge">{t('SHOWN ONCE · KEEP PRIVATE')}</span><h2>{t('Save your backup recovery key')}</h2><p>{t('If you ever lose your password and cannot get an email code, this key gets you back in. Keep it somewhere safe, such as a password manager.')}</p><label htmlFor="new-recovery-code">{t('Recovery key')}</label><input id="new-recovery-code" value={code} readOnly autoComplete="off"/><div className="actions"><button className="secondary" onClick={()=>void navigator.clipboard.writeText(code).then(()=>setCopied(true)).catch(()=>setCopied(false))}>{copied?t('Copied'):t('Copy key')}</button><button onClick={done}>{t('I saved it')}</button></div></section>;
}
