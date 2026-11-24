import {useEffect,useState} from 'react';
import {Icon} from './Icon';
import {t,T} from './i18n';

const demos:{say:string;who:string;what:string;amount:string;kind:string}[]=[
  {say:'Rahim theke 5000 taka pelam',who:'Rahim',what:'Payment',amount:'+ BDT 5,000',kind:T('Money in')},
  {say:'I paid Rahman 5000 for freight yesterday',who:'Rahman',what:'Freight',amount:'BDT 5,000',kind:T('Money out')},
  {say:'Received 20,000 from Karim Traders',who:'Karim Traders',what:'Payment',amount:'+ BDT 20,000',kind:T('Money in')},
  {say:'Bill from Dhaka Packaging 12000 due in 10 days',who:'Dhaka Packaging',what:'Bill',amount:'BDT 12,000',kind:T('Unpaid supplier invoice')},
];
const still=()=>{try{return matchMedia('(prefers-reduced-motion: reduce)').matches;}catch{return false;}};

/** A looping, decorative demo: a sentence is typed, then the record Capzoryx reads from it appears. */
export function TypingDemo(){
  const [i,setI]=useState(0);
  const [n,setN]=useState(still()?999:0);
  const [done,setDone]=useState(still());
  const d=demos[i];
  useEffect(()=>{
    if(still())return;
    let id=0;
    if(n<d.say.length)id=window.setTimeout(()=>setN(n+1),n===0?500:38);
    else if(!done)id=window.setTimeout(()=>setDone(true),350);
    else id=window.setTimeout(()=>{setDone(false);setN(0);setI((i+1)%demos.length);},3200);
    return()=>window.clearTimeout(id);
  },[n,done,d.say.length,i]);
  return <div className="hero-demo" aria-hidden="true">
    <div className="hd-bar"><span className="hd-dot"/><span className="hd-dot"/><span className="hd-dot"/><b>{t('Assistant')}</b></div>
    <div className="hd-body">
      <p className="hd-user">{d.say.slice(0,n)}<span className="hd-caret"/></p>
      <div className={done?'hd-card show':'hd-card'}><span className="hd-badge">{t(d.kind)}</span><strong>{d.who}</strong><small>{d.what}</small><em>{d.amount}</em><span className="hd-add"><Icon name="check"/>{t('Add to my records')}</span></div>
    </div>
  </div>;
}
