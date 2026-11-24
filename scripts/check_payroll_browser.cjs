// Payroll end to end in the real web client: the owner adds an employee, pays salary and a bonus,
// shares a private invite link, and the employee signs up through it and sees only their own payslips.
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
    const shots=process.env.PAYROLL_SHOTS;
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
    await owner.locator('#username').fill('payroll_owner');
    await owner.locator('#password').fill('Fictional payroll owner 57!');
    await owner.getByRole('button',{name:'Create my workspace'}).click();
    {const skip=owner.locator('dialog[open]').getByRole('button',{name:/skip/i});if(await skip.isVisible({timeout:1500}).catch(()=>false))await skip.click();}
    await owner.getByRole('button',{name:'I saved it'}).click();
    // New owners are offered business setup first; these checks skip it.
    await owner.getByRole('dialog',{name:'Set up your business'}).getByRole('button',{name:'Not now'}).click();

    // The sidebar search finds Payroll by its Bangla word.
    await owner.getByLabel('Search pages and settings').fill('বেতন');
    await owner.getByRole('option').getByRole('button',{name:/Payroll/}).click();
    await owner.getByText('Add your first employee').waitFor();

    for(const [name,role] of [['Rahim Uddin','Accountant'],['Karim Hasan','Sales']]){
      await owner.getByRole('button',{name:'Add employee'}).first().click();
      const form=owner.locator('form.payroll-form');
      await form.getByLabel('Name').fill(name);
      await form.getByLabel('Designation').fill(role);
      await form.getByLabel(/Monthly salary/).fill('25,000');
      await form.getByRole('button',{name:'Add employee'}).click();
      await owner.getByText(`${name} added.`).waitFor();
    }
    await owner.getByRole('button',{name:/Pay 2 salaries/}).click();
    await owner.getByText(/Paid 2 salaries for/).waitFor();
    assert.ok(await owner.getByRole('button',{name:'All salaries paid'}).isDisabled());

    await owner.getByRole('button',{name:'Give a bonus'}).click();
    const bonus=owner.locator('form.payroll-form');
    await bonus.getByLabel(/Amount per person/).fill('5000');
    await bonus.getByLabel('Reason').fill('Eid bonus');
    await bonus.getByRole('checkbox',{name:'Rahim Uddin'}).check();
    await bonus.getByRole('button',{name:'Pay bonus to 1'}).click();
    await owner.getByText(/Bonus paid to 1/).waitFor();

    // Salaries are also cash spent in the books.
    const books=await (await owner.request.get(origin+'/api/workspace')).json();
    assert.equal(books.snapshot.expenses_minor,5500000);

    const rahimRow=owner.getByRole('row').filter({hasText:'Rahim Uddin'});
    await rahimRow.getByRole('button',{name:'Invite link'}).click();
    const link=await rahimRow.getByRole('link',{name:'WhatsApp'}).getAttribute('href');
    const invite=decodeURIComponent(link).match(/https?:\/\/\S+\?employee=[A-Za-z0-9_-]+/)[0];
    assert.ok(invite.startsWith(origin+'/?employee='),invite);
    await shot(owner,'payroll-owner');

    // The employee, on their own phone, opens the link and chooses a login.
    const employee=await session(390);
    await employee.goto(invite);
    await employee.getByRole('heading',{name:'Welcome, Rahim Uddin'}).waitFor();
    await employee.getByText('payroll_owner invited you').waitFor();
    await shot(employee,'payroll-join');
    await employee.getByLabel('Choose a username').fill('rahim_u');
    await employee.getByLabel('Choose a password',{exact:true}).fill('Rahim own password 2026!');
    await employee.getByRole('button',{name:'Create my login'}).click();
    {const saved=employee.getByRole('button',{name:'I saved it'});if(await saved.isVisible({timeout:3000}).catch(()=>false))await saved.click();}
    await employee.getByRole('heading',{name:'Payslips'}).waitFor();
    assert.equal(new URL(employee.url()).searchParams.get('employee'),null);
    const slip=employee.locator('.payslips li');
    assert.equal(await slip.count(),1);
    const text=await slip.first().innerText();
    assert.match(text,/25,000\.00/);assert.match(text,/5,000\.00/);assert.match(text,/30,000\.00/);assert.match(text,/Eid bonus/);
    // Nothing of the employer's: no sidebar, no other employee, no books.
    assert.equal(await employee.locator('nav').count(),0);
    assert.equal(await employee.getByText('Karim Hasan').count(),0);
    const own=await (await employee.request.get(origin+'/api/payroll')).json();
    assert.equal(own.employees.length,0);assert.equal(own.payments.length,0);
    await shot(employee,'payroll-employee');

    // The link works once; the owner sees the joined username.
    const again=await session(390);
    await again.goto(invite);
    await again.getByText('This invite link has expired or was already used').waitFor();
    await owner.reload();
    await openPage(owner,'Payroll');
    await owner.getByText('@rahim_u').waitFor();

    // The employee signs out and back in with the password they chose.
    await employee.getByRole('button',{name:'Sign out'}).click();
    await employee.locator('.auth-tabs').getByRole('button',{name:'Sign in'}).click();
    await employee.locator('#username').fill('rahim_u');
    await employee.locator('#password').fill('Rahim own password 2026!');
    await employee.locator('form button.full-width').click();
    await employee.getByRole('heading',{name:'Payslips'}).waitFor();
    console.log('PASS payroll: owner pays salary and bonus, employee joins by private link and sees only their own payslips');
  }finally{
    await browser?.close();
    server.kill('SIGTERM');
  }
})().catch(error=>{console.error(error);process.exit(1);});
