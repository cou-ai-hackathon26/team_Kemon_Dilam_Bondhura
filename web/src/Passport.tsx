import {apiFetch,nativeBuild} from './apiBase';
import {useEffect, useRef, useState} from 'react';
import {t, useI18n} from './i18n';
import {currencyDigits} from './api';
import {formatDate, formatTime} from './format';

type Link = {id: string; expires_at: number};
type Send = <T>(path: string, body?: unknown) => Promise<T>;
type Summary = {id:string; key_id:string; format:string; start:string; as_of:string; issued_at:number; expires_at:number;
  currency:string; balance_minor:number; receipts_minor:number; expenses_minor:number};

function verifierOrigin(origin:string):string {
  if(nativeBuild)return 'https://capzoryx.vercel.app';
  const host=new URL(origin);
  if (host.protocol==='http:' && ['127.0.0.1','localhost','[::1]'].includes(host.hostname)) return host.origin;
  return 'https://capzoryx.vercel.app';
}
export function passportUrl(id:string,origin=location.origin):string {
  if(!/^[a-f0-9]{32}$/.test(id))throw new Error('Invalid passport ID');
  return new URL('/verify/'+id,verifierOrigin(origin)).href;
}

export function PassportManager({request}: {request: Send}) {
  const [links, setLinks] = useState<Link[]>([]);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');
  useEffect(() => {
    let current = true;
    request<{passports:Link[]}>('/passport/list').then(result => {if (current) setLinks(result.passports);})
      .catch(() => {if (current) setError(t('Could not load passport links.'));}).finally(() => {if (current) setLoading(false);});
    return () => {current = false;};
  }, []); // WorkspaceContent remounts when the workspace or member changes.

  async function change(id?:string) {
    const question=id?t('Revoke this passport? Its link and printed QR code will stop verifying.')
      :t('Create a public passport? Anyone with its link can read the balance and recent totals until expiry or revocation.');
    if(!window.confirm(question))return;
    setBusy(true);
    setError('');
    try {
      if (id) {
        await request('/passport/revoke', {id});
        setLinks(rows => rows.filter(row => row.id !== id));
      } else {
        const link = await request<Link>('/passport/create', {});
        setLinks(rows => [link, ...rows]);
      }
    } catch {setError(t('Could not update the passport. Check your connection and signing configuration.'));}
    finally {setBusy(false);}
  }
  return <section className="panel"><h2>{t('Cash-flow passport')}</h2>
    <p>{t('Anyone with the link can see the cash balance and recent receipt and expense totals. Links expire after 90 days and can be revoked here.')}</p>
    <button disabled={busy || loading} onClick={() => void change()}>{t('Create a signed passport')}</button>
    {error && <p role="alert">{error}</p>}
    <ul>{links.map(link => <li key={link.id}><a href={passportUrl(link.id)} target="_blank" rel="noopener noreferrer">{t('View passport')}</a>
      {' · '}{t('Expires {time}', {time:formatTime(link.expires_at)})}{' '}
      <button className="secondary small" disabled={busy} onClick={() => void change(link.id)}>{t('Revoke link')}</button></li>)}</ul>
  </section>;
}

const bytes = (value:string) => Uint8Array.from(atob(value), char => char.charCodeAt(0));

async function readVerified(id:string, signal:AbortSignal):Promise<Summary> {
  const [data, keys] = await Promise.all([
    apiFetch('/api/public/passport/' + id, {credentials:'omit', cache:'no-store', signal}),
    fetch(nativeBuild?'/passport-keys.json':'/.well-known/capzoryx-passport-key.json', {credentials:'omit', cache:'no-store', signal})
  ]);
  if (!data.ok || !keys.ok) throw Error();
  const {canonical, signature} = await data.json();
  if(typeof canonical!=='string'||canonical.length>4096||typeof signature!=='string')throw Error();
  const summary = JSON.parse(canonical) as Summary;
  if(summary.id!==id||summary.format!=='capzoryx-passport-v1'||typeof summary.key_id!=='string'
     ||!/^\d{4}-\d{2}-\d{2}$/.test(summary.start)||!/^\d{4}-\d{2}-\d{2}$/.test(summary.as_of)
     ||summary.start>summary.as_of||!/^[A-Z]{3}$/.test(summary.currency)
     ||![summary.balance_minor,summary.receipts_minor,summary.expenses_minor,summary.issued_at,summary.expires_at].every(Number.isSafeInteger)
     ||summary.receipts_minor<0||summary.expenses_minor<0||summary.expires_at<=summary.issued_at)throw Error();
  const registry=await keys.json();
  if(!registry||!Object.hasOwn(registry,summary.key_id)||typeof registry[summary.key_id]!=='string')throw Error();
  const raw=registry[summary.key_id];
  const key = await crypto.subtle.importKey('raw', bytes(raw), {name:'Ed25519'}, false, ['verify']);
  if (!await crypto.subtle.verify('Ed25519', key, bytes(signature), new TextEncoder().encode(canonical))
    || summary.expires_at * 1000 <= Date.now()) throw Error();
  return summary;
}

