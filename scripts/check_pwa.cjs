/** Exercise the installed service worker, real API, IndexedDB and a cold offline navigation with synthetic records. */
const assert=require('node:assert/strict');
const path=require('node:path');
const readline=require('node:readline');
const {spawn}=require('node:child_process');
const {chromium}=require('../web/node_modules/playwright');
const root=path.resolve(__dirname,'..');
async function poll(check){
  const end=Date.now()+20000;
  while(Date.now()<end){
    if(await check())return;
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw Error('Browser state did not settle');
}
(async()=>{
  const server=spawn(process.env.PYTHON||'python',['scripts/serve_pwa_test.py'],{cwd:root,stdio:['ignore','pipe','inherit']});
  let browser;
  try{
    const [origin]=await Promise.race([
      require('node:events').once(readline.createInterface({input:server.stdout}),'line'),
      new Promise((_,reject)=>server.once('exit',code=>reject(Error('Test server exited '+code))))
    ]);
    assert.match(origin,/^http:\/\/127\.0\.0\.1:[0-9]+$/);
    await poll(async()=>{
      try{return (await fetch(origin+'/api/ready')).ok;}catch{return false;}
    });
    const context=await chromium.launchPersistentContext('',{headless:!process.env.DISPLAY,executablePath:process.env.CHROMIUM_PATH||undefined,
      args:['--bypass-app-banner-engagement-checks'],viewport:{width:390,height:844}});
    browser=context;
    const page=await context.newPage();
    await page.addInitScript(()=>window.addEventListener('beforeinstallprompt',()=>{window.sawInstallPrompt=true;}));
    page.setDefaultTimeout(20000);
    await page.goto(origin);
    await page.evaluate(()=>navigator.serviceWorker.ready);
    await page.waitForFunction(()=>!!navigator.serviceWorker.controller);
    const cdp=await context.newCDPSession(page);
    assert.deepEqual((await cdp.send('Page.getInstallabilityErrors')).installabilityErrors,[]);
    await page.locator('body').click({position:{x:2,y:2}});
    if(process.argv.includes('--managed-browser')){
      console.log('PASS Chrome reports zero installability errors; native install prompt skipped on managed browser');
    }else{
      await page.waitForFunction(()=>window.sawInstallPrompt===true);
      console.log('PASS browser emitted beforeinstallprompt');
    }
    const signup=await context.request.post(origin+'/api/auth/signup',{headers:{Origin:origin,'X-SafeLedger-Client':'web'},
      data:{username:'offline_browser_test',password:'Fictional workspace for QA 57!'}});
    assert.equal(signup.status(),201);
    await page.reload();
    await page.getByRole('heading',{name:'Financial overview',exact:true}).waitFor();
    await poll(async()=>!!(await page.evaluate(()=>window.capzoryxStore.scope())));
    // The quick tour no longer opens by itself; close it only if an older build shows it.
    {const skip=page.locator('dialog[open]').getByRole('button',{name:/skip/i});if(await skip.isVisible({timeout:1500}).catch(()=>false))await skip.click();}
    // New owners are offered business setup first; this check skips it (it is covered by check_business_browser.cjs).
    await page.getByRole('dialog',{name:'Set up your business'}).getByRole('button',{name:'Not now'}).click();

    await context.setOffline(true);
    await page.reload();
    await page.getByRole('button',{name:'Record a payment offline'}).click();  // offline recording is an option, not a wall
    await page.getByRole('heading',{name:'Offline capture'}).waitFor();
    await page.getByLabel(/Amount/).fill('১২৩.৪৫');
    await page.getByRole('button',{name:'Save and sync on reconnect'}).click();
    await page.getByText('Queued records: 1',{exact:true}).waitFor();
    const draft=await page.evaluate(async()=>{
      const store=window.capzoryxStore;return (await store.drafts(await store.scope()))[0];
    });
    await context.setOffline(false);
    await page.getByText('Offline records synced.',{exact:true}).waitFor();
    const snapshot=await (await context.request.get(origin+'/api/workspace')).json();
    assert.equal(snapshot.snapshot.balance_minor,12345);
    const repeated=await context.request.post(origin+'/api/workspace/offline-records',{headers:{Origin:origin,'X-SafeLedger-Client':'web'},data:draft});
    assert.equal((await repeated.json()).replayed,true);
    console.log('PASS cold offline capture, reconnect and idempotent replay');
    await page.evaluate(()=>{
      const form=document.createElement('form');form.action='/share';form.method='POST';
      const input=document.createElement('input');input.name='text';input.value='Synthetic payment sample';
      form.appendChild(input);document.body.appendChild(form);form.submit();
    });
    await page.waitForURL('**/share?id=*');
    await page.getByRole('heading',{name:'Review shared payment text'}).waitFor();
    assert.ok(await page.getByText('Synthetic payment sample',{exact:true}).isVisible());
    const paths=await page.evaluate(async()=>{
      const result=[];
      for(const key of await caches.keys())for(const req of await (await caches.open(key)).keys())result.push(new URL(req.url).pathname);
      return result;
    });
    assert.ok(paths.includes('/index.html'));
    assert.ok(!paths.some(p=>p.startsWith('/api')||p==='/share'));
    const headers={Origin:origin,'X-SafeLedger-Client':'web'};
    const invitation=await context.request.post(origin+'/api/account/invitations',{headers,data:{role:'bookkeeper'}});
    assert.equal(invitation.status(),201);
    const code=(await invitation.json()).code;
    const id=new URL(page.url()).searchParams.get('id');
    await page.getByRole('button',{name:'Sign out',exact:true}).click();
    await poll(()=>page.evaluate(async id=>!(await window.capzoryxStore.scope()) && !(await window.capzoryxStore.getShare(id)) &&
      !(await caches.keys()).some(k=>k.startsWith('capzoryx-')),id));
    const accepted=await page.evaluate(async draft=>{
      try{await window.capzoryxStore.saveDraft(draft);return true;}catch{return false;}
    },draft);
    assert.equal(accepted,false,'a stale open form cannot write a draft after logout');
    console.log('PASS Web Share Target POST, scoped review, private routes uncached and logout cleanup');
    const newMember=await context.request.post(origin+'/api/auth/signup',{headers,
      data:{username:'offline_browser_recorder',password:'Fictional workspace for QA 28!'}});
    assert.equal(newMember.status(),201);
    const joined=await context.request.post(origin+'/api/account/join',{headers,data:{code}});
    assert.equal((await joined.json()).role,'bookkeeper');
    await page.reload();
    await poll(()=>page.evaluate(async()=>(await window.capzoryxStore.scope())?.role==='bookkeeper'));
    await poll(()=>page.evaluate(()=>!!navigator.serviceWorker.controller));
    const shareStatus=await page.evaluate(async()=>(await fetch('/share',{method:'POST',
      body:new URLSearchParams({text:'Synthetic wallet text'}),redirect:'manual'})).status);
    assert.equal(shareStatus,403);
    await poll(()=>page.evaluate(async()=>!!(await caches.match('/index.html',{ignoreVary:true}))));
    await context.setOffline(true);
    await page.reload();
    await page.getByRole('button',{name:'Record a payment offline'}).click();  // offline recording is an option, not a wall
    await page.getByRole('heading',{name:'Offline capture'}).waitFor();
    await page.getByLabel(/Amount/).fill('25.50');
    await page.getByRole('button',{name:'Save and sync on reconnect'}).click();
    await page.getByText('Queued records: 1',{exact:true}).waitFor();
    await context.setOffline(false);
    await page.getByText('Offline records synced.',{exact:true}).waitFor();
    const after=await (await context.request.get(origin+'/api/workspace')).json();
    assert.equal(after.snapshot.balance_minor,14895);
    console.log('PASS bookkeeper can sync their cash record and cannot receive wallet shares');
  }finally{if(browser)await browser.close();server.kill();}
})().catch(error=>{console.error(error);process.exitCode=1;});
