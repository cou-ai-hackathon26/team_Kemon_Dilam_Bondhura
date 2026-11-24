import {useEffect,useMemo,useRef,useState} from 'react';
import {money} from './api';
import {t,T} from './i18n';

type Request=<R,>(path:string,body?:unknown)=>Promise<R>;
type Industry={key:string;label:string;niches:string[];words:string};
type Catalog={industries:Industry[];business_types:string[];channels:string[];goals:string[];units:string[]};
type Product={id:string;name:string;category:string;variant:string;brand:string;unit:string;quantity:string|null;cost_minor:number|null;price_minor:number|null;expense_minor?:number|null;market_key:string|null};
type Profile={identity?:Record<string,unknown>;operations?:Record<string,unknown>;goals?:Record<string,unknown>;consent?:Record<string,unknown>};
// Server sentences shown in the list of missing details, listed so they are translated.
export const GAP_REASONS=[T('Business name, industry and location are needed before anything else.'),
  T('Add the products or services you sell so market prices can be matched to them.'),T('Name a few competitors you know so they can be compared first.'),
  T('Monthly revenue and expenses are needed for the profit and growth estimates.'),T('Choose your goals so recommendations are ranked for you.')];
export type BusinessView={profile:Profile;step:number;completed:boolean;products:Product[];gaps:{field:string;why:string}[];currency:string;niche_category:string|null};

const STEPS=[T('Business'),T('Products'),T('Operations'),T('Goals'),T('Permissions')];
const SETUP_STEPS=[0,1,4];
const CITIES=['Dhaka','Mirpur, Dhaka','Uttara, Dhaka','Dhanmondi, Dhaka','Gulshan, Dhaka','Mohammadpur, Dhaka','Old Dhaka','Savar','Gazipur','Narayanganj','Chattogram','Cumilla','Sylhet','Khulna','Rajshahi','Barishal','Rangpur','Mymensingh','Bogura','Jashore','Cox\'s Bazar','Feni','Noakhali','Tangail','Dinajpur'];  // new owners answer three short steps; operations and goals details live on the My business page
const FINISH=T('What Capzoryx may do');
const KEYS=['identity','products','operations','goals','consent'];
const TYPE_LABEL:Record<string,string>={retail:T('Retail'),wholesale:T('Wholesale'),manufacturing:T('Manufacturing'),service:T('Service'),distribution:T('Distribution'),online:T('Online')};
const CHANNEL_LABEL:Record<string,string>={shop:T('Shop or showroom'),facebook:T('Facebook page'),website:T('Website'),marketplace:T('Marketplace (Daraz etc.)'),whatsapp:T('WhatsApp'),phone:T('Phone orders'),wholesale:T('Wholesale buyers'),field_sales:T('Field sales')};
const GOAL_LABEL:Record<string,string>={profit:T('Increase profit'),sales:T('Grow sales'),costs:T('Reduce costs'),compete:T('Compete better'),new_markets:T('Reach new markets'),inventory:T('Manage stock better'),protect_capital:T('Protect capital from price swings'),retention:T('Keep customers coming back'),grow_lean:T('Grow without much new investment')};
const MONEY_FIELDS:[string,string][]=[['monthly_revenue_minor',T('Monthly sales')],['monthly_expenses_minor',T('Monthly expenses')],['monthly_profit_minor',T('Monthly profit')],['working_capital_minor',T('Working capital now')],['inventory_value_minor',T('Stock value now')],['investment_minor',T('Starting investment')],['growth_budget_minor',T('Budget for growth')]];

const toMinor=(v:string)=>{const n=Number(v.replace(/,/g,''));return v.trim()&&Number.isFinite(n)?Math.round(n*100):null;};
const fromMinor=(v:unknown)=>typeof v==='number'?String(v/100):'';
const list=(v:string)=>v.split(/[,\n]/).map(x=>x.trim()).filter(Boolean);
const joined=(v:unknown)=>Array.isArray(v)?v.join(', '):'';
const str=(v:unknown)=>typeof v==='string'?v:'';
const errorText=(e:unknown)=>e instanceof Error?e.message:t('Request failed.');

