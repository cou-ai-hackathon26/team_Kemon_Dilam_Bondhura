import {useEffect,useState} from 'react';
import {t} from './i18n';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Status={configured:boolean;linked:boolean;pending:boolean;chat_label:string|null;chat_enabled:boolean;records_enabled:boolean};
const path='/channels/telegram';

export function TelegramLink({request}:{request:Request}){
  const [status,setStatus]=useState<Status|null>(null);
  const [url,setUrl]=useState('');
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  async function run(action:()=>Promise<void>){
    setBusy(true);setMessage('');
    try{await action();}
    catch(e){setMessage(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  async function load(){setStatus(await request<Status>(path+'/link'));}
  useEffect(()=>{void run(load);},[]);// eslint-disable-line react-hooks/exhaustive-deps
  // After Telegram opens, watch for the bot to link the chat (every 3 s for up to 5 minutes).
  useEffect(()=>{
    if(!url||status?.linked)return;
    let tries=0;
    const timer=window.setInterval(()=>{
      if(++tries>100){window.clearInterval(timer);return;}
      request<Status>(path+'/link').then(next=>{setStatus(next);if(next.linked)setUrl('');}).catch(()=>undefined);
    },3000);
    return()=>window.clearInterval(timer);
  },[url,status?.linked]);// eslint-disable-line react-hooks/exhaustive-deps
  const reset=async()=>{await request(path+'/unlink',{});setUrl('');await load();};
  const toggle=(kind:'chat'|'records',on:boolean)=>run(async()=>{await request(path+'/'+kind,{enabled:on});await load();});
  async function start(){
    const link=await request<{bot:string;code:string}>(path+'/link-code',{});
    const target='https://t.me/'+encodeURIComponent(link.bot)+'?start='+encodeURIComponent(link.code);
    setUrl(target);window.open(target,'_blank','noopener');
  }
  let body;
  if(!status)body=busy?<p className="muted">{t('Checking…')}</p>:<button className="secondary" onClick={()=>void run(load)}>{t('Check connection')}</button>;
  else if(!status.configured)body=<p className="muted">{t('Telegram is not set up on this server yet.')}</p>;
  else if(status.linked)body=<>
    <p className="channel-state"><span className="dot on" aria-hidden="true"/>{t('Connected to {chat}',{chat:status.chat_label||'Telegram'})}</p>
    <label className="switch-row"><input type="checkbox" checked={status.chat_enabled} disabled={busy} onChange={e=>void toggle('chat',e.target.checked)}/>
      <span><b>{t('Answer questions about my books')}</b><small>{t('Totals you ask for are sent to this private chat.')}</small></span></label>
    <label className="switch-row"><input type="checkbox" checked={status.records_enabled} disabled={busy||!status.chat_enabled} onChange={e=>void toggle('records',e.target.checked)}/>
      <span><b>{t('Let me add cash entries from chat')}</b><small>{t('Each entry is shown back to you and saved only when you tap Save.')}</small></span></label>
    <div className="actions"><button className="ghost" disabled={busy} onClick={()=>void run(reset)}>{t('Disconnect Telegram')}</button></div></>;
  else if(status.pending)body=<>
    <p>{t('Is {chat} your account?',{chat:status.chat_label||'Telegram'})}</p>
    <div className="actions"><button disabled={busy} onClick={()=>void run(async()=>{await request(path+'/confirm',{});await load();})}>{t('Yes, connect it')}</button>
      <button className="ghost" disabled={busy} onClick={()=>void run(reset)}>{t('Cancel')}</button></div></>;
  else if(url)body=<>
    <p className="muted">{t('Waiting for Telegram… press Start in the bot chat. This page updates by itself.')}</p>
    <div className="actions"><a className="button" href={url} target="_blank" rel="noreferrer">{t('Open Telegram')}</a>
      <button className="secondary" disabled={busy} onClick={()=>void run(load)}>{t('Check now')}</button></div></>;
  else body=<><p className="muted">{t('Chat with Capzoryx in Telegram: ask about cash, dues and bills in Bangla, Banglish or English, and get bill reminders.')}</p>
    <button disabled={busy} onClick={()=>void run(start)}>{t('Connect Telegram')}</button></>;
  return <section id="telegram" className="panel channel"><h2>Telegram</h2>{body}{message&&<p role="status">{message}</p>}</section>;
}
