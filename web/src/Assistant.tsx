import {lazy,Suspense,useEffect,useRef,useState,type FormEvent} from 'react';
import {ApiError} from './api';
import {Icon} from './Icon';
import {useCurrency} from './Currency';
import {DraftCard} from './DraftCard';
import {formatDate,prettyVendor} from './format';
import {localAnswer,type AssistantAction} from './localAssistant';
import {looksLikeTransaction,parseRecordForDraft} from './recordParser';
import {hisabDraftMessage,hisabPrivacyNote,type HisabDraftResult} from './hisabDraft';
import type {AgentResult,CashEvent,Draft,Forecast,Invoice,Profile,Session,Snapshot} from './types';
import {t,T} from './i18n';

type Props={snapshot:Snapshot|null;forecast:Forecast|null;week:Forecast|null;events:CashEvent[];invoices:Invoice[];session:Session;profile:Profile|null;cloudAvailable:boolean;hisabAvailable:boolean;revision:string;request:<T>(path:string,body?:unknown)=>Promise<T>;onSaved:()=>Promise<void>;onProposal:()=>Promise<void>;onNavigate:(action:AssistantAction)=>void;onClose?:()=>void;visible:boolean};
type BrainButton={label:string;payload:string};
type BrainReply={understood:boolean;text:string;buttons:BrainButton[]};
type Message={id:number;role:'user'|'assistant';text:string;quick?:BrainButton[];kind?:'device'|'smart'|'hisab';actions?:AssistantAction[];draft?:Draft;agent?:AgentResult;offTopic?:string;retry?:string};
type AskMode='default'|'off-topic'|'hisab'|'voice';
const VoiceNote=lazy(()=>import('./VoiceNote').then(m=>({default:m.VoiceNote})));
const hints=[T('What is my balance?'),T('I paid Rahman 5000 for freight yesterday'),T('Which loan fits my cash flow?'),T('Which bills are overdue?')];

