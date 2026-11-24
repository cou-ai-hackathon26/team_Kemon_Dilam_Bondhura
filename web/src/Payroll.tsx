import {useEffect,useMemo,useState} from 'react';
import {money} from './api';
import {formatDate} from './format';
import {t} from './i18n';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Employee={id:string;name:string;designation:string;salary_minor:number;joined_on:string;status:'active'|'left';username:string|null;invited:boolean};
type Payment={id:string;employee_id:string;period:string;kind:'salary'|'bonus';amount_minor:number;paid_on:string;note:string};
type Overview={period:string;currency:string;employees:Employee[];payments:Payment[];
  summary:{active:number;monthly_salary_minor:number;paid_this_period_minor:number;due_this_period_minor:number;unpaid_count:number;year_to_date_minor:number}};
type Form='add'|'bonus'|null;

const toMinor=(value:string)=>Math.round(Number(value.replace(/,/g,''))*100);
const monthName=(period:string)=>new Date(period+'-01T00:00:00').toLocaleDateString(document.documentElement.lang||undefined,{month:'long',year:'numeric'});

/** Payroll for the workspace owner: employees, monthly salaries, bonuses and private invite links. */
export default function Payroll({request,onChanged}:{request:Request;onChanged:()=>void}){
  const [data,setData]=useState<Overview|null>(null);
  const [period,setPeriod]=useState('');
  const [form,setForm]=useState<Form>(null);
  const [busy,setBusy]=useState(false);
  const [message,setMessage]=useState('');
  const [error,setError]=useState('');
  const [links,setLinks]=useState<Record<string,string>>({});
  const [filter,setFilter]=useState('');
  const load=async(p=period)=>{const next=await request<Overview>('/payroll'+(p?'?period='+p:''));setData(next);setPeriod(next.period);};
  useEffect(()=>{void load().catch(e=>setError(e instanceof Error?e.message:t('Request failed.')));},[]);// eslint-disable-line react-hooks/exhaustive-deps
  async function run(action:()=>Promise<string|void>){
    setBusy(true);setError('');setMessage('');
    try{const done=await action();if(done)setMessage(done);await load();}
    catch(e){setError(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  const names=useMemo(()=>Object.fromEntries((data?.employees||[]).map(e=>[e.id,e.name])),[data]);
  const paidThisMonth=useMemo(()=>new Set((data?.payments||[]).filter(p=>p.period===period&&p.kind==='salary').map(p=>p.employee_id)),[data,period]);
  const yearTotal=(id:string)=>(data?.payments||[]).filter(p=>p.employee_id===id&&p.period.startsWith(period.slice(0,4))).reduce((s,p)=>s+p.amount_minor,0);
  if(!data)return <section className="panel" role="status">{error||t('Loading payroll…')}</section>;
  const c=data.currency,m=(minor:number)=>money(minor,c),s=data.summary;
  const history=data.payments.filter(p=>!filter||p.employee_id===filter).slice(0,60);
  return <div className="payroll">
    <section className="payroll-top">
      <label className="payroll-month">{t('Month')}<input type="month" value={period} onChange={e=>{if(e.target.value)void run(()=>load(e.target.value));}}/></label>
      <div className="payroll-actions">
        {!!data.employees.length&&<><button disabled={busy||!s.unpaid_count} onClick={()=>void run(async()=>{
          const r=await request<{paid:number;total_minor:number}>('/payroll/salaries',{period});onChanged();
          return t('Paid {count} salaries for {month}: {total}.',{count:r.paid,month:monthName(period),total:m(r.total_minor)});})}>
          {s.unpaid_count?t('Pay {count} salaries · {total}',{count:s.unpaid_count,total:m(s.due_this_period_minor)}):t('All salaries paid')}</button>
        <button className="secondary" disabled={busy||!s.active} onClick={()=>setForm(form==='bonus'?null:'bonus')}>{t('Give a bonus')}</button></>}
        <button className="secondary" disabled={busy} onClick={()=>setForm(form==='add'?null:'add')}>{t('Add employee')}</button>
      </div>
    </section>
    <section className="metrics payroll-metrics">
      <article className="metric"><p>{t('Employees')}</p><strong>{s.active}</strong></article>
      <article className="metric"><p>{t('Monthly salaries')}</p><strong>{m(s.monthly_salary_minor)}</strong></article>
      <article className="metric"><p>{t('Paid in {month}',{month:monthName(period)})}</p><strong>{m(s.paid_this_period_minor)}</strong></article>
      <article className="metric"><p>{t('Still to pay')}</p><strong>{m(s.due_this_period_minor)}</strong></article>
    </section>
    {form==='add'&&<AddEmployee busy={busy} currency={c} onSubmit={body=>void run(async()=>{await request('/payroll/employees',body);setForm(null);return t('{name} added.',{name:body.name});})}/>}
    {form==='bonus'&&<GiveBonus busy={busy} currency={c} employees={data.employees.filter(e=>e.status==='active')} month={monthName(period)}
      onSubmit={body=>void run(async()=>{const r=await request<{paid:number;total_minor:number}>('/payroll/bonus',{...body,period});setForm(null);onChanged();
        return t('Bonus paid to {count}: {total}.',{count:r.paid,total:m(r.total_minor)});})}/>}
    {message&&<p className="success" role="status">{message}</p>}
    {error&&<p className="error" role="alert">{error}</p>}
    <section className="panel">
      <h2>{t('Employees')}</h2>
      {!data.employees.length?<p className="muted">{t('Add your first employee to start paying salaries and sharing payslips.')}</p>:
      <div className="table-wrap"><table className="payroll-table"><thead><tr><th>{t('Employee')}</th><th>{t('Salary')}</th><th>{monthName(period)}</th><th>{t('This year')}</th><th>{t('Payslip access')}</th><th/></tr></thead>
        <tbody>{data.employees.map(e=><tr key={e.id} className={e.status==='left'?'left':undefined}>
          <td><b>{e.name}</b><small>{e.designation||'—'}{e.status==='left'&&' · '+t('Left')}</small></td>
          <td className="amount">{m(e.salary_minor)}</td>
          <td>{paidThisMonth.has(e.id)?<span className="pill paid">{t('Paid')}</span>:e.status==='active'?<span className="pill due">{t('Due')}</span>:'—'}</td>
          <td className="amount">{m(yearTotal(e.id))}</td>
          <td>{e.username?<span className="pill joined">@{e.username}</span>:links[e.id]?<InviteBox link={links[e.id]} name={e.name}/>:
            <button className="secondary small-button" disabled={busy} onClick={()=>void run(async()=>{
              const r=await request<{link:string}>(`/payroll/employees/${e.id}/invite`,{});setLinks(l=>({...l,[e.id]:r.link}));})}>{e.invited?t('New invite link'):t('Invite link')}</button>}</td>
          <td><button className="ghost small-button" disabled={busy} onClick={()=>void run(async()=>{await request(`/payroll/employees/${e.id}/status`,{active:e.status!=='active'});})}>{e.status==='active'?t('Mark as left'):t('Rehire')}</button></td>
        </tr>)}</tbody></table></div>}
    </section>
    <section className="panel">
      <div className="panel-heading"><h2>{t('Payment history')}</h2>
        <select value={filter} onChange={e=>setFilter(e.target.value)} aria-label={t('Filter by employee')}><option value="">{t('Everyone')}</option>{data.employees.map(e=><option key={e.id} value={e.id}>{e.name}</option>)}</select></div>
      {!history.length?<p className="muted">{t('No payments yet.')}</p>:
      <div className="table-wrap"><table><thead><tr><th>{t('Month')}</th><th>{t('Employee')}</th><th>{t('Type')}</th><th>{t('Amount')}</th><th>{t('Paid on')}</th></tr></thead>
        <tbody>{history.map(p=><tr key={p.id}><td>{monthName(p.period)}</td><td>{names[p.employee_id]||'—'}</td>
          <td>{p.kind==='salary'?t('Salary'):t('Bonus')}{p.note&&<small>{p.note}</small>}</td><td className="amount">{m(p.amount_minor)}</td><td>{formatDate(p.paid_on)}</td></tr>)}</tbody></table></div>}
      <p className="muted small-print">{t('Every payment is also recorded as cash spent in your books. Payroll history cannot be edited.')}</p>
    </section>
  </div>;
}

function InviteBox({link,name}:{link:string;name:string}){
  const [copied,setCopied]=useState(false);
  const text=t('Hello {name}, open this private link to see your payslips on Capzoryx: {link}',{name,link});
  return <div className="invite-box">
    <button className="secondary small-button" onClick={()=>void navigator.clipboard?.writeText(link).then(()=>setCopied(true))}>{copied?t('Copied'):t('Copy link')}</button>
    <a className="button small-button" href={'https://wa.me/?text='+encodeURIComponent(text)} target="_blank" rel="noreferrer">WhatsApp</a>
    <small>{t('Valid for 7 days, one person.')}</small>
  </div>;
}

function AddEmployee({busy,currency,onSubmit}:{busy:boolean;currency:string;onSubmit:(body:{name:string;designation:string;salary_minor:number;joined_on:string})=>void}){
  const [name,setName]=useState('');const [designation,setDesignation]=useState('');const [salary,setSalary]=useState('');
  const [joined,setJoined]=useState(new Date().toISOString().slice(0,10));
  const amount=toMinor(salary);
  return <form className="panel payroll-form" onSubmit={e=>{e.preventDefault();if(name.trim()&&amount>0)onSubmit({name:name.trim(),designation:designation.trim(),salary_minor:amount,joined_on:joined});}}>
    <h2>{t('Add employee')}</h2>
    <div className="form-row">
      <label>{t('Name')}<input required maxLength={80} value={name} onChange={e=>setName(e.target.value)}/></label>
      <label>{t('Designation')}<input maxLength={80} value={designation} onChange={e=>setDesignation(e.target.value)}/></label>
      <label>{t('Monthly salary ({currency})',{currency})}<input required inputMode="decimal" value={salary} onChange={e=>setSalary(e.target.value)} placeholder="25000"/></label>
      <label>{t('Joined on')}<input type="date" value={joined} onChange={e=>setJoined(e.target.value)}/></label>
    </div>
    <button disabled={busy||!name.trim()||!(amount>0)}>{t('Add employee')}</button>
  </form>;
}

function GiveBonus({busy,currency,employees,month,onSubmit}:{busy:boolean;currency:string;employees:Employee[];month:string;onSubmit:(body:{amount_minor:number;note:string;employee_ids:string[]})=>void}){
  const [amount,setAmount]=useState('');const [note,setNote]=useState('');const [chosen,setChosen]=useState<string[]>([]);
  const value=toMinor(amount);
  const toggle=(id:string)=>setChosen(c=>c.includes(id)?c.filter(x=>x!==id):[...c,id]);
  return <form className="panel payroll-form" onSubmit={e=>{e.preventDefault();if(value>0)onSubmit({amount_minor:value,note:note.trim(),employee_ids:chosen});}}>
    <h2>{t('Give a bonus for {month}',{month})}</h2>
    <div className="form-row">
      <label>{t('Amount per person ({currency})',{currency})}<input required inputMode="decimal" value={amount} onChange={e=>setAmount(e.target.value)} placeholder="5000"/></label>
      <label>{t('Reason')}<input maxLength={120} value={note} onChange={e=>setNote(e.target.value)} placeholder={t('Eid bonus')}/></label>
    </div>
    <fieldset className="bonus-people"><legend>{chosen.length?t('{count} selected',{count:chosen.length}):t('Everyone')}</legend>
      {employees.map(e=><label key={e.id} className="check"><input type="checkbox" checked={chosen.includes(e.id)} onChange={()=>toggle(e.id)}/><span>{e.name}</span></label>)}</fieldset>
    <button disabled={busy||!(value>0)}>{chosen.length?t('Pay bonus to {count}',{count:chosen.length}):t('Pay bonus to everyone')}</button>
  </form>;
}
