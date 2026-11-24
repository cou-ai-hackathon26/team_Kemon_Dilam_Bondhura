import {useEffect,useMemo,useRef,useState,type KeyboardEvent} from 'react';
import {Icon} from './Icon';
import {t,T} from './i18n';

export type SearchPage='Overview'|'Business'|'Data'|'Invoices'|'Cash flow'|'Financing'|'Assistant'|'Approvals'|'Payroll'|'Audit'|'Settings';
type Entry={id:string;label:string;where:string;page?:SearchPage;settings?:string;target?:string;action?:string;icon:string;words:string};

// Every page and the features inside them, like a phone's settings search. `words` adds Banglish and Bangla terms.
const ENTRIES:Entry[]=[
  {id:'overview',label:T('Overview'),where:'',page:'Overview',icon:'Overview',words:'home dashboard balance hisab হিসাব'},
  {id:'records',label:T('Add records'),where:'',page:'Data',icon:'Data',words:'entry add jog likhbo এন্ট্রি যোগ'},
  {id:'invoices',label:T('Invoices'),where:'',page:'Invoices',icon:'Invoices',words:'bill bills due supplier বিল'},
  {id:'cash',label:T('Cash flow'),where:'',page:'Cash flow',icon:'Cash flow',words:'forecast outlook purbabhash পূর্বাভাস'},
  {id:'loans',label:T('Loans'),where:'',page:'Financing',icon:'Financing',words:'loan rin murabaha interest ঋণ লোন'},
  {id:'assistant',label:T('Assistant'),where:'',page:'Assistant',icon:'Assistant',words:'ai chat ask proshno প্রশ্ন'},
  {id:'approvals',label:T('Approvals'),where:'',page:'Approvals',icon:'Approvals',words:'approve payment onumodon অনুমোদন'},
  {id:'payroll',label:T('Payroll'),where:'',page:'Payroll',icon:'Payroll',words:'salary salaries bonus employee staff payslip beton bonas karmochari বেতন বোনাস কর্মচারী স্টাফ'},
  {id:'market',label:T('Your market'),where:T('Overview'),page:'Overview',target:'market',icon:'Cash flow',words:'market bajar bazar price dam dor stock gold sona competitor research বাজার দাম দর প্রতিযোগী'},
  {id:'activity',label:T('Activity'),where:'',page:'Audit',icon:'Audit',words:'history audit log itihash ইতিহাস'},
  {id:'settings',label:T('Settings'),where:'',page:'Settings',icon:'Settings',words:'setting options সেটিংস'},
  {id:'scan',label:T('Scan a bill'),where:T('Add records'),page:'Data',icon:'camera',words:'photo chobi receipt camera ছবি'},
  {id:'khata',label:T('Khata page photo'),where:T('Add records'),page:'Data',icon:'camera',words:'khata notebook page খাতা'},
  {id:'voice',label:T('Voice entry'),where:T('Add records'),page:'Data',icon:'pen',words:'voice mic speak bole ভয়েস'},
  {id:'offline',label:T('Offline capture'),where:T('Add records'),page:'Data',icon:'down',words:'offline internet net অফলাইন'},
  {id:'collection',label:T('Collection plan'),where:T('Cash flow'),page:'Cash flow',icon:'Cash flow',words:'collect gap shortfall aday ghatti আদায় ঘাটতি'},
  {id:'passport',label:T('Cash-flow passport'),where:T('Activity'),page:'Audit',icon:'check',words:'passport qr lender verify পাসপোর্ট'},
  {id:'telegram',label:'Telegram',where:T('Settings'),page:'Settings',settings:'Connections',target:'telegram',icon:'send',words:'telegram bot chat টেলিগ্রাম'},
  {id:'sms',label:T('Payment SMS'),where:T('Settings'),page:'Settings',settings:'Connections',target:'payment-sms',icon:'send',words:'sms bkash nagad rocket bank forward বিকাশ নগদ এসএমএস'},
  {id:'mcp',label:T('Read-only MCP access'),where:T('Settings'),page:'Settings',settings:'Connections',target:'mcp-access',icon:'Settings',words:'mcp token api ai tool'},
  {id:'business',label:T('My business'),where:T('My business'),page:'Business',icon:'Business',words:'business data submit upload file notes profile setup products industry niche competitors goals byabsa bebsa ব্যবসা পণ্য প্রতিযোগী'},
  {id:'profile',label:T('Profile and photo'),where:T('Settings'),page:'Settings',settings:'Profile',icon:'Settings',words:'profile name photo picture nam নাম ছবি'},
  {id:'language',label:T('Language'),where:T('Settings'),page:'Settings',settings:'Profile',icon:'globe',words:'language bhasha bangla english ভাষা বাংলা'},
  {id:'email',label:T('Email'),where:T('Settings'),page:'Settings',settings:'Profile',icon:'send',words:'email mail ইমেইল'},
  {id:'datasaver',label:T('Low-data mode'),where:T('Settings'),page:'Settings',settings:'Profile',icon:'down',words:'data saver low mb ডেটা'},
  {id:'smart',label:T('Smart assist'),where:T('Settings'),page:'Settings',settings:'Profile',icon:'spark',words:'ai smart cloud model'},
  {id:'password',label:T('Change password'),where:T('Settings'),page:'Settings',settings:'Security',icon:'Approvals',words:'password pass login পাসওয়ার্ড'},
  {id:'twostep',label:T('Two-step sign-in'),where:T('Settings'),page:'Settings',settings:'Security',icon:'Approvals',words:'2fa two step otp authenticator security নিরাপত্তা'},
  {id:'devices',label:T('Signed-in phones'),where:T('Settings'),page:'Settings',settings:'Security',icon:'Approvals',words:'device phone session sign out logout'},
  {id:'team',label:T('Invite someone'),where:T('Settings'),page:'Settings',settings:'Sharing',icon:'Settings',words:'team member invite share staff approver bookkeeper শেয়ার সদস্য'},
  {id:'reports',label:T('Reports and backup'),where:T('Settings'),page:'Settings',settings:'Data',icon:'download',words:'report pdf backup export download রিপোর্ট ব্যাকআপ'},
  {id:'clear',label:T('Clear a workspace'),where:T('Settings'),page:'Settings',settings:'Data',icon:'close',words:'clear delete reset remove workspace মুছুন'},
  {id:'ask',label:T('Ask your assistant'),where:'',action:'ask',icon:'spark',words:'ask question help prosno সাহায্য'},
  {id:'pdf',label:T('Download PDF'),where:'',action:'pdf',icon:'download',words:'pdf print report'},
  {id:'theme',label:T('Dark or light theme'),where:'',action:'theme',icon:'moon',words:'dark light theme night mode ডার্ক'},
  {id:'tour',label:T('Quick tutorial'),where:'',action:'tour',icon:'help',words:'tour tutorial guide learn shikhun শিখুন'},
  {id:'refresh',label:T('Refresh'),where:'',action:'refresh',icon:'refresh',words:'reload update sync'},
];