export default function Assistant({snapshot,forecast,week,events,invoices,session,profile,cloudAvailable,hisabAvailable,revision,request,onSaved,onProposal,onNavigate,onClose,visible}:Props){
  const {currency,digits,nativeMoney:money}=useCurrency();
  const [messages,setMessages]=useState<Message[]>([]);const [task,setTask]=useState('');const [busy,setBusy]=useState(false);
  const [picking,setPicking]=useState(false);const [target,setTarget]=useState('');
  const [voiceOpen,setVoiceOpen]=useState(false);
  const [speakReplies,setSpeakReplies]=useState(false);
  const [readNotice,setReadNotice]=useState('');
  const lastIntent=useRef('');const nextId=useRef(0);const mounted=useRef(true);const pending=useRef(false);
  const log=useRef<HTMLDivElement>(null);const input=useRef<HTMLTextAreaElement>(null);
  const owner=session.role==='owner';const canAddRecords=owner||session.role==='bookkeeper';const smart=!!profile?.smart_assist&&cloudAvailable;
  const canUseHisab=owner&&hisabAvailable&&currency==='BDT';
  const allowHisabBackup=!!profile?.smart_assist&&!!profile.record_api_fallback;
  useEffect(()=>{mounted.current=true;return()=>{mounted.current=false;};},[]);
  useEffect(()=>{if(visible)input.current?.focus();},[visible]);
  useEffect(()=>{if(log.current)log.current.scrollTop=log.current.scrollHeight;},[messages,busy]);
  const add=(...m:Omit<Message,'id'>[])=>setMessages(cur=>[...cur,...m.map(x=>({...x,id:nextId.current++}))].slice(-40));
  const openBills=invoices.filter(i=>i.status==='open');

  function readAnswer(text:string){
    const synth=window.speechSynthesis;
    const lang=/[\u0980-\u09ff]/u.test(text)?'bn':'en';
    const voice=synth?.getVoices().find(item=>item.localService&&item.lang.toLowerCase().startsWith(lang));
    if(!voice){setReadNotice(t('This device has no offline voice for the reply language. The answer is shown above.'));return;}
    synth.cancel();
    const reply=new SpeechSynthesisUtterance(text);
    reply.voice=voice;reply.lang=voice.lang;
    setReadNotice('');
    synth.speak(reply);
  }
  async function quick(button:BrainButton){
    const page=/^__open:(.+)__$/.exec(button.payload);
    if(page){onNavigate(page[1] as AssistantAction);return;}
    if(pending.current)return;
    pending.current=true;setBusy(true);add({role:'user',text:button.label});
    try{
      const brain=await request<BrainReply>('/chat/message',{text:button.payload});
      if(mounted.current)add({role:'assistant',kind:'device',text:brain.text,quick:brain.buttons});
    }catch(e){if(mounted.current)add({role:'assistant',kind:'device',text:e instanceof Error?e.message:t('Request failed.')});}
    finally{pending.current=false;if(mounted.current)setBusy(false);}
  }
  async function ask(question:string,mode:AskMode='default'){
    const q=question.trim();
    const useHisab=mode==='hisab';
    if(!q||pending.current||(useHisab&&(!canUseHisab||q.length<3||q.length>400)))return;
    pending.current=true;setBusy(true);setTask('');
    add({role:'user',text:q,kind:useHisab?'hisab':undefined});
    function reply(message:Omit<Message,'id'|'role'>){
      if(!mounted.current)return;
      add({role:'assistant',...message});
      if(mode==='voice'&&speakReplies)readAnswer(message.text);
    }
    try{
      if(useHisab){
        const result=await request<HisabDraftResult>('/assist/hisab',{text:q,consent:true,...(allowHisabBackup?{allow_api_fallback:true}:{})});
        if(mounted.current)add({role:'assistant',kind:'hisab',text:hisabDraftMessage(result.source),draft:result.draft});
        return;
      }
      // 1. A sentence that describes a payment or a bill becomes a draft record to confirm.
      if(canAddRecords&&mode!=='voice'){
        const draft=await parseRecordForDraft(q,{currency,digits});
        if(draft){add({role:'assistant',kind:'device',text:draft.kind==='invoice'?t('I read this as a bill you still have to pay. Check it, then add it to your records.'):draft.direction==='inflow'?t('I read this as money you received. Check it, then add it to your records.'):t('I read this as a payment you made. Check it, then add it to your records.'),draft});return;}
      }
      // Workspace questions can use current balances, forecasts, invoices and help without a cloud model.
      const brain=await request<BrainReply>('/chat/message',{text:q.slice(0,240)}).catch(()=>null);
      if(brain?.understood&&brain.text){reply({kind:'device',text:brain.text,quick:brain.buttons});return;}
      // 3. Offline or not understood: the on-device assistant.
      const local=localAnswer(q,{snapshot,forecast,week,events,invoices},lastIntent.current);
      if(local.handled){lastIntent.current=local.intent;reply({kind:'device',text:local.text,actions:local.actions});return;}
      const blocked=['security','approval','limit'].includes(local.intent);
      if(blocked){add({role:'assistant',kind:'device',text:local.text});return;}
      // 3. Everything else goes to smart assist, if it is on.
      const offTopic=local.intent==='out_of_domain';
      if(offTopic&&mode!=='off-topic'){add({role:'assistant',kind:'device',text:local.text,offTopic:q});return;}
      if(!smart){add({role:'assistant',kind:'device',text:profile&&!profile.smart_assist?t('I can answer questions about your business right away, for example “আমার ব্যবসা সম্পর্কে বলো”, “বাজার কেমন?” or “কোন পণ্যে লাভ বেশি?”. For other questions, turn on smart assist in Settings.'):t('Smart assist is not available right now. I can still help with your balance, invoices, cash flow, loans and adding records.')});return;}
      if(owner&&mode!=='voice'&&looksLikeTransaction(q)){
        const r=await request<{draft:Draft}>('/assist/extract',{text:q});
        if(!mounted.current)return;
        if(r.draft.ok){add({role:'assistant',kind:'smart',text:t('I read this from your message. Check it, then add it to your records.'),draft:r.draft});return;}
      }
      const history=messages.filter(m=>m.kind!=='hisab'&&!m.draft&&!m.agent).slice(-4).map(m=>({role:m.role,text:m.text.slice(0,500)}));
      const r=await request<{answer:string}>('/assist/ask',{question:q,history});
      reply({kind:'smart',text:r.answer});
    }catch(e){
      if(!mounted.current)return;
      let text=e instanceof Error?e.message:t('Something went wrong. Please try again.');
      const editableHisabError=useHisab&&e instanceof ApiError&&e.status===422;
      if(useHisab&&e instanceof ApiError&&e.status===503){
        text=t('Hisab-LM is temporarily unavailable. You can send your message normally.');
      }else if(editableHisabError&&e.code==='HISAB_REVIEW_NEEDED'){
        text=t('Hisab-LM could not verify this payment. Check the amount and who paid, then try again or send it normally.');
      }else if(!useHisab&&e instanceof ApiError&&(e.code==='AI_QUOTA_EXHAUSTED'||e.status===503)){
        text=t('Smart assist is busy right now. Please try again in a minute. Your balance, invoices and cash-flow questions still work.');
      }
      if(editableHisabError)setTask(q);
      add({role:'assistant',kind:useHisab?'hisab':'device',text,retry:editableHisabError?undefined:q});
    }finally{
      pending.current=false;
      if(mounted.current){
        setBusy(false);
        if(mode==='voice')log.current?.scrollIntoView({block:'nearest'});
        else input.current?.focus();
      }
    }
  }
  async function prepare(){
    if(!target||pending.current)return;pending.current=true;setBusy(true);setPicking(false);
    const bill=openBills.find(i=>i.invoice_id===target);
    add({role:'user',text:`Prepare a payment for ${bill?bill.invoice_number+' · '+prettyVendor(bill.vendor_id):'this bill'}`});
    try{
      const r=await request<AgentResult>('/agent/tasks',{task:'Review this invoice and prepare it for approval if it is safe to pay.',invoice_id:target,allow_proposal:true,external_ai_consent:true,fallback_ai_consent:false});
      if(mounted.current)add({role:'assistant',kind:'smart',text:r.explanation.join('\n\n'),agent:r});
    }catch(e){if(mounted.current)add({role:'assistant',kind:'device',text:e instanceof Error?e.message:'I could not prepare that payment.'});}
    finally{pending.current=false;if(mounted.current)setBusy(false);}
  }
  return <section className={voiceOpen?'panel chat-panel chat-panel-with-voice':'panel chat-panel'} aria-label={t('Assistant')}>
    <div className="chat-heading"><div className="assistant-orb" aria-hidden="true"><Icon name="spark"/></div><div><h2>{t('Assistant')}</h2><p>{t('Ask a question, or tell me what you paid or received')}</p></div>{onClose&&<button className="secondary chat-close" onClick={onClose} aria-label={t('Minimize assistant')}>×</button>}</div>
    <div className="chat-log" ref={log} role="log" aria-label={t('Conversation')} aria-live="polite">
      {!messages.length&&<div className="chat-welcome"><span aria-hidden="true"><Icon name="spark"/></span><h3>{t('How can I help?')}</h3><p>{t('Ask a question, or tell me what you paid or received. I can add records for you, explain your numbers and compare loan offers.')}</p><div className="chat-suggestions">{hints.filter(h=>canAddRecords||!/^I paid/.test(h)).map(h=><button key={h} className="secondary" onClick={()=>void ask(h)}>{t(h)}</button>)}</div></div>}
      {messages.map(m=><article className={'chat-message '+m.role} key={m.id}>
        {m.role==='assistant'&&<small>{t('Assistant')}</small>}
        {m.text.split('\n\n').map((para,i)=><p key={i}>{para}</p>)}
        {m.draft&&canAddRecords&&<DraftCard canTrustVendor={owner} compact draft={m.draft} revision={revision} request={request} onSaved={onSaved} onDiscard={()=>setMessages(cur=>cur.map(x=>x.id===m.id?{...x,draft:undefined,text:t('Discarded. Nothing was added.')}:x))}/>}
        {!!m.quick?.length&&<div className="chat-actions">{m.quick.map(b=><button className="secondary" key={b.payload} disabled={busy} onClick={()=>void quick(b)}>{b.label}</button>)}</div>}
        {!!m.actions?.length&&<div className="chat-actions">{m.actions.map(a=><button className="secondary" key={a} onClick={()=>onNavigate(a)}>{({Financing:t('Open Loans'),Data:t('Open Add records'),Audit:t('Open Activity')} as Record<string,string>)[a]||`Open ${a}`}</button>)}</div>}
        {m.offTopic&&smart&&<div className="chat-actions"><button className="secondary" disabled={busy} onClick={()=>void ask(m.offTopic!,'off-topic')}>{t('It is about my business: try anyway')}</button></div>}
        {m.retry&&(m.kind!=='hisab'||canUseHisab)&&<div className="chat-actions"><button className="secondary" disabled={busy} aria-describedby={m.kind==='hisab'?'assistant-hisab-privacy':undefined} onClick={()=>void ask(m.retry!,m.kind==='hisab'?'hisab':'default')}>{m.kind==='hisab'?t('Retry with Hisab-LM'):t('Try again')}</button></div>}
        {m.agent?.proposal&&<button onClick={()=>void onProposal()}>{t('Open payment approval')}</button>}
        {m.agent&&!!m.agent.limitations.length&&<details><summary>{t('Good to know')}</summary>{m.agent.limitations.map((v,i)=><p key={i}>{v}</p>)}</details>}
        {m.kind==='smart'&&!m.draft&&!m.agent&&<small className="muted">{t('Please double-check important decisions.')}</small>}
        {m.role==='assistant'&&voiceOpen&&'speechSynthesis' in window&&<button className="secondary small" onClick={()=>readAnswer(m.text)}>{t('Read reply aloud')}</button>}
      </article>)}
      {busy&&<p role="status" className="chat-thinking">{t('Thinking')}<span aria-hidden="true">…</span></p>}
    </div>
    {readNotice&&<p className="muted small" role="status">{readNotice}</p>}
    {picking&&<div className="chat-pick"><label htmlFor="agent-bill">{t('Which bill?')}</label><select id="agent-bill" value={target} onChange={e=>setTarget(e.target.value)}><option value="">{t('Choose a bill')}</option>{openBills.map(i=><option key={i.invoice_id} value={i.invoice_id}>{prettyVendor(i.vendor_id)} · {i.invoice_number} · {money(i.amount_minor)} · due {formatDate(i.due_on)}</option>)}</select><div className="actions"><button disabled={!target||busy} onClick={()=>void prepare()}>{t('Prepare for approval')}</button><button className="secondary" onClick={()=>setPicking(false)}>{t('Cancel')}</button></div></div>}
    {owner&&!!openBills.length&&smart&&!picking&&<div className="chat-chips"><button className="secondary small" disabled={busy} onClick={()=>setPicking(true)}>{t('Prepare a payment…')}</button></div>}
    {canAddRecords&&<div className="chat-chips">
      <button type="button" className="secondary" aria-expanded={voiceOpen} onClick={()=>setVoiceOpen(open=>!open)}>
        {voiceOpen?t('Close voice assistant'):t('Talk to Capzoryx')}
      </button>
      {voiceOpen&&'speechSynthesis' in window&&<label className="check small"><input type="checkbox" checked={speakReplies} onChange={e=>{setSpeakReplies(e.target.checked);if(!e.target.checked)window.speechSynthesis.cancel();}}/>{t('Read voice replies aloud automatically')}</label>}
    </div>}
    {voiceOpen&&visible&&canAddRecords&&<Suspense fallback={<p role="status">{t('Opening voice assistant…')}</p>}>
      <VoiceNote request={request} revision={revision} currency={currency} questionBusy={busy}
        onQuestion={text=>void ask(text,'voice')}
        onSaved={async()=>{add({role:'assistant',kind:'device',text:t('I saved the drafts you selected in this workspace.')});await onSaved();}}/>
    </Suspense>}
    <form className="chat-compose" onSubmit={(e:FormEvent)=>{e.preventDefault();void ask(task);}}><label htmlFor="agent-task" className="sr-only">{t('Message the assistant')}</label><textarea ref={input} id="agent-task" rows={2} placeholder={t('Ask, or say what you paid…')} maxLength={800} value={task} disabled={busy} onChange={e=>setTask(e.target.value)} onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey&&!e.nativeEvent.isComposing){e.preventDefault();e.currentTarget.form?.requestSubmit();}}}/><button disabled={busy||!task.trim()} aria-label={t('Send message')}>{busy?'…':<Icon name="send"/>}</button></form>
    {canUseHisab&&<div className="chat-hisab">
      <button className="secondary small" type="button" disabled={busy||task.trim().length<3||task.trim().length>400} aria-describedby="assistant-hisab-privacy" onClick={()=>void ask(task,'hisab')}>{t('Use Hisab-LM')}</button>
      <p id="assistant-hisab-privacy" className="small muted">{task.trim().length>400?t('Use 400 characters or fewer with Hisab-LM.'):hisabPrivacyNote(allowHisabBackup)}</p>
    </div>}
    <p className="chat-footnote">{smart?t('Smart assist is on · simple questions are answered on your device'):t('Answers on your device · turn on smart assist in Settings for more')}</p>
  </section>;
}
