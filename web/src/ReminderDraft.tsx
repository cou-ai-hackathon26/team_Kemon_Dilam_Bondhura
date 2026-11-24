import {useRef,useState} from 'react';
import {ApiError} from './api';
import {t,useI18n} from './i18n';

type Request=<T,>(path:string,body?:unknown)=>Promise<T>;

export function ReminderDraft({request,id,initial}:{request:Request;id:string;initial?:{language:string;message:string}}){
  const {code}=useI18n();
  const [language,setLanguage]=useState(initial?.language||(code==='bn'?'bn':'en'));
  const [draft,setDraft]=useState(initial?.message||'');
  const [busy,setBusy]=useState(false);
  const [notice,setNotice]=useState('');
  const attempt=useRef('');
  const [approvedUntil,setApprovedUntil]=useState(0);
  async function prepare(){
    attempt.current='';setApprovedUntil(0);
    setBusy(true);setNotice('');setDraft('');
    try{
      const result=await request<{message:string}>('/copilot/message',{receivable_id:id,language});
      setDraft(result.message);
    }catch(e){setNotice(e instanceof Error?e.message:t('Request failed.'));}
    finally{setBusy(false);}
  }
  async function approve(){
    if(!draft||busy)return;
    setBusy(true);setNotice('');
    try{
      attempt.current ||= crypto.randomUUID();
      await request('/copilot/message/approve',{receivable_id:id,language,request_id:attempt.current,reviewed_message:draft});
      setApprovedUntil(Date.now()+60000);
    }catch(e){
      if(e instanceof ApiError && ['REMINDER_CHANGED','RECEIVABLE_PAID'].includes(e.code)){
        setDraft('');setNotice(t('The balance changed. Prepare a fresh reminder.'));
      }else setNotice(e instanceof ApiError && e.code==='CUSTOMER_REMINDER_ALREADY_APPROVED_TODAY'
        ?t('A reminder was already approved for this customer today.'):e instanceof Error?e.message:t('Request failed.'));
    }finally{setBusy(false);}
  }
  async function share(){
    if(Date.now()>=approvedUntil){setApprovedUntil(0);setNotice(t('Review the reminder again before sharing.'));return;}
    try{
      if(navigator.share)await navigator.share({text:draft});
      else{await navigator.clipboard.writeText(draft);setNotice(t('Reminder copied.'));}
      setDraft('');setApprovedUntil(0);attempt.current='';
    }catch(e){if(!(e instanceof Error&&e.name==='AbortError'))setNotice(t('Select the reminder text and copy it.'));}
  }
  return <details open={initial?true:undefined}><summary>{t('Draft a reminder')}</summary>
    <label>{t('Reminder language')}<select value={language} disabled={busy} onChange={e=>{setLanguage(e.target.value);setDraft('');setApprovedUntil(0);attempt.current='';setNotice('');}}>
      <option value="bn">বাংলা</option><option value="en">English</option>
    </select></label>
    <button type="button" className="secondary" disabled={busy} onClick={()=>void prepare()}>{t('Prepare reminder')}</button>
    {draft&&<><label>{t('Review your reminder')}<textarea readOnly value={draft}/></label>
      {!approvedUntil?<button type="button" disabled={busy} onClick={()=>void approve()}>{t('Approve this reminder for sharing')}</button>:<button type="button" onClick={()=>void share()}>{typeof navigator.share==='function'?t('Share reminder…'):t('Copy reminder')}</button>}</>}
    {notice&&<p role="status">{notice}</p>}
  </details>;
}