function suggestIndustry(niche:string,industries:Industry[]){
  const words=niche.toLowerCase().split(/[^\p{L}\p{M}\p{N}]+/u).filter(w=>w.length>2);
  let best:Industry|null=null,score=0;
  for(const ind of industries){
    const vocab=(ind.label+' '+ind.niches.join(' ')+' '+ind.words).toLowerCase();
    const hits=words.filter(w=>vocab.includes(w)).length;
    if(hits>score){best=ind;score=hits;}
  }
  return best;
}

/** Business setup: five short steps that each save on their own. `edit` opens any step from Settings. */
export function BusinessSetup({request,mode,onDone,onLater}:{request:Request;mode:'setup'|'edit';onDone:(view:BusinessView)=>void;onLater?:()=>void}){
  const [view,setView]=useState<BusinessView|null>(null);
  const [catalog,setCatalog]=useState<Catalog|null>(null);
  const [step,setStep]=useState(-1);
  const [busy,setBusy]=useState(false);
  const [error,setError]=useState('');
  const [saved,setSaved]=useState('');
  const heading=useRef<HTMLHeadingElement>(null);
  const overlay=useRef<HTMLDivElement>(null);
  useEffect(()=>{  // a modal: Tab and screen readers stay inside it until it closes
    if(mode!=='setup')return;
    const others=[...document.querySelectorAll<HTMLElement>('.shell > *, #workspace > *')].filter(el=>!el.contains(overlay.current)&&!el.querySelector('.biz-setup-overlay'));
    others.forEach(el=>{el.inert=true;});
    return()=>others.forEach(el=>{el.inert=false;});
  },[mode,view]);
  useEffect(()=>{void Promise.all([request<BusinessView>('/business'),request<Catalog>('/business/catalog')]).then(([v,c])=>{
    setView(v);setCatalog(c);setStep(mode==='edit'?0:v.step===0?-1:v.step>=2?4:v.step);}).catch(e=>setError(errorText(e)));},[]);// eslint-disable-line react-hooks/exhaustive-deps
  useEffect(()=>{heading.current?.focus();},[step]);
  async function save(name:string,body:unknown,next=true){
    setBusy(true);setError('');setSaved('');
    try{
      const v=await request<BusinessView>('/business/step/'+name,body);setView(v);
      if(mode==='edit'){setSaved(t('Saved.'));return;}
      if(!next)return;
      if(step===4){const done=await request<BusinessView>('/business/complete',{});onDone(done);}else setStep(SETUP_STEPS[SETUP_STEPS.indexOf(step)+1]??4);
    }catch(e){setError(errorText(e));}finally{setBusy(false);}
  }
  if(!view||!catalog)return <div ref={overlay} className={mode==='setup'?'biz-setup-overlay':undefined}><section className="panel biz-setup" role="status">{error||t('Loading…')}</section></div>;
  const body=step===-1?<Welcome start={()=>setStep(0)} later={onLater}/>:<>
    <div className="biz-progress" aria-label={t('Step {n} of {total}',{n:(mode==='setup'?SETUP_STEPS.indexOf(step):step)+1,total:mode==='setup'?3:5})}>
      {(mode==='setup'?SETUP_STEPS:[0,1,2,3,4]).map((i,n)=>{const s=mode==='setup'&&i===4?FINISH:STEPS[i];return <button key={s} type="button" className={i===step?'active':i<view.step?'done':''} disabled={mode==='setup'&&i>view.step&&!(i===4&&view.step>=1)}
        aria-current={i===step?'step':undefined} onClick={()=>setStep(i)}><span>{n+1}</span>{t(s)}</button>;})}
    </div>
    <h2 tabIndex={-1} ref={heading}>{t(mode==='setup'&&step===4?FINISH:STEPS[step])}</h2>
    {step===0&&<IdentityStep key={'i'+view.step} catalog={catalog} value={view.profile.identity||{}} busy={busy} mode={mode} submit={b=>void save('identity',b)}/>}
    {step===1&&<ProductsStep view={view} catalog={catalog} request={request} setView={setView} busy={busy} mode={mode} next={()=>void save('products',{})}/>}
    {step===2&&<OperationsStep catalog={catalog} value={view.profile.operations||{}} currency={view.currency} busy={busy} mode={mode} submit={b=>void save('operations',b)}/>}
    {step===3&&<GoalsStep catalog={catalog} value={view.profile.goals||{}} currency={view.currency} busy={busy} mode={mode} submit={b=>void save('goals',b)}/>}
    {step===4&&<ConsentStep value={view.profile.consent||{}} goals={null} busy={busy} mode={mode}
      submit={(b,goals)=>void (async()=>{if(goals?.length){try{await request('/business/step/goals',{goals});}catch{/* goals can be added later */}}await save('consent',b);})()}/>}
    {error&&<p className="error" role="alert">{error}</p>}
    {saved&&<p className="success" role="status">{saved}</p>}
    {mode==='setup'&&<div className="biz-nav">
      {step>0&&<button type="button" className="ghost" disabled={busy} onClick={()=>setStep(mode==='setup'?SETUP_STEPS[Math.max(0,SETUP_STEPS.indexOf(step)-1)]:step-1)}>{t('Back')}</button>}
      {onLater&&<button type="button" className="link" onClick={onLater}>{t('Finish later')}</button>}
    </div>}
    {mode==='edit'&&!view.completed&&!!view.profile.identity&&<button type="button" className="secondary" disabled={busy} onClick={()=>void (async()=>{setBusy(true);setError('');
      try{const done=await request<BusinessView>('/business/complete',{});setView(done);setSaved(t('Your business profile is saved.'));onDone(done);}catch(e){setError(errorText(e));}finally{setBusy(false);}})()}>{t('Finish setup')}</button>}
    {mode==='edit'&&!!view.gaps.length&&<details className="biz-gaps"><summary>{t('{n} things would make the analysis more accurate',{n:view.gaps.length})}</summary>
      <ul>{view.gaps.map(g=><li key={g.field}>{t(g.why)}</li>)}</ul></details>}
  </>;
  return mode==='setup'?<div ref={overlay} className="biz-setup-overlay" role="dialog" aria-modal="true" aria-label={t('Set up your business')}><section className="panel biz-setup">{body}</section></div>
    :<section className="panel biz-setup biz-edit">{body}</section>;
}

