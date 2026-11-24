// Only imported on demand. Microphone bytes stay in memory until the worker returns the draft.
export type Pack = {
  prepare:(lang:string, download:boolean)=>Promise<'ready'|'download-needed'>;
  transcribe:(samples:Float32Array<ArrayBuffer>)=>Promise<string>;
  close:()=>void;
};

export function openSpeechPack():Pack {
  const worker = new Worker('/speech-worker.js');
  type Reply = {type:string;text?:string};
  let pending:((reply:Reply)=>void)|undefined;
  function complete(reply:Reply){const callback=pending;pending=undefined;callback?.(reply);}
  worker.onmessage = event => complete(event.data);
  worker.onerror = () => complete({type:'error'});
  const close = () => {worker.terminate();complete({type:'error'});};
  function call(data:unknown, transfer:Transferable[]=[]):Promise<Reply>{
    if(pending)return Promise.reject(new Error('Speech in progress'));
    return new Promise((resolve,reject)=>{
      const timeout=window.setTimeout(close,300000);
      pending=reply=>{clearTimeout(timeout);if(reply.type==='error')reject(new Error('Speech unavailable'));else resolve(reply);};
      worker.postMessage(data,transfer);
    });
  }
  return {
    async prepare(lang,download){const reply=await call({type:'prepare',lang,download});
      if(reply.type!=='ready'&&reply.type!=='download-needed')throw new Error('Speech unavailable');
      return reply.type;},
    async transcribe(samples){const reply=await call({type:'audio',samples},[samples.buffer]);return reply.text||'';},
    close,
  };
}

export async function recordSpeech(signal:AbortSignal, started:(stop:()=>void)=>void):Promise<Float32Array<ArrayBuffer>> {
  const stream=await navigator.mediaDevices.getUserMedia({audio:{channelCount:1},video:false});
  let recorder:MediaRecorder|undefined;
  let timer:number|undefined;
  const stop=()=>{if(recorder?.state==='recording')recorder.stop();};
  try{
    if(signal.aborted)throw new Error('Cancelled');
    recorder=new MediaRecorder(stream);
    const chunks:Blob[]=[];
    let size=0;
    await new Promise<void>((resolve,reject)=>{
      recorder!.ondataavailable=event=>{
        size+=event.data.size;
        if(size>2_000_000){stop();reject(new Error('Clip too large'));}else chunks.push(event.data);
      };
      recorder!.onerror=()=>reject(new Error('Audio unavailable'));
      recorder!.onstop=()=>resolve();
      signal.addEventListener('abort',stop,{once:true});
      recorder!.start(250);
      timer=window.setTimeout(stop,29500);
      started(stop);
    });
    stream.getTracks().forEach(track=>track.stop());
    if(signal.aborted)throw new Error('Cancelled');
    const audio=new OfflineAudioContext(1,1,16000);
    const decoded=await audio.decodeAudioData(await new Blob(chunks,{type:recorder.mimeType}).arrayBuffer());
    if(decoded.length<4000 || decoded.length>30*16000)throw new Error('Invalid clip');
    const samples=new Float32Array(decoded.getChannelData(0));
    if(!samples.some(value=>Math.abs(value)>0.004))throw new Error('No speech');
    return samples;
  }finally{
    clearTimeout(timer);signal.removeEventListener('abort',stop);
    stop();stream.getTracks().forEach(track=>track.stop());
  }
}
