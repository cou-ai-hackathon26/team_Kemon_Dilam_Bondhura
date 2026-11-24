import {useEffect, useRef, useState} from 'react';
import {t, useI18n} from './i18n';
import {useCurrency} from './Currency';
import {normalizeSpokenAmount} from './spokenAmount';
import type {Pack} from './localSpeechPack';

type Options = {langs:string[];processLocally:true};
type Recognition = {
  processLocally:boolean;lang:string;continuous:boolean;interimResults:boolean;
  start():void;stop():void;abort():void;
  onresult:((e:{results:{[i:number]:{[i:number]:{transcript:string}}}})=>void)|null;
  onerror:(()=>void)|null;onend:(()=>void)|null;
};
type Speech = {new():Recognition;available?:(options:Options)=>Promise<string>;install?:(options:Options)=>Promise<boolean>};
type Stage = 'idle'|'checking'|'native-install'|'pack-consent'|'pack-loading'|'ready'|'recording'|'transcribing';

export function LocalVoice({onText}:{onText:(text:string)=>void}){
  const {code}=useI18n();
  const {currency}=useCurrency();
  const [stage,setStage]=useState<Stage>('idle');
  const [notice,setNotice]=useState('');
  const pack=useRef<Pack|null>(null);
  const capture=useRef<AbortController|null>(null);
  const stop=useRef<(()=>void)|null>(null);
  const native=useRef<Recognition|null>(null);
  const generation=useRef(0);
  const timer=useRef<number|undefined>(undefined);
  const browser=window as unknown as {SpeechRecognition?:Speech;webkitSpeechRecognition?:Speech};
  const SpeechRecognition=browser.SpeechRecognition||browser.webkitSpeechRecognition;
  const options:Options={langs:[code==='bn'?'bn-BD':code==='en'?'en-US':code],processLocally:true};

  function release(){
    generation.current++;
    clearTimeout(timer.current);
    const engine=native.current;native.current=null;
    if(engine){engine.onresult=null;engine.onend=null;engine.onerror=null;engine.abort();}
    capture.current?.abort();capture.current=null;stop.current=null;
    pack.current?.close();pack.current=null;
  }
  useEffect(()=>{setStage('idle');setNotice('');return release;},[code]);

  useEffect(()=>{
    if(stage!=='ready')return;
    const idle=window.setTimeout(()=>{release();setStage('idle');},120000);
    return ()=>clearTimeout(idle);
  },[stage]);

  function heard(text:string){
    if(text.trim()){
      onText(normalizeSpokenAmount(text.trim(),currency).slice(0,400));
      setNotice(t('Check the names and amount in the editable transcript before reading the record.'));
    }else setNotice(t('Could not hear the record. Try again or type it.'));
  }
  async function prepare(download:boolean,epoch:number){
    if(!['bn','en'].includes(code)||!window.isSecureContext||!('Worker' in window)||!window.MediaRecorder||!window.caches){
      setStage('idle');setNotice(t('On-device speech is unavailable for this language. You can type the record.'));return;
    }
    setStage('pack-loading');
    try{
      const {openSpeechPack}=await import('./localSpeechPack');
      if(generation.current!==epoch)return;
      pack.current??=openSpeechPack();
      const result=await pack.current.prepare(code,download);
      if(generation.current!==epoch)return;
      setStage(result==='ready'?'ready':'pack-consent');
    }catch{
      if(generation.current!==epoch)return;
      release();setStage('idle');setNotice(t('The speech pack could not open. Check your storage and connection, or type the record.'));
    }
  }
  async function begin(){
    const epoch=generation.current;
    setNotice('');setStage('checking');
    if(SpeechRecognition?.available){
      try{
        const engine=new SpeechRecognition();
        if('processLocally' in engine){
          const available=await SpeechRecognition.available(options);
          if(generation.current!==epoch)return;
          if(available==='available'){
            // Keep listening until Stop: Android ends a session after each pause, so restart it and keep every phrase.
            native.current=engine;
            engine.processLocally=true;engine.lang=options.langs[0];engine.continuous=true;engine.interimResults=false;
            let said='',stopped=false;
            engine.onresult=e=>{const r=e.results as unknown as ArrayLike<{isFinal?:boolean;0:{transcript:string}}>;
              said=Array.from(r).map(x=>x[0]?.transcript||'').join(' ').trim()||said;};
            let kept='';
            engine.onerror=()=>{if(!said&&!kept)setNotice(t('Could not hear the record. Try again or type it.'));};
            engine.onend=()=>{
              kept=(kept+' '+said).trim();said='';
              if(!stopped&&native.current===engine){try{engine.start();return;}catch{/* fall through and finish */}}
              clearTimeout(timer.current);native.current=null;stop.current=null;setStage('idle');heard(kept);
            };
            engine.start();stop.current=()=>{stopped=true;engine.stop();};setStage('recording');
            timer.current=window.setTimeout(()=>stop.current?.(),180000);return;
          }
          if((available==='downloadable'||available==='downloading')&&SpeechRecognition.install){
            setStage('native-install');return;
          }
        }
      }catch{if(generation.current!==epoch)return;native.current=null;}
    }
    await prepare(false,epoch);
  }
  async function install(){
    const epoch=generation.current;setStage('checking');
    try{
      if(await SpeechRecognition?.install?.(options)){
        if(generation.current===epoch){setStage('idle');setNotice(t('Language pack ready. Tap Dictate to record.'));}
        return;
      }
    }catch{/* Try the same-origin speech pack after a browser denies installation. */}
    if(generation.current===epoch)await prepare(false,epoch);
  }
  async function record(){
    const epoch=generation.current;
    const controller=new AbortController();capture.current=controller;
    setStage('checking');setNotice('');
    try{
      const {recordSpeech}=await import('./localSpeechPack');
      if(controller.signal.aborted||!pack.current)return;
      const samples=await recordSpeech(controller.signal,finish=>{
        if(controller.signal.aborted)return;
        stop.current=finish;setStage('recording');
      });
      if(controller.signal.aborted||!pack.current)return;
      stop.current=null;setStage('transcribing');
      const text=await pack.current.transcribe(samples);
      if(generation.current===epoch)heard(text);
    }catch{
      if(generation.current===epoch)setNotice(t('Could not hear the record. Try again or type it.'));
    }finally{
      if(generation.current===epoch){stop.current=null;capture.current=null;setStage('ready');}
    }
  }
  const busy=stage==='checking'||stage==='pack-loading'||stage==='transcribing';
  return <div>
    {stage==='native-install'&&<p className="small muted">{t('Your browser can download a language pack for private dictation.')}</p>}
    {stage==='pack-consent'&&<p className="small muted">{t('The {model} speech pack is about {size} MB. Audio stays on this device. Check names and amounts before saving.',{model:code==='bn'?'Vosk বাংলা':'Whisper',size:code==='bn'?'106':'116'})} <a href="/speech-licenses/NOTICE.txt" target="_blank" rel="noreferrer">{t('Speech model licenses')}</a></p>}
    {stage==='idle'&&<button className="secondary" type="button" onClick={()=>void begin()}>{t('Dictate on this device')}</button>}
    {stage==='native-install'&&<button className="secondary" type="button" onClick={()=>void install()}>{t('Install browser language pack')}</button>}
    {stage==='pack-consent'&&<button className="secondary" type="button" onClick={()=>void prepare(true,generation.current)}>{t('Download offline speech pack')}</button>}
    {stage==='ready'&&<button className="secondary" type="button" onClick={()=>void record()}>{t('Record on this device')}</button>}
    {stage==='recording'&&<button className="secondary" type="button" onClick={()=>stop.current?.()}>{t('Stop recording')}</button>}
    {busy&&<p role="status">{stage==='transcribing'?t('Transcribing on this device…'):t('Preparing on-device speech…')}</p>}
    {(busy||stage==='native-install'||stage==='pack-consent')&&<button className="link" type="button" onClick={()=>{release();setStage('idle');setNotice('');}}>{t('Cancel')}</button>}
    {notice&&<p role="status">{notice}</p>}
  </div>;
}
