// Business setup in the real web client: acceptance tests A (new owner sees setup), B (profile and products
// are validated and saved) and E (a returning owner goes straight to the dashboard).
const {chromium}=require('../web/node_modules/playwright');
const assert=require('node:assert/strict');
const {spawn}=require('node:child_process');
const {once}=require('node:events');
const readline=require('node:readline');
const path=require('node:path');
const fs=require('node:fs/promises');

async function openPage(page,name){
  const button=page.locator(`nav [data-tour="${name}"]`);
  await page.locator(`nav [data-tour="Overview"]`).waitFor({state:"attached"});
  if(!await button.isVisible())await page.locator('nav .more-toggle').click();
  await button.click();
}

(async()=>{
  const server=spawn(process.env.PYTHON||'python',['scripts/serve_golden_site.py'],{cwd:path.resolve(__dirname,'..'),stdio:['ignore','pipe','inherit']});
  const stopped=once(server,'exit');
  const line=readline.createInterface({input:server.stdout});
  let browser;
  try{
    const [origin]=await Promise.race([once(line,'line'),stopped.then(()=>{throw Error('Synthetic server failed to start');})]);
    let ready=false;
    for(let attempt=0;attempt<100&&!ready;attempt++){
      try{ready=(await fetch(origin+'/api/ready')).ok;}catch{}
      if(!ready)await new Promise(r=>setTimeout(r,100));
    }
    assert.ok(ready,'isolated API readiness');
    browser=await chromium.launch({executablePath:process.env.CHROMIUM_PATH||undefined});
    const shots=process.env.SETUP_SHOTS;
    if(shots)await fs.mkdir(shots,{recursive:true});
    async function session(width){
      const context=await browser.newContext({viewport:{width,height:900},locale:'en-US',timezoneId:'Asia/Dhaka'});
      await context.route(/^https?:\/\//,route=>new URL(route.request().url()).origin===origin?route.continue():route.abort());
      const page=await context.newPage();
      page.setDefaultTimeout(15000);
      return page;
    }
    const shot=async(page,name)=>{if(shots)await page.screenshot({path:path.join(shots,name+'.png'),fullPage:true,animations:'disabled'});};

    const owner=await session(1280);
    await owner.goto(origin);
    await owner.getByRole('button',{name:'Create account',exact:true}).click();
    await owner.locator('#username').fill('gold_owner');
    await owner.locator('#password').fill('Fictional gold business 57!');
    await owner.getByRole('button',{name:'Create my workspace'}).click();
    await owner.getByRole('button',{name:'I saved it'}).click();

    // A: a new owner is offered business setup straight away.
    const wizard=owner.getByRole('dialog',{name:'Set up your business'});
    await wizard.getByRole('heading',{name:'Tell us about your business once'}).waitFor();
    await shot(owner,'setup-welcome');
    await wizard.getByRole('button',{name:'Set up my business'}).click();

    // B: three short steps; answers are picked from lists (only the business name is typed).
    await wizard.getByLabel('Business name').fill('Golden Heritage BD');
    await wizard.getByLabel('Industry').selectOption('jewelry');
    await wizard.getByLabel('What do you sell or do?').selectOption('Gold trading and jewelry retail');
    await wizard.getByLabel('City or area').selectOption('Dhaka');
    await shot(owner,'setup-identity');
    await wizard.getByRole('button',{name:'Save and continue'}).click();

    await wizard.getByLabel('Product or service').fill('22K Gold');
    await wizard.getByLabel('Category',{exact:true}).fill('Gold');
    await wizard.getByLabel('Unit',{exact:true}).fill('vori');
    await wizard.getByLabel('Quantity in stock').fill('5');
    await wizard.getByLabel(/Purchase cost per unit/).fill('194000');
    await wizard.getByLabel(/Selling price per unit/).fill('220,000');
    await wizard.getByRole('button',{name:'Add product'}).click();
    await wizard.getByText('Live metal price').waitFor();
    const file=path.join(require('node:os').tmpdir(),'capzoryx-products.csv');
    await fs.writeFile(file,'Product,Category,Unit,Cost,Price\nSilver ring,Silver,pcs,1200,1800\n22K Gold,Gold,bhori,,\n');
    await wizard.locator('input[type=file]').setInputFiles(file);
    await wizard.getByText('1 added, 1 skipped.').waitFor();
    await shot(owner,'setup-products');
    await wizard.getByRole('button',{name:'Save and continue'}).click();

    // Last step: only what the assistant may do, already switched on.
    await wizard.getByRole('heading',{name:'What Capzoryx may do'}).waitFor();
    assert.ok(await wizard.getByRole('checkbox',{name:/Send me important alerts automatically/}).isChecked());
    await wizard.getByRole('button',{name:'Finish setup'}).click();
    await owner.getByText(/Your business profile is saved|researching your market/).waitFor();
    assert.equal(await wizard.count(),0);

    const saved=await (await owner.request.get(origin+'/api/business')).json();
    assert.ok(saved.completed);
    assert.equal(saved.profile.identity.industry,'jewelry');
    assert.deepEqual(saved.profile.identity.business_types,['retail']);
    assert.ok(saved.profile.consent.alerts && saved.profile.consent.monitoring && saved.profile.consent.external_research);
    assert.deepEqual(saved.products.map(p=>[p.name,p.unit,p.market_key,p.cost_minor]),[['22K Gold','bhori','gold_22k',19400000],['Silver ring','piece','silver',120000]]);

    // D/H: Overview shows this business's market; every figure carries its source, or says it is unavailable.
    const panel=owner.locator('.market-panel');
    await panel.getByRole('heading',{name:'Your market'}).waitFor();
    await panel.locator('.market-list li').first().waitFor();
    const rows=await panel.locator('.market-list > li').allInnerTexts();
    assert.ok(rows.some(r=>r.includes('Local 22K gold dealer price')),rows.join('\n'));
    for(const row of rows)assert.match(row,/BDT\s[\d,]+\.\d\d[\s\S]*(Daily reference rate|Estimate|Your price|Web research)|Unavailable|No price yet/);
    const local=panel.locator('.market-list > li').filter({hasText:'Local 22K gold dealer price'});
    await local.getByRole('button',{name:"Add today's price"}).click();
    await local.getByLabel(/Price \(BDT/).fill('200000');
    await local.getByLabel('Where you saw it').fill('BAJUS');
    await local.getByRole('button',{name:'Save price'}).click();
    await local.getByText('Your price').waitFor();
    assert.match(await local.innerText(),/BDT\s200,000\.00/);
    await panel.getByText(/Web research is not set up|Online research is off|Research my market/).first().waitFor();
    // Phase 3: the owner's own numbers. 22K gold bought at 194,000 is now quoted at 200,000 with no selling costs entered.
    const numbers=owner.locator('.insights-panel');
    await owner.reload();
    await numbers.getByRole('heading',{name:'Your numbers'}).waitFor();
    const goldRow=numbers.locator('tbody tr').filter({hasText:'22K Gold'});
    assert.match(await goldRow.innerText(),/In profit[\s\S]*BDT\s194,000\.00[\s\S]*BDT\s200,000\.00[\s\S]*BDT\s6,000\.00/);
    await numbers.getByRole('heading',{name:'If market prices move'}).waitFor();
    await numbers.getByText('Business capability').waitFor();
    // Phase 5: the advisor offers costed options, and choosing one is remembered.
    // With a healthy position and no gaps there is nothing to say, so the panel stays hidden rather than padding advice.
    const advice=await (await owner.request.get(origin+'/api/advisor')).json();
    assert.equal(advice.insights.length,0);
    assert.deepEqual(advice.gaps.map(g=>g.lever),['Digital visibility']);  // setup no longer asks for sales channels
    await owner.locator('.advisor-panel').getByText('Where you can compete').waitFor();
    // When the price drops close to break-even, the advisor explains it and offers three costed options.
    const again=owner.locator('.market-panel .market-list > li').filter({hasText:'Local 22K gold dealer price'});
    await again.getByRole('button',{name:"Add today's price"}).click();
    await again.getByLabel(/Price \(BDT/).fill('195000');
    await again.getByLabel('Where you saw it').fill('BAJUS');
    await again.getByRole('button',{name:'Save price'}).click();
    await again.getByText(/BDT\s195,000\.00/).waitFor();
    await owner.reload();
    const advisor=owner.locator('.advisor-panel');
    await advisor.getByText('22K Gold is close to break-even').waitFor();
    for(const option of ['Protect what you have, no spending','Move stock at a margin with a small offer','Buy more only if cash is strong'])await advisor.getByRole('heading',{name:option}).waitFor();
    await advisor.getByRole('button',{name:"I'll try this"}).first().click();
    await advisor.getByText(/You chose this/).waitFor();
    await advisor.scrollIntoViewIfNeeded();await owner.screenshot({path:path.join(process.env.SETUP_SHOTS||require('node:os').tmpdir(),'advisor.png'),clip:await advisor.boundingBox()}).catch(()=>{});

    await numbers.scrollIntoViewIfNeeded();await owner.screenshot({path:path.join(process.env.SETUP_SHOTS||require('node:os').tmpdir(),'insights.png'),clip:await numbers.boundingBox()}).catch(()=>{});

    await shot(owner,'market-panel');

    // E: signing out and back in keeps the profile and does not show setup again.
    await owner.getByRole('button',{name:'Sign out'}).click();
    await owner.locator('.auth-tabs').getByRole('button',{name:'Sign in'}).click();
    await owner.locator('#username').fill('gold_owner');
    await owner.locator('#password').fill('Fictional gold business 57!');
    await owner.locator('form button.full-width').click();
    await owner.locator('nav [data-tour="Overview"]').waitFor();
    await owner.waitForTimeout(800);
    assert.equal(await wizard.count(),0);
    assert.equal(await owner.locator('.biz-resume').count(),0);

    // The profile stays editable on My business, where any business data can also be submitted.
    await owner.getByLabel('Search pages and settings').fill('business');
    await owner.getByRole('option').getByRole('button',{name:/My business/}).click();
    await owner.locator('.biz-edit').getByLabel('Business name').waitFor();
    assert.equal(await owner.locator('.biz-edit').getByLabel('Business name').inputValue(),'Golden Heritage BD');
    const data=owner.locator('.business-data');
    await data.getByLabel('What is it about?').selectOption('competitors');
    await data.getByLabel('Title').fill('New shop nearby');
    await data.getByLabel('Details').fill('Swarna Kutir opened near Mirpur 10 and sells 22K at a lower making charge.');
    await data.getByRole('button',{name:'Save business data'}).click();
    await data.locator('.business-data-list li').first().waitFor();
    assert.match(await data.locator('.business-data-list').innerText(),/New shop nearby/);
    await shot(owner,'setup-settings');
    // Phone: My business must not scroll sideways, and a new owner's analysis comes before empty cash panels.
    await owner.setViewportSize({width:390,height:844});
    await owner.waitForTimeout(300);
    const wide=await owner.evaluate(()=>document.documentElement.scrollWidth);
    assert.ok(wide<=392,`page is ${wide}px wide on a 390px phone`);
    await owner.locator('nav [data-tour="Overview"]').click();
    await owner.locator('.advisor-panel, .market-panel').first().waitFor();
    const order=await owner.evaluate(()=>{const a=document.querySelector('.market-panel'),b=document.querySelector('.balance-cards, .dashboard-grid');return !!a&&!!b&&!!(a.compareDocumentPosition(b)&Node.DOCUMENT_POSITION_FOLLOWING);});
    assert.ok(order,'business panels should come before the empty cash panels');
    console.log('PASS business setup and market: new owner sees setup, profile saved, market figures sourced or marked unavailable, returning owner goes straight in');
  }finally{
    await browser?.close();
    server.kill('SIGTERM');
  }
})().catch(error=>{console.error(error);process.exit(1);});
