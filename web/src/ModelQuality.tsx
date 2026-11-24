import {useState} from 'react';
import {t,T} from './i18n';

type Evaluation = {suite:string;metric:string;samples:number;passed:number;dataset_sha256:string};
type Quality = {requests:number;calls:number;successful_calls:number;evaluations:Evaluation[];traces:{trace_id:string;task:string;calls:number;successful_calls:number;latency_ms:number}[]};
type Request = <V>(path:string,body?:unknown)=>Promise<V>;
const metricNames:Record<string,string>={answer:T('Answer'),amount_minor:T('Amount'),direction:T('Cash direction'),party:T('Counterparty')};
const suiteNames:Record<string,string>={heldout_eval:T('Local assistant · set 1'),heldout_eval_v2:T('Local assistant · set 2'),heldout_eval_v3:T('Local assistant · set 3'),browser_rules:T('Browser parser'),hisab_tagger:'Hisab-Tagger'};

export function ModelQuality({request}:{request:Request}){
  const [quality,setQuality]=useState<Quality|null>(null);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  async function load(){
    setBusy(true);setError('');
    try{setQuality(await request<Quality>('/admin/ai-quality'));}
    catch(e){setError(e instanceof Error?e.message:t('Request failed'));}
    finally{setBusy(false);}
  }
  return <section className="panel settings-section">
    <h2>{t('AI quality')}</h2>
    <button className="secondary" disabled={busy} onClick={()=>void load()}>{quality?t('Refresh measurements'):t('View measurements')}</button>
    {error&&<p role="alert" className="error">{error}</p>}
    {quality&&<>
      <p>{t('This workspace made {requests} model requests in the past seven days. {ok} of {calls} provider calls succeeded.',{requests:quality.requests,ok:quality.successful_calls,calls:quality.calls})}</p>
      {quality.traces.length>0&&<details><summary>{t('Recent model requests')}</summary><ul>{quality.traces.map(row=><li key={row.trace_id+row.task}>{row.task} · {row.trace_id} · {row.latency_ms} ms</li>)}</ul></details>}
      <h3>{t('Synthetic evaluation results')}</h3>
      <div className="table-wrap" tabIndex={0}><table><thead><tr><th>{t('Suite')}</th><th>{t('Metric')}</th><th>{t('Correct / tested')}</th></tr></thead>
        <tbody>{quality.evaluations.map(e=><tr key={e.suite+e.metric}><td>{t(suiteNames[e.suite]||e.suite)}</td><td>{t(metricNames[e.metric]||e.metric)}</td><td>{e.passed} / {e.samples}</td></tr>)}</tbody>
      </table></div>
    </>}
  </section>;
}
