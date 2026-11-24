// Challenge 1 in the real web client: a week of spending typed or spoken as one note becomes separate drafts,
// the owner edits one and unticks one, and the rest are saved together.
const assert=require('node:assert/strict');
const path=require('node:path');
const readline=require('node:readline');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const {chromium}=require('../web/node_modules/playwright');

const NOTE='Shombar dokan bhara 5000 taka dilam. Tarpor transport e 300. Budhbar Rahim theke 12 hajar taka pelam, shukrobar biddut bill 1,850 taka. Karim 2000 taka dey nai. Aj chaa nasta 250.';
(async()=>{
  const server=spawn(process.env.PYTHON||'python',['scripts/serve_golden_site.py'],{cwd:path.resolve(__dirname,'..'),stdio:['ignore','pipe','inherit']});
  const line=readline.createInterface({input:server.stdout});
  let browser;
  try{
    const [origin]=await once(line,'line');
    for(let i=0;i<100;i++){try{if((await fetch(origin+'/api/ready')).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
    browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined});
    const context=await browser.newContext({viewport:{width:390,height:860},locale:'en-US',timezoneId:'Asia/Dhaka'});
    await context.route(/^https?:\/\//,r=>new URL(r.request().url()).origin===origin?r.continue():r.abort());
    let primary=0,alternate=0,compared=0;
    const question='আমার কাছে কত টাকা আছে?';
    await context.route('**/api/assist/voice-note',route=>{
      if(route.request().method()==='GET')return route.fulfill({json:{speech:true,cloud_allowed:true,max_seconds:180,simulation_only:true}});
      const capture=route.request().postDataJSON();
      assert.equal(capture.language,'bn');
      if(capture.mode==='primary'){
        primary++;
        return route.fulfill({status:503,json:{error:{code:'SPEECH_SERVICE_BUSY'}}});
      }
      if(capture.mode==='compare'){
        compared++;
        if(compared===1)return route.fulfill({json:{
          transcript:'আজ ভাড়া ৳৫০০ দিলাম। ঠিক আছে?',engine:'whisper',agreement:'review',simulation_only:true,
          alternatives:[{transcript:'আজ ভাড়া ৳৬০০ দিলাম।',model:'whisper-large-v3-turbo'}]
        }});
        return route.fulfill({json:{transcript:question,engine:'whisper',agreement:'single',simulation_only:true}});
      }
      alternate++;
      return route.fulfill({json:{transcript:'আমি ৳৫০ দিয়ে নাস্তা কিনলাম।',engine:'whisper',simulation_only:true}});
    });
    await context.addInitScript(()=>{
      Object.defineProperty(navigator.mediaDevices,'getUserMedia',{value:async()=>({getTracks:()=>[{stop(){}}]})});
      Object.defineProperty(window,'MediaRecorder',{value:class {
        static isTypeSupported(){return true;}
        mimeType='audio/webm';
        state='inactive';
        constructor(stream){this.stream=stream;}
        start(){this.state='recording';}
        stop(){
          this.state='inactive';
          this.ondataavailable?.({data:new Blob([new Uint8Array(2000)],{type:this.mimeType})});
          this.onstop?.();
        }
      }});
      let recognitionSessions=0;
      Object.defineProperty(window,'SpeechRecognition',{value:class {
        start(){
          const session=++recognitionSessions;
          this.timer=setTimeout(()=>{
            const transcript=session===1?'আজ নাস্তায় ৫০ টাকা দিলাম।':'আর ভাড়া ১০০ টাকা দিলাম।';
            this.onresult?.({resultIndex:0,results:[{isFinal:true,0:{transcript}}]});
            if(session===1)this.onend?.();
          },80);
        }
        stop(){clearTimeout(this.timer);this.onend?.();}
      }});
    });
    const page=await context.newPage();page.setDefaultTimeout(15000);
    await page.goto(origin);
    await page.getByRole('link',{name:'Try the sample demo'}).click();
    await page.getByRole('button',{name:'Make drafts'}).click();
    assert.equal(await page.locator('.competition-demo .batch-list>li').count(),4);
    await page.getByRole('button',{name:'Confirm sample drafts'}).click();
    await page.getByText(/4 sample drafts reviewed/).waitFor();
    await page.getByText('Alert',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Overdue payment may still arrive'}).click();
    await page.getByText('Watch',{exact:true}).waitFor();
    await page.getByRole('button',{name:'Seven-day spending sample'}).click();
    await page.getByRole('button',{name:'Make drafts'}).click();
    await page.locator('.competition-demo .batch-list>li').nth(6).waitFor();
    assert.equal(await page.locator('.competition-demo .batch-list>li').count(),7);
    await page.getByText(/21,950\.00/).waitFor();
    await page.getByRole('link',{name:'Open workspace'}).click();
    console.log('PASS public competition walkthrough: confirmed sample drafts and switched radar scenarios without an account');
    await page.getByRole('button',{name:'Create account',exact:true}).click();
    await page.locator('#username').fill('voice_week');await page.locator('#password').fill('Fictional voice business 57!');
    await page.getByRole('button',{name:'Create my workspace'}).click();
    await page.getByRole('button',{name:'I saved it'}).click();
    await page.getByRole('button',{name:/Later|Not now|Skip|Maybe later/}).first().click({timeout:6000}).catch(()=>{});
    await page.locator('nav [data-tour="Data"]').click();
    const panel=page.locator('.voice-note');
    await panel.getByLabel('What you said (you can correct it)').fill(NOTE);
    await panel.getByRole('button',{name:'Make drafts'}).click();
    const items=panel.locator('.batch-list>li');
    await items.first().waitFor();
    assert.equal(await items.count(),5);  // "Karim 2000 taka dey nai" (not paid) is not a draft
    await panel.locator('.voice-summary .voice-day').first().waitFor();
    assert.match(await panel.locator('.voice-totals').innerText(),/12,000\.00/);
    assert.equal(await items.nth(2).getByLabel('Type').inputValue(),'income');
    await items.nth(1).getByLabel(/^Amount/).fill('350');            // the owner corrects transport
    await items.nth(4).getByRole('checkbox').uncheck();               // and leaves the tea out
    const wide=await page.evaluate(()=>document.documentElement.scrollWidth);
    assert.ok(wide<=392,`page is ${wide}px wide on a phone`);
    await panel.getByRole('button',{name:'Save 4 records'}).click();
    await panel.locator('.success').waitFor();
    assert.match(await panel.locator('.success').innerText(),/4 records added/);
    const snap=await page.evaluate(async()=>(await (await fetch('/api/workspace',{credentials:'same-origin',headers:{'X-SafeLedger-Client':'web'}})).json()).snapshot);
    assert.equal(snap.income_minor,1200000);
    assert.equal(snap.expenses_minor,500000+35000+185000);
    console.log('PASS voice note: one note became 5 drafts, edited and saved 4 together');
    await panel.getByRole('button',{name:'Record a voice note',exact:true}).click();
    await panel.getByRole('button',{name:/^Stop \(/}).click();
    await panel.getByText(/recording is still available to replay or retry/i).waitFor();
    await panel.getByRole('button',{name:'Try another transcription'}).click();
    await panel.getByRole('button',{name:'Make drafts'}).click();
    await panel.getByLabel(/^Amount/).waitFor();
    assert.equal(await panel.getByLabel(/^Amount/).inputValue(),'50.00');
    assert.equal(await panel.locator('.batch-list input[type=checkbox]').isChecked(),false);
    assert.equal(primary,1);
    assert.equal(alternate,1);
    console.log('PASS Bangla recording retained after provider failure and retried without recording again');

    await panel.getByRole('button',{name:'Clear',exact:true}).click();
    await panel.getByRole('button',{name:'Start live dictation'}).click();
    await page.waitForFunction(()=>document.querySelector('#voice-transcript').value.includes('১০০'));
    await panel.getByRole('button',{name:'Stop dictation'}).click();
    assert.match(await panel.getByLabel('What you said (you can correct it)').inputValue(),/৫০[\s\S]*১০০/);
    console.log('PASS dictation restarted after disconnection and retained both phrases');

    await page.locator('nav [data-tour="Assistant"]').click();
    await page.getByRole('button',{name:'Talk to Capzoryx'}).click();
    const assistant=page.locator('.chat-panel');
    const voice=assistant.locator('.voice-note');
    await voice.getByRole('button',{name:'Record a voice note',exact:true}).click();
    await voice.getByRole('button',{name:'Stop (0:02)',exact:true}).waitFor();
    await voice.getByRole('button',{name:/^Stop \(/}).click();
    await voice.getByText(/models gave different results/i).waitFor();
    await voice.getByText('Compare transcript from whisper-large-v3-turbo').click();
    await voice.getByText('আজ ভাড়া ৳৬০০ দিলাম।').waitFor();
    const candidate=voice.locator('.batch-list input[type=checkbox]');
    await candidate.waitFor();
    assert.equal(await candidate.isChecked(),false);
    const workspace=()=>page.evaluate(async()=>(await (await fetch('/api/workspace',{headers:{'X-SafeLedger-Client':'web'}})).json()).snapshot);
    assert.equal((await workspace()).expenses_minor,snap.expenses_minor);
    await candidate.check();
    await voice.getByRole('button',{name:'Save 1 records'}).click();
    await voice.getByText(/1 records added/).waitFor();
    assert.equal((await workspace()).expenses_minor,snap.expenses_minor+50000);
    assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=392));
    console.log('PASS voice survives a pause, previews model disagreement, and saves only after confirmation');

    await voice.getByRole('button',{name:'Record a voice note',exact:true}).click();
    await voice.getByRole('button',{name:/^Stop \(/}).click();
    await assistant.locator('.chat-message.user').getByText(question,{exact:true}).waitFor();
    const answer=assistant.locator('.chat-message.assistant').last();
    await answer.getByText(/৳[৪4],?[৩3][০0][০0]/u).waitFor();
    assert.equal(await voice.locator('.batch-list>li').count(),0);
    assert.equal((await workspace()).expenses_minor,snap.expenses_minor+50000);
    assert.equal(compared,2);
    console.log('PASS spoken balance question uses current workspace without creating a draft');
  }finally{await browser?.close();server.kill('SIGTERM');}
})().catch(error=>{console.error(error);process.exit(1);});
