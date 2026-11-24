import {useState} from 'react';
import {formatTime} from './format';
import {t} from './i18n';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Token={id:string;created_at:number;expires_at:number;revoked_at:number|null};

export function ReadOnlyTokens({request}:{request:Request}){
  const [tokens,setTokens]=useState<Token[]|null>(null);
  const [value,setValue]=useState('');
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  async function load(){setTokens((await request<{tokens:Token[]}>('/workspace/mcp-tokens')).tokens);}
  async function run(action:()=>Promise<void>){
    setBusy(true);setMessage('');
    try{await action();}catch(e){setMessage(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  return <section id="mcp-access" className="panel"><h2>{t('Connect a read-only MCP client')}</h2>
    <p>{t('A token lets its holder read this workspace’s balance, cash forecast and open supplier bills for 90 days.')}</p>
    <p><code>{location.origin}/api/mcp</code></p>
    <div className="actions"><button type="button" disabled={busy} onClick={()=>void run(async()=>{
      const created=await request<{token:string}>('/workspace/mcp-tokens',{});setValue(created.token);await load();
    })}>{t('Create read-only token')}</button>
    <button type="button" className="secondary" disabled={busy} onClick={()=>void run(load)}>{t('Manage tokens')}</button></div>
    {value&&<><label>{t('Copy this token before closing this page')}<input type="text" readOnly value={value} spellCheck={false} autoComplete="off" onFocus={e=>e.target.select()}/></label>
      <button className="secondary" onClick={()=>setValue('')}>{t('Hide token')}</button></>}
    {tokens&&<ul className="people">{tokens.map(token=><li key={token.id}><div><strong>{token.id.slice(0,8)}</strong>
      <small>{t('Expires {date}',{date:formatTime(token.expires_at)})}</small></div>
      {token.revoked_at||token.expires_at<=Date.now()/1000?<span>{t('Inactive')}</span>:
        <button className="secondary" disabled={busy} onClick={()=>void run(async()=>{await request('/workspace/mcp-tokens/'+token.id+'/revoke',{});setValue('');await load();})}>{t('Revoke token')}</button>}
    </li>)}</ul>}
    {message&&<p role="status">{message}</p>}
  </section>;
}