function score(entry:Entry,query:string){
  const label=t(entry.label).toLowerCase(),words=(entry.label+' '+entry.words).toLowerCase();
  if(label.startsWith(query))return 4;
  if(label.split(/\s+/).some(w=>w.startsWith(query)))return 3;
  if(words.split(/\s+/).some(w=>w.startsWith(query)))return 2;
  return label.includes(query)||words.includes(query)?1:0;
}

/** Sidebar search: suggestions appear as you type, and choosing one opens the exact page or setting. */
export function SidebarSearch({go,actions,hidden=[]}:{go:(page:SearchPage)=>void;actions:Record<string,()=>void>;hidden?:string[]}){
  const [query,setQuery]=useState('');
  const [active,setActive]=useState(0);
  const input=useRef<HTMLInputElement>(null);
  useEffect(()=>{
    const onKey=(e:globalThis.KeyboardEvent)=>{if((e.ctrlKey||e.metaKey)&&e.key.toLowerCase()==='k'){e.preventDefault();input.current?.focus();input.current?.select();}};
    window.addEventListener('keydown',onKey);return()=>window.removeEventListener('keydown',onKey);
  },[]);
  const results=useMemo(()=>{
    const q=query.trim().toLowerCase();
    if(!q)return [];
    return ENTRIES.filter(e=>!hidden.includes(e.id)).map(e=>({e,s:score(e,q)})).filter(x=>x.s>0).sort((a,b)=>b.s-a.s).slice(0,8).map(x=>x.e);
  },[query,hidden]);
  function open(entry?:Entry){
    if(!entry)return;
    setQuery('');input.current?.blur();
    if(entry.action){actions[entry.action]?.();return;}
    // Settings loads lazily, so the chosen tab is left for it to pick up when it first renders.
    if(entry.settings){try{sessionStorage.setItem('capzoryx-settings-tab',entry.settings);}catch{/* optional */}}
    if(entry.page)go(entry.page);
    window.setTimeout(()=>{
      if(entry.settings)window.dispatchEvent(new CustomEvent('capzoryx:settings-tab',{detail:entry.settings}));
      window.setTimeout(()=>{
        const target=entry.target&&document.getElementById(entry.target);
        if(!target)return;
        target.scrollIntoView({behavior:'smooth',block:'start'});
        target.classList.add('found');window.setTimeout(()=>target.classList.remove('found'),1600);
      },entry.target?250:0);
    },120);
  }
  const onKey=(e:KeyboardEvent)=>{
    if(e.key==='ArrowDown'){e.preventDefault();setActive(a=>Math.min(a+1,results.length-1));}
    else if(e.key==='ArrowUp'){e.preventDefault();setActive(a=>Math.max(a-1,0));}
    else if(e.key==='Enter'){e.preventDefault();open(results[active]);}
    else if(e.key==='Escape'){setQuery('');input.current?.blur();}
  };
  return <div className="side-search" role="search">
    <label className="side-search-box"><Icon name="search"/>
      <input ref={input} role="combobox" aria-autocomplete="list" value={query} placeholder={t('Search')} aria-label={t('Search pages and settings')} aria-expanded={!!results.length}
        aria-controls="side-search-results" onChange={e=>{setQuery(e.target.value);setActive(0);}} onKeyDown={onKey}/>
      {!query&&<kbd>Ctrl K</kbd>}</label>
    {!!query.trim()&&<ul id="side-search-results" className="side-search-results" role="listbox">
      {results.map((entry,i)=><li key={entry.id} role="option" aria-selected={i===active}>
        <button className={i===active?'active':undefined} onMouseEnter={()=>setActive(i)} onMouseDown={e=>e.preventDefault()} onClick={()=>open(entry)}>
          <Icon name={entry.icon}/><span><b>{t(entry.label)}</b>{entry.where&&<small>{t(entry.where)}</small>}</span></button></li>)}
      {!results.length&&<li className="side-search-empty">{t('No matches')}</li>}
    </ul>}
  </div>;
}
