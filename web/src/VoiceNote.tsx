import {useEffect,useRef,useState} from 'react';
import {currencyDigits,money,parseMinor} from './api';
import {slug} from './recordParser';
import {formatDate,todayIso} from './format';
import {Icon} from './Icon';
import {t,T,useI18n} from './i18n';

type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
type EntryType='expense'|'income'|'bill';
type Draft={title?:string;needs_review?:boolean;entry_type:EntryType;amount_minor:number;effective_on:string;memo:string;warnings:string[];evidence:string;party:string;category:string;due_on?:string};
type Split={drafts:Draft[];dropped:{evidence:string;reason:string;count?:number}[];method:'ai'|'rules'};
type Row={id:string;title:string;include:boolean;type:EntryType;amount:string;date:string;note:string;party:string;warnings:string[];evidence:string;due:string};
type Status={speech:boolean;cloud_allowed:boolean;max_seconds:number};
type TranscriptVariant={transcript:string;model:string};
type SpeechResult={transcript:string;truncated?:boolean;alternatives?:TranscriptVariant[];agreement?:'agree'|'review'|'single'};
type Recognition={lang:string;continuous:boolean;interimResults:boolean;start():void;stop():void;onresult:((e:{resultIndex:number;results:ArrayLike<{isFinal:boolean;0:{transcript:string}}>})=>void)|null;onend:(()=>void)|null;onerror:((e:{error:string})=>void)|null};

const TYPE:Record<EntryType,string>={expense:T('Money paid'),income:T('Money received'),bill:T('Bill still to pay')};
const DROPPED:Record<string,string>={
  AMOUNT_NOT_SAID:T('The amount was not clearly said.'),
  AMBIGUOUS_AMOUNT:T('Say each payment and its amount in a separate sentence.'),
  AMOUNT_OUT_OF_RANGE:T('This amount is outside the supported range.'),
  BALANCE_ONLY:T('This amount is a balance mentioned in the story.'),
  CURRENCY_MISMATCH:T('Record this payment in a workspace for its currency.'),
  NOT_IN_NOTE:T('These words do not appear together in the note.'),
  NOT_PAID:T('The note says the payment did not happen.'),
  PLANNED:T('The note describes a future payment.'),
  QUESTION:T('The note asks a question.'),
  REQUESTED_ONLY:T('This amount was requested. Record the amount actually exchanged.'),
  TOO_MANY_ENTRIES:T('Split the note into smaller batches of up to 40 entries.'),
};
// Server warnings, listed so they are translated with the rest of the page.
export const SERVER_WARNINGS=[T('No day was said, so today is used. Change it if needed.'),T('Check the date.'),
  T('Two numbers were said here. Check that this is the right amount.'),T('Found by the backup reader. Check it, or untick it if it is not a payment.'),
  T('The days in your note go backwards. Choose the week above if this was last week.'),T('No payment word was heard. Check whether this money was paid or received.'),T('The type was set from your words. Check it.'),
  T('Check this amount against the recording before selecting the draft.'),T('Part of the amount is unclear. Enter the amount you heard.')];
const MIME=['audio/webm;codecs=opus','audio/ogg;codecs=opus','audio/mp4','audio/webm'];
const shown=(text:string,currency:string)=>{try{return money(parseMinor(text,currency),currency);}catch{return `${text} ${currency}`;}};
const major=(minor:number,currency:string)=>{const d=currencyDigits(currency);return (minor/10**d).toFixed(d);};
const errorText=(e:unknown)=>e instanceof Error?e.message:t('Something went wrong. Please try again.');
const soundsLikeQuestion=(text:string)=>{
  const phrase=text.trim().replace(/[।.!?？]+$/u,'');
  // A conversation tag at the end of a multi-sentence note does not turn the whole note into a question.
  if (/[।!?？\n]|\.(?!\d)/u.test(phrase)) return false;
  return /[?？]\s*$/u.test(text)
    || /^(?:আমার\s+)?(?:কত|কবে|কোন|what\b|how\b|when\b|which\b|show\b)|(?:কত\s+(?:টাকা\s+)?আছে|(?:কত|কোথায়|কেমন|কি|কী|দেখাও|বলো)\s*$)|\b(?:koto|kobe|kon|kemon)\b/iu.test(phrase);
};