function Welcome({start,later}:{start:()=>void;later?:()=>void}){
  return <div className="biz-welcome">
    <p className="eyebrow">{t('Your business assistant')}</p>
    <h1>{t('Tell us about your business once')}</h1>
    <p>{t('What you sell, where you sell it and what you want to achieve. Capzoryx uses this to follow your market, compare competitors, warn you about real risk using your own costs, and suggest next steps that fit your budget.')}</p>
    <ul className="biz-why">
      <li>{t('About 3 minutes. Only the first step is required; you can skip any numbers you do not know.')}</li>
      <li>{t('Each step is saved, so you can stop and continue later.')}</li>
      <li>{t('Your details are encrypted and visible only to the owner of this workspace.')}</li>
    </ul>
    <div className="actions"><button onClick={start}>{t('Set up my business')}</button>{later&&<button className="secondary" onClick={later}>{t('Not now')}</button>}</div>
  </div>;
}

function Chips({options,labels,value,onChange}:{options:string[];labels:Record<string,string>;value:string[];onChange:(v:string[])=>void}){
  return <div className="chips">{options.map(o=><label key={o} className={value.includes(o)?'chip on':'chip'}>
    <input type="checkbox" checked={value.includes(o)} onChange={()=>onChange(value.includes(o)?value.filter(x=>x!==o):[...value,o])}/>{t(labels[o]||o)}</label>)}</div>;
}

function Submit({busy,mode,last}:{busy:boolean;mode:'setup'|'edit';last?:boolean}){
  return <button className="biz-submit" disabled={busy}>{busy?t('Saving…'):mode==='edit'?t('Save'):last?t('Finish setup'):t('Save and continue')}</button>;
}

