import {useState} from 'react';
import {t,T} from './i18n';

type Flag={name:string; workspace_enabled:boolean; operator_enabled:boolean};
type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
const labels:Record<string,string>={
  cash_corrections:T('Manual cash corrections'),voice:T('Voice transcription'),bill_scan:T('Online bill scanning'),assistant:T('Online assistant'),
  offer_reader:T('Offer reader'),financing_match:T('Financing match'),ask_books:T('Ask your books'),
  collection_plans:T('Collection plans and reminders'),passport_issue:T('Issue public passports'),
  agent_mode:T('Agentic Mode'),statement_import:T('Statement import'),payment_intake:T('Wallet payment intake')
};

export function WorkspaceFeatures({request}:{request:Request}){
  const [flags,setFlags]=useState<Flag[]|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  async function load(name?:string,enabled?:boolean){
    setBusy(true);setError('');
    try{
      const path='/workspace/features';
      const result=await request<{features:Flag[]}>(name?path+'/'+encodeURIComponent(name):path,name?{enabled}:undefined);
      setFlags(result.features);
    }catch(e){setError(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  return <section className="panel settings-section">
    <h2>{t('Workspace features')}</h2>
    <p>{t('Choose which tools members can use in this workspace.')}</p>
    {!flags&&<button type="button" className="secondary" disabled={busy} onClick={()=>void load()}>{t('View feature settings')}</button>}
    {flags?.map(f=><label key={f.name} className="check">
      <input type="checkbox" checked={f.operator_enabled&&f.workspace_enabled}
        disabled={busy||!f.operator_enabled} onChange={e=>void load(f.name,e.target.checked)}/>
      {t(labels[f.name]||f.name)} {!f.operator_enabled&&<small>{t('Disabled by the operator')}</small>}
    </label>)}
    {error&&<p role="alert">{error}</p>}
  </section>;
}
