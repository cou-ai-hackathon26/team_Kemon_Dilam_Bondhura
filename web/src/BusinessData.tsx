import {useEffect,useRef,useState} from 'react';
import {formatDate} from './format';
import {t,T} from './i18n';

type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
type Note={id:string;kind:string;title:string;text:string;file_name:string;research:boolean;created_at:number};
type View={notes:Note[];kinds:string[];research_allowed:boolean;profile_ready:boolean;research_started?:boolean};

const KIND:Record<string,{label:string;hint:string}>={
  sales:{label:T('Sales and income'),hint:T('e.g. Best months, daily sales, what sells most, sales sheet')},
  prices:{label:T('Prices and costs'),hint:T('e.g. Your price list, supplier prices, rent, delivery costs')},
  competitors:{label:T('Competitors'),hint:T('e.g. Shops near you, their prices, what they do better')},
  customers:{label:T('Customers'),hint:T('e.g. Who buys from you, where they come from, what they ask for')},
  suppliers:{label:T('Suppliers and stock'),hint:T('e.g. Where you buy, delivery time, stock problems')},
  plans:{label:T('Plans and problems'),hint:T('e.g. A new branch, Eid plans, a loan, a problem you want solved')},
  other:{label:T('Anything else'),hint:T('Anything about your business that the assistant should know')},
};
const TEXT_FILE=/\.(txt|csv|tsv|md|json)$/i;
const MAX_CHARS=20000;
const errorText=(e:unknown)=>e instanceof Error?e.message:t('Request failed.');

/** Anything the owner wants Capzoryx to know: typed notes or a text/CSV file. Used by the assistant and, if allowed, by market research. */
export function BusinessData({request}:{request:Request}){
  const [data,setData]=useState<View|null>(null);
  const [kind,setKind]=useState('sales');
  const [title,setTitle]=useState('');
  const [text,setText]=useState('');
  const [file,setFile]=useState('');
  const [research,setResearch]=useState(true);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [notice,setNotice]=useState('');
  const picker=useRef<HTMLInputElement>(null);
  useEffect(()=>{void request<View>('/business/notes').then(setData).catch(e=>setError(errorText(e)));},[]);// eslint-disable-line react-hooks/exhaustive-deps

  async function pick(f:File|undefined){
    setError('');
    if(!f)return;
    if(!TEXT_FILE.test(f.name)){setError(t('Choose a text or spreadsheet file saved as .csv or .txt. For Excel, use File → Save as → CSV.'));return;}
    if(f.size>400_000){setError(t('This file is too large. Keep it under 400 KB, or paste the important part.'));return;}
    const body=(await f.text()).replace(/^﻿/,'');
    setText(body.slice(0,MAX_CHARS));setFile(f.name.slice(0,120));
    if(!title.trim())setTitle(f.name.replace(/\.[^.]+$/,'').slice(0,120));
    if(body.length>MAX_CHARS)setNotice(t('Only the first {count} characters of the file were kept.',{count:MAX_CHARS.toLocaleString()}));
  }
  async function save(){
    setBusy(true);setError('');setNotice('');
    try{
      const next=await request<View>('/business/notes',{kind,title:title.trim(),text:text.trim(),file_name:file,research});
      setData(next);setTitle('');setText('');setFile('');if(picker.current)picker.current.value='';
      setNotice(next.research_started?t('Saved. The assistant now knows this and is researching your market again.'):t('Saved. The assistant now knows this.'));
    }catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  async function remove(id:string){
    if(!window.confirm(t('Delete this business data? The assistant will no longer use it.')))return;
    setBusy(true);setError('');
    try{setData(await request<View>(`/business/notes/${id}/delete`,{}));}catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  const ready=title.trim().length>=2&&text.trim().length>=2;
  return <section className="panel business-data" aria-labelledby="business-data-title">
    <p className="eyebrow">{t('Business data')}</p>
    <h2 id="business-data-title">{t('Tell Capzoryx about your business')}</h2>
    <p className="muted">{t('Write anything, or add a .csv or .txt file: sales, prices, competitors, customers, plans. The assistant uses it in every answer, and market research uses it to look in the right places.')}</p>
    <form onSubmit={e=>{e.preventDefault();if(ready&&!busy)void save();}}>
      <label>{t('What is it about?')}
        <select value={kind} onChange={e=>setKind(e.target.value)}>{(data?.kinds||Object.keys(KIND)).map(k=><option key={k} value={k}>{t(KIND[k]?.label||k)}</option>)}</select></label>
      <label>{t('Title')}<input value={title} maxLength={120} required minLength={2} onChange={e=>setTitle(e.target.value)} placeholder={t('e.g. Eid sales last year')}/></label>
      <label>{t('Details')}<textarea rows={5} value={text} maxLength={MAX_CHARS} required onChange={e=>{setText(e.target.value);if(file)setFile('');}} placeholder={t(KIND[kind]?.hint||'')}/></label>
      <div className="business-data-file">
        <input ref={picker} id="business-data-file" type="file" accept=".csv,.txt,.tsv,.md,.json,text/plain,text/csv" className="sr-only" onChange={e=>void pick(e.target.files?.[0])}/>
        <label htmlFor="business-data-file" className="button secondary small-button">{t('Add a file')}</label>
        {file&&<small>{t('File: {name}',{name:file})}</small>}
      </div>
      <label className="check"><input type="checkbox" checked={research} onChange={e=>setResearch(e.target.checked)}/> {t('Use this to guide market research')}</label>
      <small className="muted">{research?(data?.research_allowed?t('The research service reads this note to choose what to search for. Leave it off for private numbers.'):t('Market research is off, so this stays inside Capzoryx. Turn research on in your business profile below.')):t('This stays inside Capzoryx: only the assistant uses it.')}</small>
      <div className="actions"><button disabled={busy||!ready}>{busy?t('Saving…'):t('Save business data')}</button></div>
    </form>
    <div aria-live="polite">{notice&&<p className="success">{notice}</p>}</div>
    {error&&<p className="error" role="alert">{error}</p>}
    {!!data?.notes.length&&<><h3>{t('What you have shared ({count})',{count:data.notes.length})}</h3>
      <ul className="business-data-list">{data.notes.map(n=><li key={n.id}>
        <details><summary><span className="pill">{t(KIND[n.kind]?.label||n.kind)}</span> <b>{n.title}</b>
          <small>{formatDate(new Date(n.created_at*1000).toISOString().slice(0,10))}{n.file_name?' · '+n.file_name:''}{n.research?' · '+t('used for research'):''}</small></summary>
          <p className="business-data-text">{n.text.length>1500?n.text.slice(0,1500)+'…':n.text}</p>
          <button className="link danger" disabled={busy} onClick={()=>void remove(n.id)}>{t('Delete')}</button></details>
      </li>)}</ul></>}
  </section>;
}