export function VerifyPassport({id}: {id:string}) {
  useI18n();
  const [summary, setSummary] = useState<Summary|null>(null);
  const [failed, setFailed] = useState(false);
  const [qr, setQr] = useState('');
  const [printing,setPrinting]=useState(false);
  const [retry,setRetry]=useState(0);
  const printRequested=useRef(false);
  const report=summary?.id===id?summary:null;
  useEffect(() => {
    let live=true, revision=0, controller:AbortController|undefined, timer:number|undefined;
    let printFrame=0;
    function conceal(){
      revision++;controller?.abort();clearTimeout(timer);cancelAnimationFrame(printFrame);
      printRequested.current=false;setPrinting(false);setSummary(null);setQr('');
    }
    async function check(){
      controller?.abort();clearTimeout(timer);
      const attempt=++revision;
      controller=new AbortController();
      setSummary(null);setQr('');setFailed(false);
      try{
        const signal=AbortSignal.any([controller.signal,AbortSignal.timeout(15000)]);
        const current=await readVerified(id,signal);
        if(!live||attempt!==revision)return;
        const lib=await import('qrcode');
        const image=await lib.toDataURL(passportUrl(id));
        if(!live||attempt!==revision)return;
        if(current.expires_at*1000<=Date.now())throw Error();
        setSummary(current);setQr(image);
        // Recheck active tabs periodically and always at expiration, on focus and before printing.
        timer=window.setTimeout(check,Math.min(60000,current.expires_at*1000-Date.now()));
        if(printRequested.current){
          printRequested.current=false;
          printFrame=requestAnimationFrame(()=>{if(live&&attempt===revision)window.print();});
        }
      }catch{
        if(live&&attempt===revision){setSummary(null);setQr('');setFailed(true);printRequested.current=false;}
      }finally{if(live&&attempt===revision)setPrinting(false);}
    }
    const changed=()=>{if(document.hidden)conceal();else void check();};
    const disconnected=()=>{conceal();setFailed(true);};
    changed();
    window.addEventListener('pagehide',conceal);window.addEventListener('focus',changed);
    window.addEventListener('pageshow',changed);document.addEventListener('visibilitychange',changed);
    window.addEventListener('online',changed);window.addEventListener('offline',disconnected);
    return ()=>{live=false;revision++;controller?.abort();clearTimeout(timer);cancelAnimationFrame(printFrame);
      window.removeEventListener('pagehide',conceal);window.removeEventListener('focus',changed);
      window.removeEventListener('pageshow',changed);document.removeEventListener('visibilitychange',changed);
      window.removeEventListener('online',changed);window.removeEventListener('offline',disconnected);};
  }, [id,retry]);
  const money = (minor:number) => new Intl.NumberFormat(document.documentElement.lang, {
    style:'currency', currency:report!.currency
  }).format(minor / 10 ** currencyDigits(report!.currency));
  return <main className="passport-page"><article className="panel">
    <p className="eyebrow">Capzoryx</p><h1>{t('Cash-flow passport')}</h1>
    {report ? <>
      <p className="success">{t('Signature verified')}</p>
      <p>{formatDate(report.start)} — {formatDate(report.as_of)}</p>
      <dl>{[[t('Cash balance'),report.balance_minor],[t('Money received'),report.receipts_minor],[t('Money spent'),report.expenses_minor]].map(([label,amount]) =>
        <div key={label}><dt>{label}</dt><dd>{money(Number(amount))}</dd></div>)}</dl>
      <p>{t('Based on records entered in the workspace. This document does not verify bank balances or creditworthiness.')}</p>
      <p>{t('Expires {time}', {time:formatTime(report.expires_at)})}</p>
      {qr && <img src={qr} width="150" height="150" alt={t('QR code to check the current passport')}/>}
      <button className="secondary passport-print" disabled={printing} onClick={()=>{printRequested.current=true;setPrinting(true);setRetry(n=>n+1);}}>{t('Print or save PDF')}</button>
    </> : <p role="status">{failed ? t('This passport could not be verified. It may have expired or been revoked.') : t('Verifying signature…')}{failed&&<button type="button" className="secondary" onClick={()=>setRetry(n=>n+1)}>{t('Try again')}</button>}</p>}
  </article></main>;
}