/** Monday of this week or of last week, as YYYY-MM-DD in local time. */
function weekStart(which:'this'|'last'){
  const d=new Date();const back=(d.getDay()+6)%7+(which==='last'?7:0);d.setDate(d.getDate()-back);
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;
}
const plusDays=(iso:string,n:number)=>{const d=new Date(iso+'T12:00:00');d.setDate(d.getDate()+n);return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}-${String(d.getDate()).padStart(2,'0')}`;};
const dayName=(iso:string,code:string)=>new Date(iso+'T12:00:00').toLocaleDateString(code==='bn'?'bn-BD':'en-GB',{weekday:'long',day:'numeric',month:'short'});

function toBase64(blob:Blob):Promise<string>{
  return new Promise((resolve,reject)=>{const r=new FileReader();r.onload=()=>resolve(String(r.result).split(',')[1]||'');r.onerror=()=>reject(r.error);r.readAsDataURL(blob);});
}

/** Challenge 1: say a whole week of spending in one note; every entry becomes its own draft to check and save. */
export function VoiceNote({request,revision,currency,onSaved,onQuestion,questionBusy=false}:{request:Request;revision:string;currency:string;onSaved:()=>Promise<void>|void;onQuestion?:(text:string)=>void;questionBusy?:boolean}){
  const {code}=useI18n();
  const [status,setStatus]=useState<Status|null>(null);
  const [statusError,setStatusError]=useState(false);
  const [language,setLanguage]=useState<'bn'|'en'>(currency==='BDT'||code==='bn'?'bn':'en');
  const [stage,setStage]=useState<'idle'|'opening'|'recording'|'listening'|'hearing'|'splitting'|'review'|'saving'|'saved'>('idle');
  const [seconds,setSeconds]=useState(0);
  const [transcript,setTranscript]=useState('');
  const [rows,setRows]=useState<Row[]>([]);
  const [dropped,setDropped]=useState<Split['dropped']>([]);
  const [method,setMethod]=useState<Split['method']|null>(null);
  const [error,setError]=useState('');
  const [result,setResult]=useState('');
  const [interim,setInterim]=useState('');
  const [week,setWeek]=useState<'recent'|'this'|'last'>('recent');
  const [level,setLevel]=useState(0);
  const [clip,setClip]=useState<Blob|null>(null);
  const [clipUrl,setClipUrl]=useState('');
  const [alternate,setAlternate]=useState('');
  const [variants,setVariants]=useState<TranscriptVariant[]>([]);
  const [agreement,setAgreement]=useState<SpeechResult['agreement']>();
  const [truncated,setTruncated]=useState(false);
  const [source,setSource]=useState<'typed'|'speech'>('typed');
  const dictationText=useRef('');
  const wantDictation=useRef(false);
  const sendDictation=useRef(false);
  const recorder=useRef<MediaRecorder|null>(null);
  const live=useRef<Recognition|null>(null);
  const timer=useRef<number|undefined>(undefined);
  const restart=useRef<number|undefined>(undefined);
  const operation=useRef(false);
  const alive=useRef(true);
  const browser=window as unknown as {SpeechRecognition?:new()=>Recognition;webkitSpeechRecognition?:new()=>Recognition};
  const Dictation=browser.SpeechRecognition||browser.webkitSpeechRecognition;
  const maxSeconds=status?.max_seconds||180;
  const canRecord=!!status?.speech&&!!status.cloud_allowed&&typeof MediaRecorder!=='undefined'&&!!navigator.mediaDevices?.getUserMedia;

  async function checkStatus(){
    setStatusError(false);
    try{
      const available=await request<Status>('/assist/voice-note');
      if(alive.current)setStatus(available);
    }catch{if(alive.current)setStatusError(true);}
  }

  useEffect(()=>{
    alive.current=true;
    void checkStatus();
    return ()=>{
      alive.current=false;wantDictation.current=false;
      clearInterval(timer.current);clearTimeout(restart.current);
      const rec=recorder.current;
      if(rec){rec.onstop=null;rec.stream.getTracks().forEach(track=>track.stop());}
      const recognition=live.current;
      if(recognition){recognition.onend=null;try{recognition.stop();}catch{/* Already disconnected. */}}
    };
  },[]);// eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{
    if(!clip){setClipUrl('');return;}
    const url=URL.createObjectURL(clip);
    setClipUrl(url);
    return ()=>URL.revokeObjectURL(url);
  },[clip]);

  function changeTranscript(value:string){
    setTranscript(value);
    setRows([]);
    setDropped([]);
    setMethod(null);
    setResult('');
    setError('');
    setStage('idle');
  }

  async function record(){
    if(operation.current)return;
    if(onQuestion)window.speechSynthesis?.cancel();
    operation.current=true;
    setError('');setResult('');setStage('opening');
    let stream:MediaStream;
    try{stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1,echoCancellation:true,noiseSuppression:true,autoGainControl:true}});}
    catch{operation.current=false;if(alive.current){setStage('idle');setError(t('Microphone permission is off. Allow the microphone, or type the note.'));}return;}
    if(!alive.current){stream.getTracks().forEach(track=>track.stop());return;}
    const mimeType=MIME.find(m=>MediaRecorder.isTypeSupported(m))||'';
    let rec:MediaRecorder;
    try{rec=new MediaRecorder(stream,{...(mimeType?{mimeType}:{}),audioBitsPerSecond:32000});}
    catch{
      stream.getTracks().forEach(track=>track.stop());
      operation.current=false;
      setStage('idle');
      setError(t('You can also type the note, or use live dictation.'));
      return;
    }
    const chunks:Blob[]=[];
    let interrupted=false;
    rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    rec.onstop=()=>{
      operation.current=false;
      stream.getTracks().forEach(x=>x.stop());clearInterval(timer.current);
      if(!alive.current)return;
      const blob=new Blob(chunks,{type:rec.mimeType||mimeType||'audio/webm'});
      setClip(blob);
      setAlternate('');
      setVariants([]);
      setAgreement(undefined);
      if(interrupted){
        setStage('idle');setError(t('The microphone disconnected. Play or transcribe the part that was recorded.'));
        return;
      }
      void hear(blob,onQuestion?'compare':'primary');
    };
    rec.onerror=()=>{
      interrupted=true;
    };
    stream.getAudioTracks?.().forEach(track=>track.addEventListener('ended',()=>{interrupted=true;},{once:true}));
    // A live sound-level bar shows the phone is hearing the owner the whole time.
    try{
      const audio=new AudioContext();const source=audio.createMediaStreamSource(stream);const meter=audio.createAnalyser();
      meter.fftSize=512;source.connect(meter);const buf=new Uint8Array(meter.fftSize);
      const tick=()=>{if(rec.state!=='recording'){void audio.close().catch(()=>{});setLevel(0);return;}
        meter.getByteTimeDomainData(buf);let peak=0;for(const v of buf)peak=Math.max(peak,Math.abs(v-128));
        setLevel(Math.min(1,peak/64));requestAnimationFrame(tick);};
      requestAnimationFrame(tick);
    }catch{/* the meter is optional */}
    recorder.current=rec;
    try{rec.start(1000);}catch{
      operation.current=false;
      stream.getTracks().forEach(track=>track.stop());
      setStage('idle');setError(t('The recording could not start. You can use live dictation or type the note.'));
      return;
    }
    setSeconds(0);setStage('recording');
    const started=Date.now();
    timer.current=window.setInterval(()=>{const s=Math.floor((Date.now()-started)/1000);setSeconds(s);if(s>=maxSeconds&&rec.state==='recording')rec.stop();},250);
  }
  async function hear(blob:Blob,mode:'primary'|'alternate'|'compare'){
    if(operation.current)return;
    if(blob.size<1000){setStage('idle');setError(t('The recording was too short. Hold the button a little longer.'));return;}
    operation.current=true;
    setError('');setStage('hearing');
    try{
      const mime=(blob.type||'audio/webm').toLowerCase().replace(/\s+/g,'');
      const data=await request<SpeechResult>('/assist/voice-note',{audio_b64:await toBase64(blob),mime,language,consent:true,mode});
      if(!alive.current)return;
      setTruncated(!!data.truncated);
      if(mode==='alternate'&&transcript.trim()){
        setAlternate(data.transcript);setStage('idle');
      }else{
        setSource('speech');
        changeTranscript(data.transcript);
        setVariants(data.alternatives??[]);
        setAgreement(data.agreement);
        if(onQuestion&&!data.truncated){
          operation.current=false;
          await handleVoiceTurn(data.transcript);
        }
      }
    }catch(e){if(alive.current){setStage('idle');setError(errorText(e)+' '+t('The recording is still available to replay or retry.'));}}
    finally{operation.current=false;}
  }
  function dictate(){
    if(!Dictation||operation.current)return;
    if(onQuestion)window.speechSynthesis?.cancel();
    operation.current=true;
    setError('');setRows([]);setDropped([]);setResult('');wantDictation.current=true;sendDictation.current=false;
    dictationText.current=onQuestion?'':transcript.trim();
    if(onQuestion){
      setClip(null);setTranscript('');setAlternate('');setVariants([]);setAgreement(undefined);setTruncated(false);
    }
    let failures=0;
    const run=()=>{
      const engine=new Dictation();engine.lang=language==='bn'?'bn-BD':'en-US';engine.continuous=true;engine.interimResults=true;
      engine.onresult=e=>{let said='',draft='';for(let i=e.resultIndex;i<e.results.length;i++){const x=e.results[i][0].transcript;if(e.results[i].isFinal)said+=x+' ';else draft+=x;}
        if(said.trim()){
          failures=0;
          setSource('speech');
          const copy=(dictationText.current?dictationText.current+'\n':'')+said.trim();
          if(copy.length>4000)setTruncated(true);
          dictationText.current=copy.slice(0,4000);
          setTranscript(dictationText.current);
        }
        setInterim(draft);};
      engine.onerror=e=>{
        if(!wantDictation.current||e.error==='no-speech')return;
        if(['not-allowed','service-not-allowed','audio-capture','language-not-supported'].includes(e.error)){
          wantDictation.current=false;
          if(alive.current)setError(t('Live dictation is unavailable. You can type or paste the note.'));
        }else failures++;
      };
      engine.onend=()=>{
        if(!alive.current||live.current!==engine)return;
        setInterim('');
        live.current=null;
        if(wantDictation.current&&failures<5){
          restart.current=window.setTimeout(resume,220*(1+failures));
        }else if(wantDictation.current){
          stopDictation();
          setError(t('Dictation disconnected. The words already heard are still in the transcript.'));
        }else finishDictation();
      };
      live.current=engine;engine.start();
    };
    function resume(){
      if(!wantDictation.current||!alive.current)return;
      try{run();}catch{
        if(++failures<5)restart.current=window.setTimeout(resume,220*(1+failures));
        else{
          stopDictation();
          setError(t('Dictation disconnected. The words already heard are still in the transcript.'));
        }
      }
    }
    try{run();}catch{
      wantDictation.current=false;
      operation.current=false;
      setError(t('Live dictation is unavailable. You can type or paste the note.'));
      return;
    }
    setStage('listening');
    const started=Date.now();setSeconds(0);
    timer.current=window.setInterval(()=>{const sec=Math.floor((Date.now()-started)/1000);setSeconds(sec);if(sec>=maxSeconds)stopDictation(true);},500);
  }
  function finishDictation(){
    operation.current=false;
    clearInterval(timer.current);clearTimeout(restart.current);
    setInterim('');setStage(s=>s==='listening'?'idle':s);
    if(onQuestion&&sendDictation.current&&dictationText.current.length>=3&&dictationText.current.length<4000){
      sendDictation.current=false;
      void handleVoiceTurn(dictationText.current);
    }
  }
  function stopDictation(submit=false){
    sendDictation.current=submit;
    wantDictation.current=false;
    clearTimeout(restart.current);
    try{
      if(live.current){
        restart.current=window.setTimeout(finishDictation,1200);
        live.current.stop();return;
      }
    }catch{/* The browser may have disconnected. */}
    finishDictation();
  }
  async function handleVoiceTurn(text:string){
    if(onQuestion&&soundsLikeQuestion(text)){
      if(!questionBusy)onQuestion(text);
      return;
    }
    await split(false,text,'speech');
  }
  async function split(useOnline=false,text=transcript,origin=source){
    if(operation.current||text.trim().length<3)return;
    operation.current=true;
    setError('');setResult('');setStage('splitting');
    try{
      const data=await request<Split>('/assist/batch-drafts',{transcript:text.trim(),consent:useOnline,source:origin,...(week!=='recent'?{week_start:weekStart(week)}:{})});
      if(!alive.current)return;
      setRows(data.drafts.map(d=>({id:crypto.randomUUID(),title:d.title||d.category||'',include:!d.needs_review,type:d.entry_type,amount:d.amount_minor?major(d.amount_minor,currency):'',date:d.effective_on,
        note:d.memo,party:d.party,warnings:d.warnings,evidence:d.evidence,due:d.due_on||d.effective_on})));
      setDropped(data.dropped);setMethod(data.method);
      setStage(data.drafts.length?'review':'idle');
      if(!data.drafts.length){
        setError(data.dropped.some(item=>item.reason==='BALANCE_ONLY')
          ?t('You mentioned cash on hand. To add a record, say what was received or paid and when.')
          :t('No payment draft was made. Review the transcript or describe each payment with its amount.'));
      }
    }catch(e){if(alive.current){setStage('idle');setError(errorText(e)+' '+t('Your transcript is still here. Try making drafts again.'));}}
    finally{operation.current=false;}
  }
  const update=(id:string,patch:Partial<Row>)=>setRows(cur=>cur.map(r=>r.id===id?{...r,...patch}:r));
  const chosen=rows.filter(r=>r.include);
  const total=(type:EntryType)=>chosen.filter(r=>r.type===type).reduce((sum,r)=>{try{return sum+parseMinor(r.amount,currency);}catch{return sum;}},0);
  async function save(){
    if(operation.current)return;
    setError('');
    const today=todayIso();
    let records;
    try{
      records=chosen.map((r,i)=>{
        const minor=parseMinor(r.amount,currency);
        if(minor<=0)throw new Error(t('Entry {n}: the amount must be more than zero.',{n:i+1}));
        if(r.type==='bill'){
          const vendor=slug(r.party)||'voice-supplier';
          if(r.date>today)throw new Error(t('Entry {n}: a bill date cannot be in the future.',{n:i+1}));
          return {request_id:r.id,kind:'invoice',amount_minor:minor,effective_on:r.date,vendor_id:vendor,invoice_number:`VOICE-${r.id}`,due_on:r.due<r.date?r.date:r.due,memo:r.note.slice(0,2000)};
        }
        return {request_id:r.id,kind:'cash',amount_minor:minor,effective_on:r.date,direction:r.type==='income'?'inflow':'outflow',status:r.date>today?'scheduled':'cleared',memo:r.note.slice(0,2000)};
      });
    }catch(e){setError(errorText(e));return;}
    operation.current=true;setStage('saving');
    try{
      const done=await request<{saved:number;replayed?:boolean}>('/workspace/records/batch',{expected_revision:revision,records});
      if(!alive.current)return;
      // Keep controls busy until the new workspace revision is available for the next turn.
      try{await onSaved();}catch{if(alive.current)setError(t('Records were saved. Refresh the workspace if its balance has not updated.'));}
      if(!alive.current)return;
      setResult(t('{count} records added. Your balance, forecast and activity are updated.',{count:done.replayed?records.length:done.saved}));
      setStage('saved');setRows([]);setTranscript('');setDropped([]);setClip(null);setAlternate('');setTruncated(false);setSource('typed');setVariants([]);setAgreement(undefined);
    }catch(e){if(alive.current){setStage('review');setError(errorText(e));}}
    finally{operation.current=false;}
  }

  const busy=['opening','recording','listening','hearing','splitting','saving'].includes(stage);
  return <section className="panel voice-note" aria-labelledby="voice-note-title">
    <p className="eyebrow">{t('Voice note')}</p>
    <h2 id="voice-note-title">{onQuestion?t('Talk to your workspace'):t('Say a whole week of spending at once')}</h2>
    {onQuestion?<p className="muted">{t('Ask about your cash or say what you paid or received. Record a turn, then review the answer or drafts.')}</p>:<p className="muted">{t('Talk naturally, for example: “Shombar dokan bhara 5000 dilam, tarpor transport 300. Budhbar Rahim theke 12 hajar pelam.” Each payment becomes its own draft. You check them, then save them all together.')}</p>}
    <div className="voice-controls">
      <label className="voice-lang">{t('I will speak')}<select value={language} disabled={busy} onChange={e=>setLanguage(e.target.value as 'bn'|'en')}><option value="bn">{t('Bangla or Banglish')}</option><option value="en">English</option></select></label>
      {stage==='recording'
        ?<button className="voice-record recording" onClick={()=>{if(recorder.current?.state==='recording')recorder.current.stop();}}><span className="rec-dot" aria-hidden="true"/>{t('Stop ({time})',{time:`${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`})}</button>
        :stage==='listening'
        ?<button className="voice-record recording" onClick={()=>stopDictation(true)}><span className="rec-dot" aria-hidden="true"/>{t('Stop dictation')}</button>
        :<>{canRecord&&<button className="voice-record" disabled={busy||questionBusy} onClick={()=>void record()}><Icon name="spark"/>{stage==='hearing'?t('Transcribing your recording…'):t('Record a voice note')}</button>}
          {Dictation&&<button className="secondary" disabled={busy||questionBusy} onClick={dictate}><Icon name="spark"/>{t('Start live dictation')}</button>}</>}
    </div>
    {stage==='recording'&&<div className="voice-level" aria-hidden="true"><span style={{width:`${Math.round(8+level*92)}%`}}/></div>}
    {stage==='listening'&&<p className="small muted" role="status">{t('Listening until you press Stop. Pauses are okay.')}</p>}
    {stage==='listening'&&interim&&<p className="voice-interim" aria-live="polite">{interim}…</p>}
    {stage==='recording'&&<p className="small muted" role="status">{t('Up to {minutes} minutes. Say the day, what it was for and the amount for each payment.',{minutes:Math.round(maxSeconds/60)})}</p>}
    {status===null&&!statusError&&<p className="small muted" role="status">{t('Checking recording availability…')}</p>}
    {statusError&&<button className="secondary" type="button" onClick={()=>void checkStatus()}>{t('Check microphone access again')}</button>}
    {status&&!status.cloud_allowed&&<p className="small muted">{t('Enable Smart assist in Settings to send recordings for transcription. You can also use live dictation or type the note.')}</p>}
    {status?.cloud_allowed&&!status.speech&&<p className="small muted">{t('Speech services are unavailable. Use live dictation or type the note.')}</p>}
    {canRecord&&<p className="small muted">{t('The recording is sent to the speech service to be written down and is not stored by Capzoryx.')}</p>}
    {clipUrl&&<div className="voice-clip">
      <audio controls preload="metadata" src={clipUrl} aria-label={t('Replay your recording')}/>
      {canRecord&&clip&&onQuestion&&!variants.length&&!agreement&&<button className="secondary" disabled={busy} onClick={()=>void hear(clip,'compare')}>{t('Retry model comparison')}</button>}
      {canRecord&&clip&&<button className="secondary" disabled={busy} onClick={()=>void hear(clip,'alternate')}>{t('Try another transcription')}</button>}
    </div>}
    {agreement==='agree'&&<p className="notice small">{t('The models matched on the transaction amounts and dates. Verify each draft before saving.')}</p>}
    {agreement==='review'&&!soundsLikeQuestion(transcript)&&<p className="notice">{t('The models gave different results or could not verify an amount. Compare the recording and transcriptions before saving.')}</p>}
    {agreement==='single'&&onQuestion&&<p className="muted small">{t('One speech model responded. Check its transcript against the recording.')}</p>}
    {variants.map(variant=><details key={variant.model} className="voice-alternate">
      <summary>{t('Compare transcript from {model}',{model:variant.model})}</summary>
      <p>{variant.transcript}</p>
      <button className="secondary" disabled={busy} onClick={()=>{setSource('speech');changeTranscript(variant.transcript);void handleVoiceTurn(variant.transcript);}}>{t('Use this transcription')}</button>
    </details>)}
    {alternate&&<div className="voice-alternate">
      <label htmlFor="alternate-transcript">{t('Alternative transcription')}</label>
      <textarea id="alternate-transcript" rows={4} value={alternate} dir="auto" readOnly/>
      <button className="secondary" disabled={busy} onClick={()=>{setSource('speech');changeTranscript(alternate);setAlternate('');}}>{t('Use this transcription')}</button>
    </div>}
    {truncated&&<p className="notice" role="status">{t('The transcript reached the text limit. Review the text and record the rest as a separate note.')}</p>}
    <label htmlFor="voice-transcript">{t('What you said (you can correct it)')}</label>
    <textarea id="voice-transcript" dir="auto" rows={6} maxLength={4000} value={transcript} disabled={busy} onChange={e=>changeTranscript(e.target.value)} placeholder={t('Your words appear here. You can also type or paste a whole week of spending.')}/>
    <label className="voice-week">{t('Days like “Shombar” in my note mean')}<select value={week} disabled={busy} onChange={e=>{setWeek(e.target.value as typeof week);changeTranscript(transcript);}}>
      <option value="recent">{t('The last 7 days')}</option><option value="this">{t('This week (from {date})',{date:formatDate(weekStart('this'))})}</option>
      <option value="last">{t('Last week ({from} to {to})',{from:formatDate(weekStart('last')),to:formatDate(plusDays(weekStart('last'),6))})}</option></select></label>
    <div className="actions"><button disabled={busy||transcript.trim().length<3} onClick={()=>void split()}>{stage==='splitting'?t('Making drafts…'):t('Make drafts')}</button>
      {onQuestion&&<button className="secondary" disabled={busy||questionBusy||!transcript.trim()} onClick={()=>onQuestion(transcript.trim())}>{t('Ask the workspace assistant')}</button>}
      {status?.cloud_allowed&&method==='rules'&&(dropped.some(item=>item.reason==='AMBIGUOUS_AMOUNT')||rows.length===0)&&<button className="secondary" disabled={busy} onClick={()=>void split(true)}>{t('Try online help for these drafts')}</button>}
      {(transcript||clip)&&!busy&&<button className="secondary" onClick={()=>{changeTranscript('');setClip(null);setAlternate('');setTruncated(false);setSource('typed');setVariants([]);setAgreement(undefined);}}>{t('Clear')}</button>}</div>
    {error&&<p className="error" role="alert">{error}</p>}
    {stage==='review'&&<p className="notice small" role="status">{t('{count} drafts ready for review below.',{count:rows.length})}</p>}
    {result&&<p className="success" role="status">{result}</p>}
    {dropped.length>0&&<details className="batch-dropped" open={rows.length===0}>
      <summary>{t('{count} parts were not turned into drafts',{count:dropped.reduce((sum,d)=>sum+(d.count||1),0)})}</summary>
      <ul>{dropped.map((d,i)=><li key={i}>{d.evidence&&<>“{d.evidence}” · </>}{t(DROPPED[d.reason]||T('Check the note and try again.'))}</li>)}</ul>
    </details>}

    {(stage==='review'||stage==='saving')&&<div className="batch-review">
      <h3>{t('{count} drafts from your note',{count:rows.length})}</h3>
      {method==='rules'&&<p className="notice small">{t('Read without AI. Check each draft carefully.')}</p>}
      {source==='speech'&&<p className="notice small">{t('Listen to the recording or check the transcript. Select each verified draft to save it.')}</p>}
      <div className="voice-summary" aria-label={t('Summary of your note')}>
        <div className="voice-totals"><div><small>{t('Money paid')}</small><b className="negative">{money(total('expense'),currency)}</b></div>
          <div><small>{t('Money received')}</small><b className="positive">{money(total('income'),currency)}</b></div>
          <div><small>{t('Net')}</small><b>{money(total('income')-total('expense'),currency)}</b></div></div>
        {[...new Set(chosen.map(r=>r.date))].sort().map(day=><div className="voice-day" key={day}><h4>{dayName(day,code)}</h4>
          <ul>{chosen.filter(r=>r.date===day).map(r=><li key={r.id} className={'kind-'+r.type}><span className="voice-sign" aria-hidden="true">{r.type==='income'?'+':r.type==='bill'?'⧗':'−'}</span>
            <span className="voice-title">{r.title||r.note}{r.type==='bill'&&<small>{t('Bill still to pay')}</small>}</span><b>{shown(r.amount,currency)}</b></li>)}</ul></div>)}
        {rows.length>chosen.length&&<p className="small muted">{t('{count} unticked entries are not included. Tick them below if they are payments.',{count:rows.length-chosen.length})}</p>}
      </div>
      <h4 className="voice-check-title">{t('Check each entry against what you said')}</h4>
      <ol className="batch-list">{rows.map((r,i)=><li key={r.id} className={r.include?undefined:'excluded'}>
        <div className="batch-head"><label className="check"><input type="checkbox" checked={r.include} onChange={e=>update(r.id,{include:e.target.checked})}/><b>{t('Entry {n}',{n:i+1})}{r.title?' · '+r.title:''}</b></label>
          {r.warnings.length>0&&<span className="badge warn">{t('Check')}</span>}</div>
        {r.evidence&&<p className="batch-quote">“{r.evidence}”</p>}
        <div className="batch-fields">
          <label>{t('Type')}<select value={r.type} onChange={e=>update(r.id,{type:e.target.value as EntryType})}>{(Object.keys(TYPE) as EntryType[]).map(k=><option key={k} value={k}>{t(TYPE[k])}</option>)}</select></label>
          <label>{t('Amount ({currency})',{currency})}<input inputMode="decimal" value={r.amount} onChange={e=>update(r.id,{amount:e.target.value})}/></label>
          <label>{t('Date')}<input type="date" value={r.date} max={r.type==='bill'?todayIso():undefined} onChange={e=>update(r.id,{date:e.target.value})}/></label>
          {r.type==='bill'&&<label>{t('Due date')}<input type="date" value={r.due} min={r.date} onChange={e=>update(r.id,{due:e.target.value})}/></label>}
          {r.type==='bill'&&<label>{t('Supplier')}<input value={r.party} maxLength={60} onChange={e=>update(r.id,{party:e.target.value})}/></label>}
          <label className="batch-note">{t('Note')}<input value={r.note} maxLength={200} onChange={e=>update(r.id,{note:e.target.value})}/></label>
        </div>
        {r.warnings.map((w,j)=><p className="notice small" key={j}>{t(w)}</p>)}
      </li>)}</ol>
      <p className="batch-total"><span>{t('Paid {amount}',{amount:major(total('expense'),currency)+' '+currency})}</span><span>{t('Received {amount}',{amount:major(total('income'),currency)+' '+currency})}</span>{total('bill')>0&&<span>{t('Bills {amount}',{amount:major(total('bill'),currency)+' '+currency})}</span>}</p>
      <div className="actions"><button disabled={stage==='saving'||!chosen.length} onClick={()=>void save()}>{stage==='saving'?t('Saving…'):t('Save {count} records',{count:chosen.length})}</button>
        <button className="secondary" disabled={stage==='saving'} onClick={()=>{setRows([]);setStage('idle');}}>{t('Discard all')}</button></div>
      <p className="small muted">{t('Saved together: either all chosen records are added, or none are.')} {chosen.length>0&&t('Dates from {from} to {to}.',{from:formatDate(chosen.map(r=>r.date).sort()[0]),to:formatDate(chosen.map(r=>r.date).sort().at(-1)!)})}</p>
    </div>}
  </section>;
}
