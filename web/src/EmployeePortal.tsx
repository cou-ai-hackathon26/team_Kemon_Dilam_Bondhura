import {useEffect,useState} from 'react';
import {accountRequest,money} from './api';
import {formatDate} from './format';
import {t} from './i18n';
import {Logo} from './Logo';
import type {Session} from './types';

type Payment={id:string;period:string;kind:'salary'|'bonus';amount_minor:number;paid_on:string;note:string};
type Job={employer:string;name:string;designation:string;salary_minor:number;status:string;joined_on:string;currency:string;payments:Payment[];year_to_date_minor:number};
type Month={period:string;salary:number;bonus:number;notes:string[];paid_on:string};

const monthName=(period:string)=>new Date(period+'-01T00:00:00').toLocaleDateString(document.documentElement.lang||undefined,{month:'long',year:'numeric'});
function byMonth(payments:Payment[]):Month[]{
  const months=new Map<string,Month>();
  for(const p of payments){
    const m=months.get(p.period)||{period:p.period,salary:0,bonus:0,notes:[],paid_on:p.paid_on};
    if(p.kind==='salary')m.salary+=p.amount_minor;else{m.bonus+=p.amount_minor;if(p.note)m.notes.push(p.note);}
    if(p.paid_on>m.paid_on)m.paid_on=p.paid_on;
    months.set(p.period,m);
  }
  return [...months.values()].sort((a,b)=>b.period.localeCompare(a.period));
}

/** The employee dashboard: only this person's own salary and bonus payments. */
export function EmployeePortal({logout}:{logout:()=>void}){
  const [jobs,setJobs]=useState<Job[]|null>(null);
  const [error,setError]=useState('');
  useEffect(()=>{void accountRequest<{employments:Job[]}>('/payroll/me').then(r=>setJobs(r.employments)).catch(e=>setError(e instanceof Error?e.message:t('Request failed.')));},[]);
  return <div className="employee-portal">
    <header className="employee-bar"><Logo/><button className="secondary small-button" onClick={logout}>{t('Sign out')}</button></header>
    <main className="employee-main">
      {error&&<p className="error" role="alert">{error}</p>}
      {!jobs&&!error&&<p role="status">{t('Loading your payslips…')}</p>}
      {jobs?.map(job=>{const c=job.currency,months=byMonth(job.payments);return <section key={job.employer+job.name} className="employee-job">
        <div className="employee-head"><div><p className="eyebrow">{job.employer}</p><h1>{job.name}</h1><p className="muted">{job.designation}{job.status==='left'&&' · '+t('Former employee')}</p></div></div>
        <div className="metrics">
          <article className="metric"><p>{t('Monthly salary')}</p><strong>{money(job.salary_minor,c)}</strong></article>
          <article className="metric"><p>{t('Received this year')}</p><strong>{money(job.year_to_date_minor,c)}</strong></article>
          <article className="metric"><p>{t('Last payment')}</p><strong>{months[0]?formatDate(months[0].paid_on):'—'}</strong></article>
        </div>
        <section className="panel"><h2>{t('Payslips')}</h2>
          {!months.length?<p className="muted">{t('No payments yet. Your payslips will appear here when you are paid.')}</p>:
          <ul className="payslips">{months.map(m=><li key={m.period}>
            <div className="payslip-head"><b>{monthName(m.period)}</b><strong>{money(m.salary+m.bonus,c)}</strong></div>
            <dl><div><dt>{t('Salary')}</dt><dd>{m.salary?money(m.salary,c):'—'}</dd></div>
              <div><dt>{t('Bonus')}{!!m.notes.length&&<small>{m.notes.join(', ')}</small>}</dt><dd>{m.bonus?money(m.bonus,c):'—'}</dd></div>
              <div><dt>{t('Paid on')}</dt><dd>{formatDate(m.paid_on)}</dd></div></dl>
          </li>)}</ul>}
        </section>
      </section>;})}
    </main>
  </div>;
}

/** The page behind an employee's invite link: confirm who invited them and choose a username and password. */
export function EmployeeJoin({code,onSession}:{code:string;onSession:(session:Session)=>void}){
  const [invite,setInvite]=useState<{name:string;designation:string;employer:string}|null>(null);
  const [error,setError]=useState('');
  const [username,setUsername]=useState('');
  const [password,setPassword]=useState('');
  const [show,setShow]=useState(false);
  const [busy,setBusy]=useState(false);
  useEffect(()=>{void accountRequest<{name:string;designation:string;employer:string}>('/payroll/invite/check',{code}).then(setInvite)
    .catch(()=>setError(t('This invite link has expired or was already used. Ask your employer for a new one.')));},[code]);
  async function submit(){
    setBusy(true);setError('');
    try{const session=await accountRequest<Session>('/payroll/invite/accept',{code,username,password});
      const url=new URL(window.location.href);url.searchParams.delete('employee');window.history.replaceState(null,'',url.pathname+url.search+url.hash);onSession(session);}
    catch(e){setError(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  return <main className="employee-join"><div className="panel auth-card">
    <Logo/>
    {!invite?<p className={error?'error':'muted'} role={error?'alert':'status'}>{error||t('Checking your invite…')}</p>:<>
      <h1>{t('Welcome, {name}',{name:invite.name})}</h1>
      <p className="muted">{t('{employer} invited you to see your salary and bonus payments.',{employer:invite.employer})}</p>
      <form onSubmit={e=>{e.preventDefault();void submit();}}>
        <label htmlFor="join-username">{t('Choose a username')}</label>
        <input id="join-username" autoComplete="username" autoCapitalize="none" minLength={3} maxLength={32} pattern="[A-Za-z0-9_]{3,32}" required value={username} onChange={e=>setUsername(e.target.value)} placeholder={t('letters, numbers, underscore')}/>
        <label htmlFor="join-password">{t('Choose a password')}</label>
        <div className="password-field"><input id="join-password" type={show?'text':'password'} autoComplete="new-password" minLength={8} maxLength={128} required value={password} onChange={e=>setPassword(e.target.value)}/>
          <button type="button" className="secondary" onClick={()=>setShow(!show)}>{show?t('Hide'):t('Show')}</button></div>
        {error&&<p className="error" role="alert">{error}</p>}
        <button className="full-width" disabled={busy}>{busy?t('Please wait…'):t('Create my login')}</button>
      </form></>}
  </div></main>;
}
