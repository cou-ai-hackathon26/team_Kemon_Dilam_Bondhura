/** Real browser smoke for private dictation. Run prepare-speech.py first; optional argument is the prepared dist root. */
const assert=require('node:assert/strict'),http=require('node:http'),fs=require('node:fs'),path=require('node:path');
const os=require('node:os'),{execFileSync}=require('node:child_process');
const {chromium}=require('../web/node_modules/playwright'),esbuild=require('../web/node_modules/esbuild');
const web=path.resolve(__dirname,'../web'),dist=path.resolve(process.argv[2]||path.join(web,'dist'));
const pack='/voice-pack/',tmp=fs.mkdtempSync(path.join(os.tmpdir(),'capzoryx-speech-'));
const source=path.join(web,'public/datasets/machine-voice-smoke-v1.zip');
const wave=id=>execFileSync('unzip',['-p',source,id+'.wav']);
const config=JSON.parse(fs.readFileSync(path.join(web,'vercel.json')));
const csp=url=>config.headers.find(rule=>rule.source===url).headers.find(header=>header.key==='Content-Security-Policy').value;
const bundle=esbuild.buildSync({stdin:{contents:`
import React,{useState} from 'react';import {createRoot} from 'react-dom/client';import {LocalVoice} from './src/LocalVoice';
function Fixture(){const [text,setText]=useState('');return <><LocalVoice onText={setText}/><textarea aria-label="Draft" value={text} onChange={e=>setText(e.target.value)}/></>;}
createRoot(document.querySelector('main')).render(<Fixture/>);`,loader:'tsx',resolveDir:web},bundle:true,
  write:false,jsx:'automatic',define:{'process.env.NODE_ENV':'"production"'}}).outputFiles[0].contents;
const requests=[];
const server=http.createServer((req,res)=>{
  requests.push([req.method,req.url]);
  if(req.url==='/'){res.setHeader('Content-Security-Policy',csp('/(.*)'));
    res.end('<!doctype html><title>Speech QA</title><main></main><script src="/app.js"></script>');return;}
  if(req.url==='/app.js'){res.setHeader('Content-Type','application/javascript');res.end(bundle);return;}
  let file;
  if(req.url==='/speech-worker.js'){res.setHeader('Content-Security-Policy',csp('/speech-worker.js'));file=path.join(web,'public/speech-worker.js');}
  if(req.url.startsWith(pack)&&/^(?:whisper-tiny-v1|vosk-bn-v1)\/[\w.-]+$/.test(req.url.slice(pack.length)))file=path.join(dist,req.url);
  if(!file||!fs.existsSync(file)){res.writeHead(404).end();return;}
  const types={'.js':'application/javascript','.json':'application/json','.wasm':'application/wasm'};
  res.setHeader('Content-Type',types[path.extname(file)]||'application/octet-stream');fs.createReadStream(file).pipe(res);
});
(async()=>{
  assert.ok(fs.existsSync(path.join(dist,pack,'vosk-bn-v1/manifest.json')),'Prepare the speech pack before running the browser smoke.');
  const mic=path.join(tmp,'en.wav');fs.writeFileSync(mic,wave('en-01'));
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));let browser;
  try{
    browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined,headless:true,
      args:['--use-fake-ui-for-media-stream','--use-fake-device-for-media-stream','--use-file-for-fake-audio-capture='+mic]});
    const context=await browser.newContext({permissions:['microphone']});const page=await context.newPage();
    const origin='http://127.0.0.1:'+server.address().port;
    await page.addInitScript(()=>{Object.defineProperty(window,'SpeechRecognition',{value:undefined});
      Object.defineProperty(window,'webkitSpeechRecognition',{value:undefined});});
    await page.goto(origin);await page.getByText('Dictate on this device').click();
    await page.getByText('Download offline speech pack').waitFor();
    assert.ok(!requests.some(([,url])=>url.startsWith(pack)),'No model request before consent');
    await page.getByText('Download offline speech pack').click();
    const record=page.getByRole('button',{name:'Record on this device'});
    await record.waitFor({timeout:120000});
    await context.setOffline(true);await record.click();
    const stop=page.getByRole('button',{name:'Stop recording'});await stop.waitFor();
    await page.waitForTimeout(4200);await stop.click();
    await page.waitForFunction(()=>document.querySelector('textarea').value.trim().length>0,null,{timeout:120000});
    assert.ok(!requests.some(([method])=>method==='POST'),'No audio upload');
    console.log('PASS no pack request before consent, real Chromium capture and offline transcription into an editable draft');
    await context.setOffline(false);
    const ready=await page.evaluate(async()=>{
      window.testWorker=new Worker('/speech-worker.js');
      return new Promise((resolve,reject)=>{testWorker.onmessage=e=>resolve(e.data.type);testWorker.onerror=reject;
        testWorker.postMessage({type:'prepare',lang:'bn',download:false});});
    });
    assert.equal(ready,'download-needed','English consent did not download the Bengali pack');
    const prepared=await page.evaluate(()=>new Promise(resolve=>{
      testWorker.onmessage=e=>resolve(e.data.type);testWorker.postMessage({type:'prepare',lang:'bn',download:true});
    }));
    assert.equal(prepared,'ready');
    await context.setOffline(true);
    const bangla=await page.evaluate(async bytes=>{
      const audio=new OfflineAudioContext(1,1,16000);
      const signal=await audio.decodeAudioData(Uint8Array.from(bytes).buffer);
      return new Promise(resolve=>{testWorker.onmessage=e=>resolve(e.data);testWorker.postMessage({type:'audio',samples:signal.getChannelData(0)});});
    },[...wave('bn-07')]);
    assert.equal(bangla.type,'transcript');assert.match(bangla.text,/রাজু/);assert.match(bangla.text,/পেলাম/);
    console.log('PASS separate Bengali pack consent and offline Vosk on bn-07 (party and inflow verb)');
    const native=await browser.newPage();
    await native.addInitScript(()=>{let installed=false;class Engine {
      processLocally=false;static available(o){if(!o.processLocally)throw Error();return Promise.resolve(installed?'available':'downloadable');}
      static install(o){installed=o.processLocally===true;return Promise.resolve(installed);}
      start(){if(!this.processLocally)throw Error();this.onresult({results:[[{transcript:'installed browser voice'}]]});setTimeout(()=>this.onend(),0);}
      abort(){}stop(){}
    }Object.defineProperty(window,'SpeechRecognition',{value:Engine});});
    await native.goto(origin);await native.getByText('Dictate on this device').click();
    await native.getByText('Install browser language pack').click();await native.getByText('Dictate on this device').click();
    await native.waitForFunction(()=>document.querySelector('textarea').value==='installed browser voice');
    console.log('PASS browser language-pack consent and processLocally requirement (mocked native interface)');
  }finally{await browser?.close();server.closeAllConnections();await new Promise(r=>server.close(r));fs.rmSync(tmp,{recursive:true,force:true});}
})().catch(e=>{console.error(e);process.exitCode=1;});
