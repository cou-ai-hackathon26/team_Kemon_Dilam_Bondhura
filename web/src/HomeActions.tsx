import {useCurrency} from './Currency';
import {formatDate,prettyVendor} from './format';
import {t} from './i18n';
import type {Forecast,Invoice,NextCollection,Proposal,Session} from './types';

export type HomeNavigate=(page:'Data'|'Invoices'|'Cash flow'|'Approvals'|'Audit',target?:string)=>void;

export function HomeActions({asOf,forecast,invoices,nextCollection,proposals,role,navigate}:{
  asOf:string;forecast:Forecast|null;invoices:Invoice[];nextCollection:NextCollection|null;
  proposals:Proposal[];role:Session['role'];navigate:HomeNavigate;
}){
  const {nativeMoney}=useCurrency();
  const shortfall=forecast?.days.find(day=>day.date===forecast.first_shortfall_on);
  const overdue=invoices.filter(invoice=>invoice.status==='open'&&invoice.due_on<asOf)
    .sort((a,b)=>a.due_on.localeCompare(b.due_on)||a.invoice_id.localeCompare(b.invoice_id))[0];
  const pending=role==='approver'?proposals.find(proposal=>proposal.state==='AWAITING_APPROVAL'&&proposal.details.expires_at*1000>Date.now()):undefined;
  const actions=[];
  if(shortfall)actions.push({
    title:t('Projected cash shortfall'),
    detail:t('On {date}, cash is projected to fall {amount} below your reserve.',{date:formatDate(shortfall.date),amount:nativeMoney(shortfall.shortfall_minor)}),
    label:t('Review cash plan'),open:()=>navigate('Cash flow','collection-planner')
  });
  if(role==='owner'&&nextCollection)actions.push({
    title:t('Next collection task'),detail:t('Collect {amount} by {date}',{amount:nativeMoney(nextCollection.amount_minor),date:formatDate(nextCollection.due_on)}),
    label:t('Open collection tasks'),open:()=>navigate('Cash flow','collection-tasks')
  });
  if(pending)actions.push({
    title:t('Payment awaiting review'),detail:prettyVendor(pending.details.vendor_id)+' · '+nativeMoney(pending.details.amount_minor),
    label:t('Review payment'),open:()=>navigate('Approvals','payment-'+pending.proposal_id)
  });
  if(overdue)actions.push({
    title:t('Oldest overdue bill'),detail:t('{supplier} · {amount} due {date}',{supplier:prettyVendor(overdue.vendor_id),amount:nativeMoney(overdue.amount_minor),date:formatDate(overdue.due_on)}),
    label:t('View bill'),open:()=>navigate('Invoices','bill-'+overdue.invoice_id)
  });
  if(!actions.length)return null;
  return <section className="panel home-actions" aria-labelledby="attention-title"><h2 id="attention-title">{t('Needs attention')}</h2>
    <ul>{actions.map(action=><li key={action.title}><div><strong>{action.title}</strong><p>{action.detail}</p></div>
      <button className="secondary" onClick={action.open}>{action.label}</button></li>)}</ul>
  </section>;
}
