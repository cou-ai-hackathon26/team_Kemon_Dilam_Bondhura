/* Exercises the real Bengali OCR workflow against a local throwaway server.
   Use a system with a Bengali font and Playwright Chromium. Generated examples are typeset. */
const {chromium}=require('../web/node_modules/playwright');
const crypto=require('node:crypto');
const assert=require('node:assert/strict');
const origin=new URL(process.env.KHATA_EVAL_URL||'http://127.0.0.1:4394');
if(!['127.0.0.1','localhost','[::1]'].includes(origin.hostname))throw new Error('Use an isolated local test server.');
const bengali=n=>String(n).replace(/\d/g,c=>'০১২৩৪৫৬৭৮৯'[Number(c)]);
const ascii=text=>text.normalize('NFC').replace(/[০-৯]/g,c=>String('০১২৩৪৫৬৭৮৯'.indexOf(c)));
const names=['সালমা','করিম','রুমি','মিনা'];
const kinds=['নগদ জমা','নগদ খরচ','বাকিতে বিক্রি','পাওনা আদায়'];

(async()=>{
  const browser=await chromium.launch({headless:true,...(process.env.CHROMIUM_PATH?{executablePath:process.env.CHROMIUM_PATH}:{})});
  try{
    const context=await browser.newContext({viewport:{width:1000,height:560}});
    const page=await context.newPage();page.setDefaultTimeout(60000);
    const signup=await context.request.post(new URL('/api/auth/signup',origin).href,{
      headers:{Origin:origin.origin,'X-SafeLedger-Client':'web'},data:{username:'khata_'+crypto.randomUUID().slice(0,8),password:'Generated notebook evaluation 53!'}});
    assert.equal(signup.status(),201);
    await page.goto(origin.href);
    const tour=page.locator('dialog[open]').getByRole('button',{name:/skip/i});
    await page.locator('nav [data-tour="Data"]').waitFor();
    if(await tour.isVisible())await tour.click();
    await page.locator('nav [data-tour="Data"]').click();await page.getByText('Photograph a khata page',{exact:true}).click();
    const paper=await context.newPage();let expected=0,matched=0,nonemptyRows=0;
    for(let sheet=0;sheet<10;sheet++){
      const rows=names.map((name,i)=>({amount:150+sheet*37+i*13,name,kind:kinds[(sheet+i)%4]}));
      const printedDay=String(1+sheet).padStart(2,'0')+'/11/2026';
      // Typography, rule colour and row spacing vary. Ground truth is never passed to the OCR component.
      await paper.setContent('<style>body{margin:0;background:#fffefa;color:#202632}.sheet{padding:45px;width:885px;font:'+
        (sheet%2?'32px "Noto Serif Bengali"':'34px "Noto Sans Bengali"')+'}.entry{padding:'+(8+sheet%3*3)+'px 0;border-bottom:1px solid #bed5e1;white-space:pre}</style><div class="sheet">'+
        rows.map(r=>'<div class="entry">'+bengali(printedDay)+'   '+r.name+'   '+r.kind+'   '+bengali(r.amount)+' টাকা</div>').join('')+'</div>');
      const png=await paper.locator('.sheet').screenshot();
      const upload=page.getByLabel('Khata page photo');
      await upload.setInputFiles({name:'synthetic-'+sheet+'.png',mimeType:'image/png',buffer:png});
      await page.getByText('Working on this page…',{exact:true}).waitFor({state:'visible'});
      await page.getByText('Working on this page…',{exact:true}).waitFor({state:'hidden'});
      const lines=(await page.getByRole('list',{name:'Notebook lines'}).locator('li small').allTextContents()).map(ascii);
      nonemptyRows+=lines.length;expected+=rows.length;
      const unused=new Set(lines.map((_,index)=>index));
      for(const r of rows){
        const found=[...unused].find(i=>new RegExp('(?<![0-9])'+r.amount+'(?![0-9])').test(lines[i]) && lines[i].includes(r.name));
        if(found!==undefined){matched++;unused.delete(found);}
      }
    }
    const bundle=await (await context.request.get(new URL('/api/workspace',origin).href)).json();
    assert.equal(bundle.snapshot.balance_minor,0);assert.equal(bundle.events.length,0);assert.equal(bundle.invoices.length,0);
    await page.getByRole('button',{name:'Add a missing line'}).click();
    const form=page.locator('form').filter({has:page.getByRole('button',{name:'Record this line'})});
    await form.getByLabel('Transaction type').selectOption('outflow');
    await form.getByLabel('Activity date').fill('2026-11-01');
    await form.getByLabel('Amount').fill('৭৩.২৫');
    await form.getByLabel('I checked this transaction against the notebook.').check();
    const response=page.waitForResponse(r=>new URL(r.url()).pathname==='/api/workspace/khata/confirm');
    await form.getByRole('button',{name:'Record this line'}).click();
    const posted=await response;assert.equal(posted.status(),201);
    const body=posted.request().postDataJSON();
    assert.equal(body.amount_minor,7325);assert.equal('source' in body,false);assert.equal('image' in body,false);
    const saved=await (await context.request.get(new URL('/api/workspace',origin).href)).json();
    assert.equal(saved.events.length,1);assert.equal(saved.snapshot.balance_minor,-7325);
    console.log(JSON.stringify({dataset:'synthetic typeset Bengali, not handwriting',pages:10,expected_lines:expected,
      ocr_candidate_lines:nonemptyRows,matched_name_and_amount_on_same_line:matched,recall:matched/expected,
      precision:nonemptyRows?matched/nonemptyRows:0,automatic_records:0,manually_reviewed_rows_recorded:1},null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
