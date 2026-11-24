// Run the actual web client, finance engine and an isolated public passport verifier.
const {chromium}=require('../web/node_modules/playwright');
// Phones show four pages plus More; open More first when a page button is tucked away.
async function openPage(page,name){
  const button=page.locator(`nav [data-tour="${name}"]`);
  if(!await button.isVisible())await page.locator('nav .more-toggle').click();
  await button.click();
}
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const readline=require('node:readline');
const path=require('node:path');
const fs=require('node:fs/promises');

(async()=>{
  const server=spawn(process.env.PYTHON||'python',['scripts/serve_golden_site.py'],{cwd:path.resolve(__dirname,'..'),stdio:['ignore','pipe','inherit']});
  const stopped=once(server,'exit');
  const line=readline.createInterface({input:server.stdout});
  let browser;
  try{
    const [origin]=await Promise.race([once(line,'line'),stopped.then(()=>{throw Error('Synthetic server failed to start');})]);
    assert.match(origin,/^http:\/\/127\.0\.0\.1:[0-9]+$/);
    // A free port is reserved before FastAPI runs its schema migrations.
    let ready=false;
    for(let attempt=0;attempt<100&&!ready;attempt++){
      try{ready=(await fetch(origin+'/api/ready')).ok;}catch{}
      if(!ready)await new Promise(r=>setTimeout(r,100));
    }
    assert.ok(ready,'isolated API readiness');
    browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined});
    async function session(){
      const context=await browser.newContext({viewport:{width:390,height:844},locale:'en-US',timezoneId:'Asia/Dhaka'});
      await context.route(/^https?:\/\//,route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
      const page=await context.newPage();
      page.setDefaultTimeout(15000);
      return page;
    }
    // Capture only post-login synthetic scenes; the signup recovery code stays private.
    async function frame(page,locator,name){
      const dir=process.env.CAPZORYX_DEMO_FRAMES;
      if(!dir)return;
      await fs.mkdir(dir,{recursive:true});
      await locator.scrollIntoViewIfNeeded();
      await page.screenshot({path:path.join(dir,name+'.png'),animations:'disabled'});
    }
    const owner=await session();
    await owner.goto(origin);
    await owner.getByRole('button',{name:'Create account',exact:true}).click();
    await owner.locator('#username').fill('rahim_synthetic');
    await owner.locator('#password').fill('Fictional demo business 57!');
    await owner.getByRole('button',{name:'Create my workspace'}).click();
    // The quick tour no longer opens by itself; close it only if an older build shows it.
    {const skip=owner.locator('dialog[open]').getByRole('button',{name:/skip/i});if(await skip.isVisible({timeout:1500}).catch(()=>false))await skip.click();}
    await owner.getByRole('button',{name:'I saved it'}).click();
    // New owners are offered business setup first; these checks skip it.
    await owner.getByRole('dialog',{name:'Set up your business'}).getByRole('button',{name:'Not now'}).click();
    await owner.getByRole('button',{name:'Try a sample business'}).click();
    await owner.getByRole('heading',{name:'Cover the cash gap'}).waitFor();
    const bundle=await (await owner.request.get(origin+'/api/workspace')).json();
    assert.equal(bundle.snapshot.balance_minor,15000000);
    assert.equal(bundle.month.minimum_balance_minor,-36000000);
    assert.equal(bundle.month.closing_balance_minor,24000000);
    await owner.getByRole('button',{name:'Show records for Cleared income'}).click();
    const sources=owner.locator('#cash-sources');
    for(const e of bundle.events.filter(e=>e.status==='cleared' && e.direction==='inflow'))
      await sources.getByText(e.source_record_id,{exact:false}).waitFor();
    await owner.getByRole('button',{name:'Cash briefing',exact:true}).click();
    await owner.locator('#briefing-report').getByText(/Available cash is.*150,000.00/).waitFor();
    await openPage(owner,'Settings');
    const lowData=owner.getByRole('checkbox',{name:'Reduce background downloads'});
    await lowData.check(); assert.equal(await owner.evaluate(()=>localStorage.getItem('capzoryx-data-saver')),'on');
    await owner.getByRole('tab',{name:'Data & workspaces'}).click();
    await owner.getByRole('button',{name:'View feature settings'}).click();
    const issue=owner.getByRole('checkbox',{name:'Issue public passports'});
    await Promise.all([owner.waitForResponse(r=>r.url().endsWith('/features/passport_issue')&&r.request().method()==='POST'),issue.click()]);
    const switches=async()=>(await (await owner.request.get(origin+'/api/workspace/features')).json()).features;
    assert.equal((await switches()).find(flag=>flag.name==='passport_issue').enabled,false);
    await Promise.all([owner.waitForResponse(r=>r.url().endsWith('/features/passport_issue')&&r.request().method()==='POST'),issue.click()]); await openPage(owner,'Overview');
    assert.equal((await switches()).find(flag=>flag.name==='passport_issue').enabled,true);
    const asOf=new Date(bundle.snapshot.as_of+'T00:00:00Z');
    const day=(offset)=>new Date(asOf.getTime()+offset*86400000).toISOString().slice(0,10);
    const review=owner.locator('#agent-mode');
    await review.getByRole('button',{name:'Review suggestions'}).click();
    await review.getByText('There are no overdue customer balances to follow up.').waitFor();
    await review.getByText('Request a supplier extension').click();
    const supplier=review.getByLabel('Supplier invoice');
    await supplier.selectOption({index:1});
    await review.locator('input[type=date]').fill(day(40));
    await review.getByRole('button',{name:'Copy draft'}).waitFor();
    assert.ok((await review.locator('textarea').inputValue()).includes('BDT'));
    assert.equal(bundle.month.first_shortfall_on,day(6));
    await frame(owner,owner.getByRole('heading',{name:'Cover the cash gap'}),'01-outlook');
    await openPage(owner,'Cash flow');
    const preview=owner.waitForResponse(res=>res.url()===origin+'/api/copilot/gap-fix'&&res.request().method()==='POST');
    await owner.getByRole('button',{name:'Find a collection plan'}).click();
    const {plan}=await (await preview).json();
    assert.deepEqual(plan.actions,[{kind:'collect_early',source_id:'sample-sample-export',scheduled_on:day(25),collect_by:day(6),amount_minor:36000000}]);
    assert.equal(plan.remaining_gap_minor,0);assert.equal(plan.forecast.closing_balance_minor,24000000);
    await frame(owner,owner.getByRole('button',{name:'Accept and create collection tasks'}),'02-plan');
    await owner.getByRole('button',{name:'Accept and create collection tasks'}).click();
    await owner.getByRole('button',{name:'Mark task done'}).waitFor();
    await frame(owner,owner.getByRole('button',{name:'Mark task done'}),'03-task');
    const after=await (await owner.request.get(origin+'/api/workspace')).json();
    assert.equal(after.snapshot.balance_minor,bundle.snapshot.balance_minor);
    await openPage(owner,'Audit');
    owner.once('dialog',async dialog=>{assert.ok(dialog.message().startsWith('Create a public passport?'));await dialog.accept();});
    await owner.getByRole('button',{name:'Create a signed passport'}).click();
    const link=owner.getByRole('link',{name:'View passport'}).first();
    const href=await link.getAttribute('href');
    const guest=await session();
    const publicUrl=new URL(href,origin);
    assert.equal(publicUrl.origin,origin);
    await guest.goto(publicUrl.href);
    await guest.getByText('Signature verified',{exact:true}).waitFor();
    await guest.getByAltText('QR code to check the current passport').waitFor();
    await frame(guest,guest.getByText('Signature verified',{exact:true}),'04-passport');
    const published=await (await guest.request.get(origin+'/api/public/passport/'+href.split('/').pop())).json();
    assert.equal(published.summary.balance_minor,15000000);
    assert.equal(published.summary.receipts_minor,5000000);assert.equal(published.summary.expenses_minor,2000000);
    owner.once('dialog',async dialog=>{assert.ok(dialog.message().startsWith('Revoke this passport?'));await dialog.accept();});
    await owner.getByRole('button',{name:'Revoke link'}).click();
    await link.waitFor({state:'hidden'});
    await guest.reload();await guest.getByText(/This passport could not be verified/).waitFor();
    await frame(guest,guest.getByText(/This passport could not be verified/),'05-revoked');
    console.log('PASS Rahim signup, sample cash flow, collection proof, task, public QR and revocation');

    await owner.route('**/api/auth/capabilities',async route=>{
      const response=await route.fetch();
      await route.fulfill({response,json:{...(await response.json()),hisab_lm:true,cloud_assist:true}});
    });
    await owner.route('**/api/account/profile',async route=>{
      const response=await route.fetch();
      await route.fulfill({response,json:{...(await response.json()),smart_assist:true}});
    });
    const hisabText='I paid BDT 1250.50 to Synthetic Supplier.';
    const rejectedHisabText='I paid BDT 100 to someone.';
    const hisabRequests=[],otherRequests=[];
    await owner.route('**/api/assist/hisab',async route=>{
      hisabRequests.push(route.request().postDataJSON());
      if(hisabRequests.at(-1).text===rejectedHisabText){
        await route.fulfill({status:422,json:{error:{code:'HISAB_REVIEW_NEEDED'}}});
        return;
      }
      if(hisabRequests.length===1){
        await route.fulfill({status:503,json:{error:{code:'HISAB_NOT_AVAILABLE'}}});
        return;
      }
      await route.fulfill({json:{draft:{ok:true,kind:'cash',direction:'outflow',amount_minor:125050,
        currency:'BDT',workspace_currency:'BDT',effective_on:day(0),status:'cleared',
        memo:'',vendor_name:'Synthetic Supplier',confidence:null,warnings:[],can_save:true,currency_mismatch:false},
        model:'Hisab-LM',simulation_only:true}});
    });
    await owner.route('**/api/assist/ask',async route=>{
      otherRequests.push(route.request().postDataJSON());
      await route.fulfill({json:{answer:'Synthetic assistant answer.',simulation_only:true}});
    });
    await owner.reload();
    const revision=async()=> (await (await owner.request.get(origin+'/api/workspace')).json()).dataset_revision;
    const beforeDraft=await revision();
    await openPage(owner,'Assistant');
    const chat=owner.getByRole('region',{name:'Assistant',exact:true});
    await chat.getByRole('button',{name:'What is my balance?',exact:true}).click();
    await chat.getByRole('log').getByText(/150,000\.00|1,50,000/).waitFor();
    assert.equal(hisabRequests.length,0);
    const input=chat.getByRole('textbox',{name:'Message the assistant'});
    await input.fill(hisabText);
    await chat.getByRole('button',{name:'Use Hisab-LM',exact:true}).click();
    await chat.getByRole('button',{name:'Retry with Hisab-LM',exact:true}).click();
    await chat.getByText('Draft prepared by Hisab-LM.',{exact:true}).waitFor();
    assert.deepEqual(hisabRequests,[{text:hisabText,consent:true},{text:hisabText,consent:true}]);
    assert.equal(await chat.getByLabel('Amount (BDT)',{exact:true}).inputValue(),'1250.50');
    assert.equal(await revision(),beforeDraft);
    await chat.getByRole('button',{name:'Discard',exact:true}).click();
    await input.fill(rejectedHisabText);
    await chat.getByRole('button',{name:'Use Hisab-LM',exact:true}).click();
    await chat.getByText(/Hisab-LM could not verify this payment/).waitFor();
    assert.equal(await input.inputValue(),rejectedHisabText,'A rejected sentence is available to edit');
    await input.fill('What is the weather in Dhaka right now?');
    await chat.getByRole('button',{name:'Send message',exact:true}).click();
    await chat.getByRole('button',{name:'It is about my business: try anyway'}).click();
    await chat.getByText('Synthetic assistant answer.',{exact:true}).waitFor();
    assert.equal(otherRequests.length,1);
    assert.ok(!JSON.stringify(otherRequests[0].history).includes(hisabText),'Hisab-LM submissions stay out of other model requests');
    assert.ok(!JSON.stringify(otherRequests[0].history).includes(rejectedHisabText),'Rejected Hisab-LM submissions also stay private');
    assert.equal(await revision(),beforeDraft);
    console.log('PASS Assistant uses explicit Hisab-LM opt-in, keeps its text out of other providers, and returns an unsaved editable draft.');

    await owner.unroute('**/api/assist/hisab');
    await openPage(owner,'Data');
    const quick=owner.getByRole('region',{name:'Add by photo or by typing'});
    const quickRequests=[];
    owner.on('request',r=>{if(/\/api\/assist\/(?:hisab|extract)$/.test(r.url()))quickRequests.push(r.postDataJSON());});
    const quickText=quick.getByLabel('Describe the payment or bill');
    const readIt=quick.getByRole('button',{name:'Read it',exact:true});
    const readOnline=quick.getByRole('button',{name:'Read online',exact:true});
    assert.equal(await quick.getByRole('button',{name:'Use Hisab-LM',exact:true}).count(),0);
    await quickText.fill('i paid rahman 500');
    await readIt.click();
    assert.equal(await quick.getByLabel('Amount (BDT)',{exact:true}).inputValue(),'500.00');
    assert.equal(await quick.getByLabel('Note',{exact:true}).inputValue(),'i paid rahman 500');
    assert.equal(quickRequests.length,0);
    await quick.getByRole('button',{name:'Discard',exact:true}).click();

    const consentSentence='i paid rahman ٥٠٠';
    await quickText.fill(consentSentence);
    await readIt.click();
    await readOnline.waitFor();
    assert.match(await quick.locator('#quick-read-privacy').textContent(),/private Hugging Face/);
    assert.equal(quickRequests.length,0);
    await quickText.fill(consentSentence+'.');
    await readIt.waitFor();
    assert.equal(await readOnline.count(),0,'Editing clears the online consent prompt');
    await quickText.fill(consentSentence);
    await readIt.click();
    await readOnline.click();
    await quick.getByText('Draft prepared from the details in your message.',{exact:true}).waitFor();
    assert.deepEqual(quickRequests,[{text:consentSentence,consent:true}]);
    assert.equal(await quick.getByLabel('Amount (BDT)',{exact:true}).inputValue(),'500.00');
    assert.equal(await revision(),beforeDraft);
    await quick.getByRole('button',{name:'Add to my records',exact:true}).click();
    await quick.getByText('Added to your records.',{exact:true}).waitFor();
    assert.notEqual(await revision(),beforeDraft);
    console.log('PASS Add records used one action for local drafts, asked before sending online, and saved only after confirmation.');
  }finally{
    await browser?.close();
    line.close();
    if(server.exitCode===null&&server.signalCode===null)server.kill('SIGTERM');
    await stopped;
  }
})().catch(err=>{console.error(err.message);process.exitCode=1;});
