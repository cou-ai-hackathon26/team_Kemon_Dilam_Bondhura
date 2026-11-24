import {useState,type FormEvent} from 'react';
import {useCurrency} from './Currency';
import {formatDate} from './format';
import {t,T} from './i18n';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Answer={answered:boolean;kind:string;as_of:string;amount_minor:number;source_ids:string[]};
const labels:Record<string,string>={
  balance:T('Available balance'),income:T('Cleared income'),expenses:T('Cleared expenses'),
  payables:T('Outstanding supplier bills'),overdue_bills:T('Overdue supplier bills'),
  receivables:T('Customer receivables'),overdue_receipts:T('Overdue customer balances'),
  week:T('Projected balance in 7 days'),month:T('Projected balance in 30 days')
};

export function AskBooks({request}:{request:Request}){
  const {nativeMoney}=useCurrency();
  const [question,setQuestion]=useState('');
  const [answer,setAnswer]=useState<Answer|null>(null);
  const [error,setError]=useState('');
  const [busy,setBusy]=useState(false);
  async function submit(e:FormEvent){
    e.preventDefault();setBusy(true);setError('');setAnswer(null);
    try{setAnswer(await request<Answer>('/copilot/ask',{question}));}
    catch(e){setError(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  return <section className="panel"><h2>{t('Ask your books')}</h2>
    <form onSubmit={submit}><label>{t('Question about your records')}
      <input required disabled={busy} maxLength={240} value={question} placeholder={t('How much cash do I have?')} onChange={e=>{setQuestion(e.target.value);setAnswer(null);}}/>
    </label><p className="muted">{t('Ask about a balance, income, expenses, unpaid bills or customer dues in English or Bangla.')}</p>
    <button type="submit" disabled={busy||!question.trim()}>{t('Find the answer in my records')}</button></form>
    {error&&<p role="alert">{error}</p>}
    {answer&&(answer.answered?<div role="status"><h3>{t(labels[answer.kind])}</h3>
      <strong>{nativeMoney(answer.amount_minor)}</strong><p>{t('As of {date}',{date:formatDate(answer.as_of)})}</p>
      {answer.source_ids.length>0?<details><summary>{t('Source records ({count})',{count:answer.source_ids.length})}</summary>
        <ul>{answer.source_ids.map(id=><li key={id}>{id}</li>)}</ul></details>:<p>{t('No matching records for this figure.')}</p>}
    </div>:<p role="status">{t('I could not match that question to a ledger figure. Try “expenses this month” or “customer dues”.')}</p>)}
  </section>;
}