function IdentityStep({catalog,value,busy,mode,submit}:{catalog:Catalog;value:Record<string,unknown>;busy:boolean;mode:'setup'|'edit';submit:(b:unknown)=>void}){
  const [f,setF]=useState({name:str(value.name),industry:str(value.industry),niche:str(value.niche),country:str(value.country)||'Bangladesh',city:str(value.city),
    website:str(value.website),facebook:str(value.facebook),instagram:str(value.instagram),store:str(value.store),year:value.year_established?String(value.year_established):'',description:str(value.description)});
  const [types,setTypes]=useState<string[]>(Array.isArray(value.business_types)?value.business_types as string[]:['retail']);
  const set=(k:keyof typeof f)=>(e:{target:{value:string}})=>setF({...f,[k]:e.target.value});
  const guess=useMemo(()=>f.niche.length>2?suggestIndustry(f.niche,catalog.industries):null,[f.niche,catalog]);
  const niches=useMemo(()=>{const chosen=catalog.industries.find(i=>i.key===f.industry);return chosen?.niches.length?chosen.niches:catalog.industries.flatMap(i=>i.niches);},[f.industry,catalog]);
  const [more,setMore]=useState(mode==='edit'&&!!(f.website||f.facebook||f.description));
  const industry=f.industry||guess?.key||'';
  const options=catalog.industries.find(i=>i.key===industry)?.niches||[];
  const [ownNiche,setOwnNiche]=useState(!!f.niche&&!options.includes(f.niche));
  const [ownCity,setOwnCity]=useState(!!f.city&&!CITIES.includes(f.city));
  // Choices instead of typing: pick the industry, then what you do and where from short lists ("Other" lets you type).
  return <form onSubmit={e=>{e.preventDefault();submit({name:f.name,industry:industry||'other',niche:f.niche,business_types:types.length?types:['retail'],country:f.country||'Bangladesh',city:f.city,website:f.website,
    facebook:f.facebook,instagram:f.instagram,store:f.store,year_established:f.year?Number(f.year):null,description:f.description});}}>
    <label>{t('Business name')}<input required minLength={2} maxLength={80} value={f.name} onChange={set('name')} placeholder={t('e.g. Rahman Traders')}/></label>
    <div className="form-row">
      <label>{t('Industry')}<select required value={industry} onChange={e=>{setF({...f,industry:e.target.value,niche:''});setOwnNiche(false);}}>
        <option value="" disabled>{t('Choose…')}</option>
        {catalog.industries.map(i=><option key={i.key} value={i.key}>{t(i.label)}</option>)}</select></label>
      <label>{t('What do you sell or do?')}{ownNiche||!options.length
        ?<input required minLength={2} maxLength={80} value={f.niche} onChange={set('niche')} placeholder={t('e.g. Women\'s clothing shop')}/>
        :<select required value={f.niche} onChange={e=>{if(e.target.value==='__own'){setOwnNiche(true);setF({...f,niche:''});}else setF({...f,niche:e.target.value});}}>
          <option value="" disabled>{t('Choose…')}</option>
          {options.map(n=><option key={n} value={n}>{t(n)}</option>)}
          <option value="__own">{t('Something else (type it)')}</option></select>}</label>
    </div>
    <div className="form-row">
      <label>{t('City or area')}{ownCity
        ?<input required minLength={2} maxLength={80} value={f.city} onChange={set('city')} placeholder={t('e.g. Mirpur, Dhaka')}/>
        :<select required value={f.city} onChange={e=>{if(e.target.value==='__own'){setOwnCity(true);setF({...f,city:''});}else setF({...f,city:e.target.value});}}>
          <option value="" disabled>{t('Choose…')}</option>
          {CITIES.map(c=><option key={c} value={c}>{t(c)}</option>)}
          <option value="__own">{t('Another place (type it)')}</option></select>}</label>
      <fieldset className="biz-field"><legend>{t('How do you sell?')}</legend><Chips options={catalog.business_types} labels={TYPE_LABEL} value={types} onChange={setTypes}/></fieldset>
    </div>
    <details className="biz-more" open={more} onToggle={e=>setMore((e.target as HTMLDetailsElement).open)}><summary>{t('Website, pages and description (optional)')}</summary>
      <div className="form-row">
        <label>{t('Website')}<input inputMode="url" maxLength={200} value={f.website} onChange={set('website')} placeholder="example.com"/></label>
        <label>{t('Facebook page')}<input inputMode="url" maxLength={200} value={f.facebook} onChange={set('facebook')} placeholder="facebook.com/yourpage"/></label>
        <label>{t('Instagram')}<input inputMode="url" maxLength={200} value={f.instagram} onChange={set('instagram')}/></label>
      </div>
      <div className="form-row">
        <label>{t('Online store')}<input inputMode="url" maxLength={200} value={f.store} onChange={set('store')}/></label>
        <label>{t('Year started')}<input inputMode="numeric" maxLength={4} value={f.year} onChange={set('year')}/></label>
        <label>{t('Country')}<input minLength={2} maxLength={60} value={f.country} onChange={set('country')}/></label>
      </div>
      <label>{t('Short description')}<textarea maxLength={600} rows={3} value={f.description} onChange={set('description')}/></label>
    </details>
    <Submit busy={busy} mode={mode}/>
  </form>;
}

