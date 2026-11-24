import {useEffect,useState} from 'react';
import {money} from './api';
import {formatDate} from './format';
import {t,T} from './i18n';

type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
type Strategy={tier:string;title:string;steps:string[];cost:string;benefit:string;effort:string;risk:string;measure:string};
type Insight={key:string;kind:string;severity:number;title:string;what:string;why:string;impact_minor:number|null;confidence:string;evidence:string[];measure:string;
  strategies:Strategy[];choice:{tier:string;response:string;at:number}|null};
type Advice={currency:string;insights:Insight[];gaps:{lever:string;why:string;idea:string}[];
  outcomes:{insight:string;tier:string;since:number;before:{net_minor:number;income_minor:number};now:{net_minor:number;income_minor:number};note:string}[]};

const TIER:Record<string,string>={A:T('No or little spending'),B:T('Small investment'),C:T('Bigger move')};
const CONFIDENCE:Record<string,string>={high:T('From your own records'),medium:T('Based on a quoted or researched price'),low:T('Based on an estimate')};
const day=(ts:number)=>formatDate(new Date(ts*1000).toISOString().slice(0,10));

/** What to do next: problems found from the owner's own numbers, each with three costed ways to act. */
export function AdvisorPanel({request}:{request:Request}){
  const [data,setData]=useState<Advice|null>(null);
  const [open,setOpen]=useState<string|null>(null);
  const [busy,setBusy]=useState(false);
  const load=()=>request<Advice>('/advisor').then(d=>{setData(d);setOpen(o=>o??d.insights.find(i=>i.strategies.length)?.key??null);}).catch(()=>undefined);
  useEffect(()=>{void load();},[]);// eslint-disable-line react-hooks/exhaustive-deps
  async function choose(insight:string,tier:string,response:string){
    setBusy(true);try{await request('/advisor/feedback',{insight,tier,response});await load();}finally{setBusy(false);}
  }
  if(!data||(!data.insights.length&&!data.gaps.length&&!data.outcomes.length))return null;
  const m=(v:number)=>money(v,data.currency);
  return <section className="panel advisor-panel" aria-labelledby="advisor-title">
    <div className="panel-heading"><div><p className="eyebrow">{t('Advisor')}</p><h2 id="advisor-title">{t('What to do next')}</h2></div></div>
    <ul className="advice-list">{data.insights.map(i=><li key={i.key} className={'sev-'+i.severity}>
      <button className="advice-head" aria-expanded={open===i.key} onClick={()=>setOpen(open===i.key?null:i.key)}>
        <span className={'pill level-'+Math.min(i.severity,3)}>{i.severity>=3?t('Urgent'):i.severity===2?t('Important'):i.severity===1?t('Opportunity'):t('Missing data')}</span>
        <b>{i.title}</b>{i.impact_minor!=null&&<span className={i.impact_minor<0?'negative':'positive'}>{i.impact_minor<0?'−':'+'}{m(Math.abs(i.impact_minor))}</span>}</button>
      {open===i.key&&<div className="advice-body">
        <p><b>{t('What happened')}:</b> {i.what}</p>
        <p><b>{t('Why it matters')}:</b> {i.why}</p>
        <p className="muted small-print">{t(CONFIDENCE[i.confidence]||i.confidence)}{!!i.evidence.length&&' · '+i.evidence.join('; ')} · {t('Measure')}: {i.measure}</p>
        {!!i.strategies.length&&<div className="strategies">{i.strategies.map(s=><article key={s.tier} className={i.choice?.tier===s.tier?'chosen':undefined}>
          <p className="eyebrow">{s.tier} · {t(TIER[s.tier])}</p><h4>{s.title}</h4>
          <ul>{s.steps.map((x,n)=><li key={n}>{x}</li>)}</ul>
          <dl><div><dt>{t('Cost')}</dt><dd>{s.cost}</dd></div><div><dt>{t('Benefit')}</dt><dd>{s.benefit}</dd></div>
            <div><dt>{t('Effort')}</dt><dd>{s.effort}</dd></div><div><dt>{t('Risk')}</dt><dd>{s.risk}</dd></div><div><dt>{t('Measure')}</dt><dd>{s.measure}</dd></div></dl>
          {i.choice?.tier===s.tier?<p className="success">{i.choice.response==='done'?t('Marked done {date}',{date:day(i.choice.at)}):t('You chose this {date}',{date:day(i.choice.at)})}
            {i.choice.response==='doing'&&<button className="link" disabled={busy} onClick={()=>void choose(i.key,s.tier,'done')}>{t('Mark done')}</button>}</p>
          :<button className="secondary small-button" disabled={busy} onClick={()=>void choose(i.key,s.tier,'doing')}>{t('I\'ll try this')}</button>}
        </article>)}</div>}
        <button className="link" disabled={busy} onClick={()=>void choose(i.key,'-','dismissed')}>{t('Not relevant: hide for 30 days')}</button>
      </div>}
    </li>)}</ul>
    {!!data.gaps.length&&<><h3>{t('Where you can compete')}</h3><ul className="gap-list">{data.gaps.map(g=><li key={g.lever}><b>{g.lever}</b><small>{g.why}</small><span>{g.idea}</span></li>)}</ul></>}
    {!!data.outcomes.length&&<><h3>{t('Since you acted')}</h3><ul className="gap-list">{data.outcomes.map((o,n)=><li key={n}>
      <b>{t('Option {tier}, chosen {date}',{tier:o.tier,date:day(o.since)})}</b>
      <span>{t('Net if sold today: {before} → {now}',{before:m(o.before.net_minor),now:m(o.now.net_minor)})}</span><small>{o.note}</small></li>)}</ul></>}
  </section>;
}
