import {useEffect,useRef,useState} from 'react';
import {useCurrency} from './Currency';
import {CashChart} from './Charts';
import {t} from './i18n';
import {formatDate} from './format';
import type {Forecast,NextCollection} from './types';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Action={source_id:string;scheduled_on:string;collect_by:string;amount_minor:number};
type Plan={id?:string;actions:Action[];original_gap_minor:number;remaining_gap_minor:number;forecast:Forecast};
type Task={id:string;action:Action;due_on:string;status:string};

export function GapFixPanel({request,asOf,reserve,owner,previewInitially=false,openTasksInitially,onTasksChanged}:{request:Request;asOf:string;reserve:number;owner:boolean;previewInitially?:boolean;openTasksInitially:boolean;onTasksChanged:(next:NextCollection|null)=>void}){
  const {nativeMoney}=useCurrency();
  const [plan,setPlan]=useState<Plan|null>(null);
  const [searched,setSearched]=useState(false);
  const [gap,setGap]=useState(0);
  const [tasks,setTasks]=useState<Task[]>([]);
  const [showTasks,setShowTasks]=useState(false);
  const [accepted,setAccepted]=useState(false);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const lock=useRef(false);
  const live=useRef(true);
  useEffect(()=>{live.current=true;return ()=>{live.current=false;};},[]);
  async function run(fn:()=>Promise<void>){
    if(lock.current)return;
    lock.current=true;setBusy(true);setError('');
    try{await fn();}catch(e){if(live.current)setError(e instanceof Error?e.message:t('Request failed.'));}
    finally{lock.current=false;if(live.current)setBusy(false);}
  }
  async function preview(){
    const result=await request<{plan:Plan|null;peak_gap_minor:number}>('/copilot/gap-fix',{as_of:asOf,horizon_days:30,reserve_minor:reserve});
    if(live.current){setPlan(result.plan);setGap(result.peak_gap_minor);setSearched(true);setAccepted(false);}
  }
  function updateTasks(rows:Task[]){
    setTasks(rows);
    const next=rows[0];
    onTasksChanged(next?{due_on:next.due_on,amount_minor:next.action.amount_minor}:null);
  }
  useEffect(()=>{
    if(owner && openTasksInitially)void run(reloadTasks);
    else if(previewInitially)void run(preview);
  },[]);
  async function reloadTasks(){
    const result=await request<{tasks:Task[]}>('/copilot/tasks');
    if(live.current){updateTasks(result.tasks);setShowTasks(true);}
  }
  async function accept(){
    if(!plan?.id)return;
    await request('/copilot/gap-fix/accept',{plan_id:plan.id});
    if(live.current)setAccepted(true);
    await reloadTasks();
  }
  async function closeTask(id:string,status:'done'|'dismissed'){
    const result=await request<{tasks:Task[]}>('/copilot/tasks/'+id,{status});
    if(live.current)updateTasks(result.tasks);
  }
  return <section id="collection-planner" className="panel" tabIndex={-1}>
    <h2 id="collection-tasks" tabIndex={-1}>{t('Plan early collections')}</h2>
    <p>{t('Compare up to three early collections using your original receipt dates and a reserve of {amount}.',{amount:nativeMoney(reserve)})}</p>
    <div className="actions">
      <button type="button" disabled={busy} onClick={()=>void run(preview)}>{t('Find a collection plan')}</button>
      {owner&&<button type="button" className="secondary" disabled={busy} onClick={()=>void run(reloadTasks)}>{t('Open collection tasks')}</button>}
    </div>
    {error&&<p role="alert" className="error">{error}</p>}
    {searched&&!plan&&<p role="status">{gap?t('The available receipts cannot reduce the largest forecast gap.'):t('Your forecast stays within the reserve.')}</p>}
    {plan&&<>
      <p>{t('The largest reserve gap changes from {before} to {after}.',{before:nativeMoney(plan.original_gap_minor),after:nativeMoney(plan.remaining_gap_minor)})}</p>
      <ol>{plan.actions.map(action=><li key={action.source_id}>
        <strong>{t('Collect {amount} by {date}',{amount:nativeMoney(action.amount_minor),date:formatDate(action.collect_by)})}</strong>
        <small> · {t('From the receipt scheduled for {date}',{date:formatDate(action.scheduled_on)})} · {action.source_id}</small>
      </li>)}</ol>
      <details><summary>{t('Projected balance after early collection')}</summary><CashChart forecast={plan.forecast}/></details>
      {owner&&plan.id&&<button type="button" disabled={busy||accepted} onClick={()=>void run(accept)}>{accepted?t('Plan accepted'):t('Accept and create collection tasks')}</button>}
    </>}
    {showTasks&&<><h3>{t('Open collection tasks')}</h3>
      {tasks.length===0?<p>{t('No open collection tasks.')}</p>:<ul className="people">{tasks.map(task=><li key={task.id}>
        <div><strong>{t('Collect {amount} by {date}',{amount:nativeMoney(task.action.amount_minor),date:formatDate(task.due_on)})}</strong>
        <small>{task.action.source_id}</small></div>
        <div className="actions"><button disabled={busy} onClick={()=>void run(()=>closeTask(task.id,'done'))}>{t('Mark task done')}</button>
        <button disabled={busy} className="secondary" onClick={()=>void run(()=>closeTask(task.id,'dismissed'))}>{t('Dismiss task')}</button></div>
      </li>)}</ul>}
      <p className="muted">{t('When the money arrives, enter the receipt separately in your records.')}</p>
    </>}
  </section>;
}
