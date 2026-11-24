/** Capture an artificial tone through the real recorder and verify consent before an upload. */
const assert=require('node:assert/strict'), http=require('node:http'), fs=require('node:fs'),path=require('node:path');
const esbuild=require('../web/node_modules/esbuild'),{chromium}=require('../web/node_modules/playwright');
const root=path.resolve(__dirname,'../web');
(async()=>{
 const bundle=esbuild.buildSync({stdin:{contents:`
 import React,{useState} from 'react';import {createRoot} from 'react-dom/client';
 import {VoiceCapture} from './src/VoiceCapture';
 function Fixture(){const [value,setValue]=useState('');
 async function request(path,body){return (await fetch('/api'+path,{method:'POST',body:JSON.stringify(body)})).json();}
 return <><VoiceCapture provider={{host:'synthetic.test',model:'smoke'}} request={request} onText={setValue}/>
 <textarea aria-label="Transcript" value={value} onChange={e=>setValue(e.target.value)}/></>;}
 createRoot(document.querySelector('main')).render(<Fixture/>);`,loader:'tsx',resolveDir:root},
 bundle:true,write:false,jsx:'automatic',define:{'process.env.NODE_ENV':'"production"'}}).outputFiles[0].contents;
 const uploads=[];
 const server=http.createServer((req,res)=>{
   if(req.url==='/api/assist/voice'&&req.method==='POST'){
     const chunks=[];req.on('data',c=>chunks.push(c));req.on('end',()=>{
       uploads.push(JSON.parse(Buffer.concat(chunks)));res.setHeader('Content-Type','application/json');
       res.end(JSON.stringify({transcript:'Synthetic voice transcript',simulation_only:true}));
     });return;
   }
   if(req.url==='/app.js'||req.url==='/voice-recorder.js'){
     res.setHeader('Content-Type','application/javascript');
     res.end(req.url==='/app.js'?bundle:fs.readFileSync(path.join(root,'public/voice-recorder.js')));return;
   }
   res.setHeader('Content-Type','text/html');res.end('<!doctype html><main></main><script src="/app.js"></script>');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 let browser;
 try{
   browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,args:['--autoplay-policy=no-user-gesture-required']});
   const page=await browser.newPage();
   await page.addInitScript(()=>{
     const contexts=[];
     navigator.mediaDevices.getUserMedia=async()=>{
       const ctx=new AudioContext({sampleRate:16000}),tone=ctx.createOscillator(),sink=ctx.createMediaStreamDestination();
       contexts.push(ctx,tone,sink);tone.connect(sink);tone.start();await ctx.resume();return sink.stream;
     };
   });
   await page.goto('http://127.0.0.1:'+server.address().port);
   async function record(){
     await page.getByRole('button',{name:'Record a voice note'}).click();
     const stop=page.getByRole('button',{name:'Stop recording'});await stop.waitFor();
     await page.waitForTimeout(400);await stop.click();
     await page.getByRole('button',{name:'Send clip for transcription'}).waitFor();
   }
   await record();assert.equal(uploads.length,0);
   await page.getByRole('button',{name:'Discard recording'}).click();assert.equal(uploads.length,0);
   await record();await page.getByRole('button',{name:'Send clip for transcription'}).click();
   await page.waitForFunction(()=>document.querySelector('textarea')?.value==='Synthetic voice transcript');
   assert.equal(uploads.length,1);
   const body=uploads[0],wave=Buffer.from(body.audio_b64,'base64');
   assert.equal(body.consent,true);assert.equal(body.expected_host,'synthetic.test');
   assert.equal(wave.subarray(0,4).toString(),'RIFF');assert.equal(wave.readUInt32LE(24),16000);
   assert.equal(wave.readUInt32LE(4),wave.length-8);assert.equal(wave.readUInt32LE(40),wave.length-44);
   assert.ok(wave.subarray(44).some(x=>x!==0));
   await page.getByRole('textbox',{name:'Transcript'}).fill('Reviewed transcript');
   console.log('PASS synthetic microphone, discard without upload, explicit WAV upload and editable transcript');
 }finally{await browser?.close();await new Promise(resolve=>server.close(resolve));}
})().catch(e=>{console.error(e);process.exitCode=1;});