function ProductsStep({view,catalog,request,setView,busy,mode,next}:{view:BusinessView;catalog:Catalog;request:Request;setView:(v:BusinessView)=>void;busy:boolean;mode:'setup'|'edit';next:()=>void}){
  const blank={name:'',category:'',variant:'',unit:'',quantity:'',cost:'',price:'',expense:''};
  const [f,setF]=useState(blank);
  const [editing,setEditing]=useState<string|null>(null);
  const [working,setWorking]=useState(false);
  const [note,setNote]=useState('');
  const [error,setError]=useState('');
  const c=view.currency;
  const set=(k:keyof typeof f)=>(e:{target:{value:string}})=>setF({...f,[k]:e.target.value});
  async function act(fn:()=>Promise<BusinessView>,done?:string){
    setWorking(true);setError('');setNote('');
    try{setView(await fn());if(done)setNote(done);}catch(e){setError(errorText(e));}finally{setWorking(false);}
  }
  const bad=(['quantity','cost','price','expense'] as const).filter(k=>f[k].trim()&&!Number.isFinite(Number(f[k].replace(/,/g,''))));
  const payload=()=>({name:f.name,category:f.category,variant:f.variant,unit:f.unit,quantity:f.quantity.trim()?f.quantity.replace(/,/g,''):null,cost_minor:toMinor(f.cost),price_minor:toMinor(f.price),expense_minor:toMinor(f.expense)});
  return <div>
    <p className="muted">{t('Add what you sell. Purchase cost and selling price are optional, but they let Capzoryx work out your real profit and break-even price.')}</p>
    {!!view.products.length&&<div className="table-wrap"><table className="biz-products"><thead><tr><th>{t('Product')}</th><th>{t('Quantity')}</th><th>{t('Cost')}</th><th>{t('Price')}</th><th/></tr></thead>
      <tbody>{view.products.map(p=><tr key={p.id}><td><b>{p.name}</b><small>{[p.category,p.variant].filter(Boolean).join(' · ')}{p.market_key&&<span className="pill">{t('Live metal price')}</span>}</small></td>
        <td>{p.quantity?`${p.quantity} ${p.unit}`:p.unit||'—'}</td><td className="amount">{p.cost_minor==null?<span className="muted">{t('Not set')}</span>:money(p.cost_minor,c)}</td>
        <td className="amount">{p.price_minor==null?<span className="muted">{t('Not set')}</span>:money(p.price_minor,c)}</td>
        <td><button type="button" className="ghost small-button" onClick={()=>{setEditing(p.id);setF({name:p.name,category:p.category,variant:p.variant,unit:p.unit,quantity:p.quantity||'',cost:fromMinor(p.cost_minor),price:fromMinor(p.price_minor),expense:fromMinor(p.expense_minor)});}}>{t('Edit')}</button>
          <button type="button" className="ghost small-button" disabled={working} onClick={()=>void act(()=>request<BusinessView>(`/business/products/${p.id}/delete`,{}))}>{t('Remove')}</button></td></tr>)}</tbody></table></div>}
    <form className="biz-product-form" onSubmit={e=>{e.preventDefault();if(bad.length){setError(t('Use numbers only for quantity, cost and price (for example 1250 or 1,250.50).'));return;}void act(()=>request<BusinessView>(editing?`/business/products/${editing}`:'/business/products',payload()),editing?t('Product updated.'):t('{name} added.',{name:f.name})).then(()=>{setF(blank);setEditing(null);});}}>
      <div className="form-row">
        <label>{t('Product or service')}<input required maxLength={80} value={f.name} onChange={set('name')} placeholder={t('e.g. Cotton saree')}/></label>
        <label>{t('Category')}<input maxLength={80} value={f.category} onChange={set('category')} placeholder={t('e.g. Sarees')}/></label>
        <label>{t('Unit')}<input maxLength={20} list="biz-units" value={f.unit} onChange={set('unit')} placeholder={t('e.g. piece, kg, set')}/></label>
      </div>
      <datalist id="biz-units">{catalog.units.map(u=><option key={u} value={u}/>)}</datalist>
      <div className="form-row">
        <label>{t('Quantity in stock')}<input inputMode="decimal" value={f.quantity} onChange={set('quantity')}/></label>
        <label>{t('Purchase cost per unit ({currency})',{currency:c})}<input inputMode="decimal" value={f.cost} onChange={set('cost')} placeholder={t('optional')}/></label>
        <label>{t('Selling price per unit ({currency})',{currency:c})}<input inputMode="decimal" value={f.price} onChange={set('price')} placeholder={t('optional')}/></label>
      </div>
      <label>{t('Costs to sell one unit ({currency})',{currency:c})}<input inputMode="decimal" value={f.expense} onChange={set('expense')} placeholder={t('optional: delivery, packaging, commission, making charge')}/></label>
      <div className="actions"><button className="secondary" disabled={working||!f.name.trim()}>{editing?t('Update product'):t('Add product')}</button>
        {editing&&<button type="button" className="ghost" onClick={()=>{setEditing(null);setF(blank);}}>{t('Cancel')}</button>}
        <label className="button-like secondary">{t('Import Excel/CSV')}<input type="file" accept=".csv,text/csv" hidden onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(!file)return;
          void file.text().then(text=>act(async()=>{const r=await request<BusinessView&{added:number;skipped:{row:number;reason:string}[]}>('/business/import',{csv:text});
            setNote(t('{added} added, {skipped} skipped.',{added:r.added,skipped:r.skipped.length}));return r;}));}}/></label></div>
      <small className="muted">{t('For Excel, save the sheet as CSV first. Columns: Product, Category, Unit, Quantity, Cost, Price, Expense.')}</small>
    </form>
    {note&&<p className="success" role="status">{note}</p>}
    {error&&<p className="error" role="alert">{error}</p>}
    {mode==='setup'&&<button type="button" className="biz-submit" disabled={busy||working} onClick={next}>{view.products.length?t('Save and continue'):t('Skip for now')}</button>}
  </div>;
}

