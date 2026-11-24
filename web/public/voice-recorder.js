// Mono 16 kHz PCM capture with no third-party code or network access.
class VoiceRecorder extends AudioWorkletProcessor {
  constructor(){
    super();
    this.buffer=new Int16Array(30*16000);
    this.frames=0;this.done=false;
    this.port.onmessage=event=>{if(event.data==='stop')this.finish();};
  }
  finish(){
    if(this.done)return;
    this.done=true;
    const samples=this.buffer.slice(0,this.frames);
    this.port.postMessage(samples.buffer,[samples.buffer]);
  }
  process(inputs){
    if(this.done)return false;
    const input=inputs[0]?.[0];
    if(input){
      for(let i=0;i<input.length && this.frames<this.buffer.length;i++){
        const value=Math.max(-1,Math.min(1,input[i]));
        this.buffer[this.frames++]=Math.round(value*(value<0?32768:32767));
      }
      if(this.frames===this.buffer.length)this.finish();
    }
    return !this.done;
  }
}
registerProcessor('voice-recorder',VoiceRecorder);
