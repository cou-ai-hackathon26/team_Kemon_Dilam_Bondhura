import {useEffect,useRef,useState} from 'react';
import {t,useI18n} from './i18n';

type Props={
  provider:{host:string;model:string};
  onText:(transcript:string)=>void;
  request:<T>(path:string,body?:unknown)=>Promise<T>;
};
function encodeWave(pcm:ArrayBuffer){
  const bytes=new Uint8Array(44+pcm.byteLength),view=new DataView(bytes.buffer);
  for(const [offset,text] of [[0,'RIFF'],[8,'WAVE'],[12,'fmt '],[36,'data']] as const){
    bytes.set(new TextEncoder().encode(text),offset);
  }
  view.setUint32(4,bytes.length-8,true);view.setUint32(16,16,true);
  view.setUint16(20,1,true);view.setUint16(22,1,true);
  view.setUint32(24,16000,true);view.setUint32(28,32000,true);
  view.setUint16(32,2,true);view.setUint16(34,16,true);view.setUint32(40,pcm.byteLength,true);
  bytes.set(new Uint8Array(pcm),44);
  let binary='';
  for(let offset=0;offset<bytes.length;offset+=8192)binary+=String.fromCharCode(...bytes.subarray(offset,offset+8192));
  return btoa(binary);
}
export function VoiceCapture({provider,request,onText}:Props){
  const {code}=useI18n();
  const [state,setState]=useState<'idle'|'starting'|'recording'|'review'|'sending'>('idle');
  const [error,setError]=useState('');
  const active=useRef<{stream:MediaStream;context:AudioContext;node?:AudioWorkletNode}|null>(null);
  const alive=useRef(true);
  const pcm=useRef<ArrayBuffer|null>(null);
  const timer=useRef<number|undefined>(undefined);
  function release(){
    clearTimeout(timer.current);
    const recording=active.current;active.current=null;
    if(recording){
      recording.stream.getTracks().forEach(track=>track.stop());
      recording.node?.disconnect();void recording.context.close().catch(()=>{});
    }
  }
  useEffect(()=>{alive.current=true;return ()=>{alive.current=false;release();pcm.current=null;};},[]);
  async function record(){
    if(state!=='idle')return;
    setState('starting');setError('');
    let stream:MediaStream|null=null;
    try{
      stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1},video:false});
      if(!alive.current){stream.getTracks().forEach(track=>track.stop());return;}
      const context=new AudioContext({sampleRate:16000});
      active.current={stream,context};
      await context.audioWorklet.addModule('/voice-recorder.js');
      if(!alive.current || active.current?.context!==context)return;
      const node=new AudioWorkletNode(context,'voice-recorder',{numberOfInputs:1,numberOfOutputs:0});
      active.current.node=node;
      const stop=()=>node.port.postMessage('stop');
      node.port.onmessage=event=>{
        if(!alive.current||active.current?.node!==node)return;
        pcm.current=event.data instanceof ArrayBuffer && event.data.byteLength>0?event.data:null;
        release();setState(pcm.current?'review':'idle');
        if(!pcm.current)setError(t('Could not hear the record. Try again or type it.'));
      };
      stream.getAudioTracks()[0].addEventListener('ended',stop,{once:true});
      context.createMediaStreamSource(stream).connect(node);
      await context.resume();
      if(!alive.current||active.current?.node!==node)return;
      setState('recording');timer.current=window.setTimeout(stop,30000);
    }catch{
      stream?.getTracks().forEach(track=>track.stop());release();
      if(alive.current){setState('idle');setError(t('Microphone unavailable. You can type the record.'));}
    }
  }
  async function send(){
    if(!pcm.current||state!=='review')return;
    const samples=pcm.current;
    pcm.current=null;setState('sending');setError('');
    try{
      const data=await request<{transcript:string}>('/assist/voice',{audio_b64:encodeWave(samples),language:code==='bn'?'bn':'en',
        consent:true,expected_host:provider.host,expected_model:provider.model});
      if(alive.current)onText(data.transcript);
    }catch(e){if(alive.current)setError(e instanceof Error?e.message:t('Could not hear the record. Try again or type it.'));}
    finally{if(alive.current)setState('idle');}
  }
  return <section><p className="small muted">{t('Record up to 30 seconds. Send the clip to {host} ({model}) only after you review the recording length.',provider)}</p>
    {state==='review'?<><p>{t('Recorded {seconds} seconds.',{seconds:Math.ceil((pcm.current?.byteLength||0)/32000)})}</p>
      <button type="button" onClick={()=>void send()}>{t('Send clip for transcription')}</button>
      <button className="secondary" type="button" onClick={()=>{pcm.current=null;setState('idle');}}>{t('Discard recording')}</button></>:
      <button className="secondary" type="button" disabled={state==='starting'||state==='sending'}
        onClick={()=>state==='recording'?active.current?.node?.port.postMessage('stop'):void record()}>
        {state==='recording'?t('Stop recording'):state==='sending'?t('Transcribing…'):t('Record a voice note')}</button>}
    {error&&<p role="alert" className="error">{error}</p>}</section>;
}