function OperationsStep({catalog,value,currency,busy,mode,submit}:{catalog:Catalog;value:Record<string,unknown>;currency:string;busy:boolean;mode:'setup'|'edit';submit:(b:unknown)=>void}){
  const [modeOf,setMode]=useState(str(value.mode));
  const [channels,setChannels]=useState<string[]>(Array.isArray(value.channels)?value.channels as string[]:[]);
  const [f,setF]=useState({customers:joined(value.customers),locations:joined(value.locations),competitors:joined(value.competitors),suppliers:joined(value.suppliers),branches:value.branches==null?'':String(value.branches)});
  const [expenses,setExpenses]=useState<{name:string;amount:string}[]>(Array.isArray(value.expenses)?(value.expenses as {name:string;monthly_minor:number}[]).map(x=>({name:x.name,amount:fromMinor(x.monthly_minor)})):[]);
  const set=(k:keyof typeof f)=>(e:{target:{value:string}})=>setF({...f,[k]:e.target.value});
  return <form onSubmit={e=>{e.preventDefault();submit({mode:modeOf||null,channels,customers:list(f.customers),locations:list(f.locations),competitors:list(f.competitors),suppliers:list(f.suppliers),
    branches:f.branches?Number(f.branches):null,expenses:expenses.filter(x=>x.name.trim()&&toMinor(x.amount)!=null).map(x=>({name:x.name.trim(),monthly_minor:toMinor(x.amount)}))});}}>
    <fieldset className="biz-field"><legend>{t('Where do you sell?')}</legend><div className="chips">{(['offline','online','hybrid'] as const).map(m=><label key={m} className={modeOf===m?'chip on':'chip'}>
      <input type="radio" name="biz-mode" checked={modeOf===m} onChange={()=>setMode(m)}/>{m==='offline'?t('In person'):m==='online'?t('Online'):t('Both')}</label>)}</div></fieldset>
    <fieldset className="biz-field"><legend>{t('Sales channels')}</legend><Chips options={catalog.channels} labels={CHANNEL_LABEL} value={channels} onChange={setChannels}/></fieldset>
    <div className="form-row">
      <label>{t('Who are your customers?')}<input value={f.customers} onChange={set('customers')} placeholder={t('e.g. families, students, offices, shops')}/></label>
      <label>{t('Areas you serve')}<input value={f.locations} onChange={set('locations')} placeholder={t('e.g. Dhanmondi, Mirpur')}/></label>
      <label>{t('Branches')}<input inputMode="numeric" value={f.branches} onChange={set('branches')}/></label>
    </div>
    <label>{t('Competitors you know')}<input value={f.competitors} onChange={set('competitors')} placeholder={t('Names separated by commas')}/></label>
    <label>{t('Main suppliers (optional)')}<input value={f.suppliers} onChange={set('suppliers')}/></label>
    <fieldset className="biz-field"><legend>{t('Regular monthly expenses ({currency})',{currency})}</legend>
      {expenses.map((x,i)=><div key={i} className="biz-expense"><input aria-label={t('Expense')} value={x.name} placeholder={t('e.g. Shop rent')} onChange={e=>setExpenses(expenses.map((y,j)=>j===i?{...y,name:e.target.value}:y))}/>
        <input aria-label={t('Amount')} inputMode="decimal" value={x.amount} onChange={e=>setExpenses(expenses.map((y,j)=>j===i?{...y,amount:e.target.value}:y))}/>
        <button type="button" className="ghost small-button" aria-label={t('Remove')} onClick={()=>setExpenses(expenses.filter((_,j)=>j!==i))}>×</button></div>)}
      <button type="button" className="secondary small-button" onClick={()=>setExpenses([...expenses,{name:'',amount:''}])}>{t('Add an expense')}</button></fieldset>
    <Submit busy={busy} mode={mode}/>
  </form>;
}

