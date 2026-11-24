import {VoiceCapture} from './VoiceCapture';
import {LocalVoice} from './LocalVoice';
import {Icon} from './Icon';
import {useRef,useState} from 'react';
import {useCurrency} from './Currency';
import {DraftCard} from './DraftCard';
import {prepareDocumentPhoto} from './format';
import {looksLikeOpenBill,parseRecordForDraft} from './recordParser';
import {hisabDraftMessage,hisabPrivacyNote,type HisabDraftResult} from './hisabDraft';
import type {Draft,Profile} from './types';
import {t,useI18n} from './i18n';

type Props={request:<T>(path:string,body?:unknown)=>Promise<T>;revision:string;onSaved:()=>Promise<void>|void;profile:Profile|null;owner:boolean;cloudAvailable:boolean;hisabAvailable:boolean};
type OnlineRead={sentence:string;source:'hisab'|'api'};

/** The fast way to add records: photograph a bill or receipt, or just type or say what happened. */
export function QuickAdd({request,revision,onSaved,profile,owner,cloudAvailable,hisabAvailable}:Props){
  const {currency,digits}=useCurrency();
  const {code}=useI18n();
  const [photoText,setPhotoText]=useState('');
  const [hisabDraft,setHisabDraft]=useState<HisabDraftResult['source']|null>(null);
  const [text,setText]=useState('');const [busy,setBusy]=useState<''|'photo'|'text'>('');const [error,setError]=useState('');
  const [onlineRead,setOnlineRead]=useState<OnlineRead|null>(null);
  const [draft,setDraft]=useState<Draft|null>(null);const [preview,setPreview]=useState('');
  const [photoOnline,setPhotoOnline]=useState(false);  // offer to read this photo with a free online vision model
  const file=useRef<HTMLInputElement>(null);const gallery=useRef<HTMLInputElement>(null);
  const smart=owner&&!!profile?.smart_assist&&cloudAvailable;
  const allowHisabBackup=smart&&!!profile?.record_api_fallback;
  const canUseHisab=owner&&hisabAvailable&&currency==='BDT';
  const canUseRecordApi=allowHisabBackup||smart&&!!profile?.cloud_providers?.some(p=>p.allowed.includes('record_data'));
  const sentence=text.trim();
  const online=onlineRead?.sentence===sentence&&
    (onlineRead.source==='hisab'?canUseHisab:canUseRecordApi)?onlineRead.source:null;
  function updateText(value:string){setText(value);setOnlineRead(null);}
  async function scan(f:File|undefined){
    if(!f)return;setPhotoOnline(false);setOnlineRead(null);setHisabDraft(null);setError('');setDraft(null);setPhotoText('');setPreview('');setBusy('photo');
    try{
      const image=await prepareDocumentPhoto(f);setPreview(image);
      const {readPhotoText,draftFromBill}=await import('./billPhoto');
      const recognized=await readPhotoText(image,code);
      setPhotoText(recognized.trim().slice(0,3000));
      const result=draftFromBill(recognized,currency);
      if(result)setDraft(result);
      else if(owner&&profile?.smart_assist)setPhotoOnline(true);
      else setError(t('No unambiguous total in the workspace currency was found. Check the photo and enter the record manually.'));
    }catch(e){setError(e instanceof Error?e.message:t('The photo could not be read.'));}
    finally{setBusy('');if(file.current)file.current.value='';if(gallery.current)gallery.current.value='';}
  }
  async function readOnline(){
    if(!preview)return;setBusy('photo');setError('');
    try{
      const r=await request<{draft:Draft}>('/assist/scan',{image:preview,free_vision_consent:true});
      if(!r.draft.ok)throw new Error(t('The photo could not be read. A sharper, well-lit photo of the whole bill works best.'));
      setDraft(r.draft);setPhotoOnline(false);
    }catch(e){setError(e instanceof Error?e.message:t('The photo could not be read.'));}
    finally{setBusy('');}
  }
  async function read(){
    if(!sentence||busy)return;setHisabDraft(null);setError('');setDraft(null);setPhotoText('');setPreview('');setBusy('text');
    try{
      if(online==='hisab'){
        const result=await request<HisabDraftResult>('/assist/hisab',{text:sentence,consent:true,...(allowHisabBackup?{allow_api_fallback:true}:{})});
        setDraft(result.draft);setHisabDraft(result.source??'hisab_lm');setOnlineRead(null);return;
      }
      if(online==='api'){
        const r=await request<{draft:Draft}>('/assist/extract',{text:sentence});
        if(!r.draft.ok)throw new Error(t('I could not find an amount or a payment in that. Try something like "I paid Rahman 5000 for freight yesterday".'));
        setDraft(r.draft);setOnlineRead(null);return;
      }
      setOnlineRead(null);
      const local=await parseRecordForDraft(sentence,{currency,digits});
      if(local){setDraft(local);return;}
      if(sentence.length>=3&&sentence.length<=400&&(canUseHisab||canUseRecordApi)){
        const billUsesApi=canUseRecordApi&&looksLikeOpenBill(sentence);
        setOnlineRead({sentence,source:canUseHisab&&!billUsesApi?'hisab':'api'});return;
      }
      throw new Error(t('I could not understand that. Try something like "I paid Rahman 5000 for freight yesterday".'));
    }catch(e){setError(e instanceof Error?e.message:t('Could not read that.'));}
    finally{setBusy('');}
  }
  return <section className="panel quick-add" aria-labelledby="quick-add-title">
    <div className="panel-heading"><div><p className="eyebrow">{t('The fast way')}</p><h2 id="quick-add-title">{t('Add by photo or by typing')}</h2></div></div>
    <div className="quick-grid">
      <div className="quick-box">
        <h3><Icon name="camera"/> {t('Photograph a bill or receipt')}</h3>
        <p className="small muted">{t('Read the total on this device. English and Bangla language files download on first use.')}</p>
        <input ref={file} id="document-photo" type="file" accept="image/*" capture="environment" hidden onChange={e=>void scan(e.target.files?.[0])}/>
        <input ref={gallery} id="document-gallery" type="file" accept="image/*" hidden onChange={e=>void scan(e.target.files?.[0])}/>
        <div className="actions"><button id="capture-photo" disabled={busy!==''} onClick={()=>file.current?.click()}>{busy==='photo'?t('Reading your photo…'):t('Take a photo')}</button><button className="secondary" disabled={busy!==''} onClick={()=>gallery.current?.click()}>{t('Choose from gallery')}</button></div>
        {photoText&&<details><summary>{t('Text read from the photo')}</summary><p>{photoText}</p></details>}
        {preview&&busy==='photo'&&<img className="scan-preview" src={preview} alt={t('Your photo being read')}/>}
        {photoOnline&&!draft&&busy===''&&<div className="notice small" role="status"><p>{t('This phone could not find the total. Read the photo with a free online AI instead? The photo is sent to Groq or Google, which may keep it; nothing is saved until you check it.')}</p>
          <div className="actions"><button onClick={()=>void readOnline()}>{t('Read online')}</button><button className="secondary" onClick={()=>{setPhotoOnline(false);setError(t('Enter the record by typing it below.'));}}>{t('No, I will type it')}</button></div></div>}
      </div>
      <div className="quick-box">
        <h3><Icon name="pen"/> {t('Type it like a message')}</h3>
        <label htmlFor="quick-text" className="sr-only">{t('Describe the payment or bill')}</label>
        <textarea id="quick-text" rows={3} maxLength={400} value={text} disabled={busy!==''} onChange={e=>updateText(e.target.value)} placeholder={'e.g. "I paid Rahman 5000 for freight yesterday"\n"Received 20,000 from Karim Traders"\n"Bill from Dhaka Packaging 12000 due in 10 days"'}/>
        <LocalVoice onText={updateText}/>
        {owner&&profile?.smart_assist&&profile.speech_provider&&<VoiceCapture provider={profile.speech_provider} request={request} onText={updateText}/> }
        <div className="actions">
          <button type="button" disabled={busy!==''||!sentence} aria-describedby={online?'quick-read-privacy':undefined} onClick={()=>void read()}>{busy==='text'?t('Reading…'):online?t('Read online'):t('Read it')}</button>
        </div>
        {online&&<p id="quick-read-privacy" role="status" className="small muted">{online==='hisab'?hisabPrivacyNote(allowHisabBackup):t('Reading online sends this sentence to your configured AI provider to prepare a draft.')}</p>}
      </div>
    </div>
    {error&&<p className="error" role="alert">{error}</p>}
    {draft&&hisabDraft&&<p className="notice small" role="status">{hisabDraftMessage(hisabDraft)}</p>}
    {draft&&<div className={preview?'scan-compare has-photo':'scan-compare'}>{preview&&<figure className="scan-photo"><img src={preview} alt={t('Your photo')}/><figcaption className="small muted">{t('Compare with your photo before adding')}</figcaption></figure>}<DraftCard canTrustVendor={owner} key={JSON.stringify(draft)} draft={draft} revision={revision} request={request} onDiscard={()=>{setDraft(null);setPreview('');}} onSaved={async()=>{await onSaved();setText('');setPreview('');}}/></div>}
  </section>;
}