function GoalsStep({catalog,value,currency,busy,mode,submit}:{catalog:Catalog;value:Record<string,unknown>;currency:string;busy:boolean;mode:'setup'|'edit';submit:(b:unknown)=>void}){
  const [goals,setGoals]=useState<string[]>(Array.isArray(value.goals)?value.goals as string[]:[]);
  const [nums,setNums]=useState<Record<string,string>>(Object.fromEntries(MONEY_FIELDS.map(([k])=>[k,fromMinor(value[k])])));
  const [challenges,setChallenges]=useState(str(value.challenges));
  return <form onSubmit={e=>{e.preventDefault();submit({goals,challenges,...Object.fromEntries(MONEY_FIELDS.map(([k])=>[k,toMinor(nums[k])]))});}}>
    <fieldset className="biz-field"><legend>{t('What matters most right now?')}</legend><Chips options={catalog.goals} labels={GOAL_LABEL} value={goals} onChange={setGoals}/></fieldset>
    <details className="biz-more" open={MONEY_FIELDS.some(([k])=>nums[k])}><summary>{t('Your numbers (optional, rough is fine)')}</summary>
      <div className="biz-money">{MONEY_FIELDS.map(([k,label])=><label key={k}>{t(label)} ({currency})<input inputMode="decimal" value={nums[k]} onChange={e=>setNums({...nums,[k]:e.target.value})}/></label>)}</div>
    </details>
    <label>{t('Biggest challenge right now (optional)')}<textarea rows={2} maxLength={600} value={challenges} onChange={e=>setChallenges(e.target.value)}/></label>
    <Submit busy={busy} mode={mode}/>
  </form>;
}

function ConsentStep({value,goals,busy,mode,submit}:{value:Record<string,unknown>;goals:string[]|null;busy:boolean;mode:'setup'|'edit';submit:(b:unknown,goals?:string[])=>void}){
  // On by default so the assistant starts working straight away; every switch can be turned off here or later.
  const on=(k:string)=>value[k]===undefined?true:!!value[k];
  const [f,setF]=useState({personalized_ai:on('personalized_ai'),external_research:on('external_research'),monitoring:on('monitoring'),alerts:on('alerts')});
  const [chosen,setChosen]=useState<string[]>([]);
  const [limits,setLimits]=useState({warn_cushion_pct:String(value.warn_cushion_pct??5),move_pct:String(value.move_pct??3)});
  const rows:[keyof typeof f,string,string][]=[
    ['personalized_ai',T('Use my business details for personal advice'),T('The assistant reads your profile and products to tailor answers. Nothing is shared with other users.')],
    ['external_research',T('Research my market and competitors online'),T('Only public information is searched: your industry, area and competitor names. Your costs and sales are never sent.')],
    ['monitoring',T('Keep watching market prices for me'),T('Prices linked to your products are checked in the background, even when you are signed out.')],
    ['alerts',T('Send me important alerts automatically'),T('Warnings go to the channels you turn on (Telegram, phone, email) without asking each time. Nothing is ever bought, sold or paid automatically.')],
  ];
  return <form onSubmit={e=>{e.preventDefault();submit({...f,warn_cushion_pct:Number(limits.warn_cushion_pct)||5,move_pct:Number(limits.move_pct)||3},chosen);}}>
    {goals&&<fieldset className="biz-field"><legend>{t('What matters most right now? (optional)')}</legend><Chips options={goals} labels={GOAL_LABEL} value={chosen} onChange={setChosen}/></fieldset>}
    <p className="muted">{t('Your details are stored encrypted in this workspace. You can change these choices any time on the My business page.')}</p>
    {rows.map(([k,title,help])=><label key={k} className="switch-row"><input type="checkbox" checked={f[k]} onChange={e=>setF({...f,[k]:e.target.checked})}/>
      <span><b>{t(title)}</b><small>{t(help)}</small></span></label>)}
    {(f.monitoring||f.alerts)&&<div className="form-row biz-limits">
      <label>{t('Warn me when the price is within this % of break-even')}<input type="number" min={0.5} max={50} step={0.5} value={limits.warn_cushion_pct} onChange={e=>setLimits({...limits,warn_cushion_pct:e.target.value})}/></label>
      <label>{t('Warn me when the price falls by this % between checks')}<input type="number" min={0.5} max={50} step={0.5} value={limits.move_pct} onChange={e=>setLimits({...limits,move_pct:e.target.value})}/></label>
    </div>}
    {(f.monitoring||f.alerts)&&<p className="muted small-print">{t('Critical alerts (at or past break-even) are always sent, even in quiet hours. Connect Telegram in Settings to get them on your phone.')}</p>}
    <Submit busy={busy} mode={mode} last/>
  </form>;
}
