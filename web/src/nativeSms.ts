/** Payment SMS on the owner's own phone (Android app only): bKash, Nagad, Rocket and bank messages become wallet
 * payments to review. Only messages from wallet and bank senders are read, and only after the owner turns it on. */
import {registerPlugin,type PluginListenerHandle} from '@capacitor/core';
import {ApiError} from './api';

type Message={id?:string;sender:string;body:string;date:number};
type SmsPlugin={
  status():Promise<{granted:boolean}>;
  requestAccess():Promise<{granted:boolean}>;
  read(options:{since:number;limit?:number}):Promise<{messages:Message[]}>;
  addListener(event:'walletSms',listener:(message:Message)=>void):Promise<PluginListenerHandle>;
};
const Sms=registerPlugin<SmsPlugin>('CapzoryxSms');
type Request=<T,>(path:string,body?:unknown)=>Promise<T>;
type Scope={business_id:string;user_id:string};
type Saved={enabled:boolean;since:number};
const FIRST_LOOK_BACK=7*24*3600*1000;  // when first turned on, last week's payments are picked up too

const key=(scope:Scope)=>`capzoryx-sms:${scope.business_id}:${scope.user_id}`;
export function smsSettings(scope:Scope):Saved{
  try{const v=JSON.parse(localStorage.getItem(key(scope))||'null') as Saved|null;if(v&&typeof v.since==='number')return v;}catch{/* fall through */}
  return {enabled:false,since:0};
}
function save(scope:Scope,value:Saved){try{localStorage.setItem(key(scope),JSON.stringify(value));}catch{/* the next sync starts again from the last week */}}

export async function smsAccess():Promise<boolean>{
  try{return (await Sms.status()).granted;}catch{return false;}
}

export async function enableSms(scope:Scope):Promise<boolean>{
  const {granted}=await Sms.requestAccess();
  if(!granted)return false;
  const current=smsSettings(scope);
  save(scope,{enabled:true,since:current.since||Date.now()-FIRST_LOOK_BACK});
  return true;
}
export function disableSms(scope:Scope){save(scope,{...smsSettings(scope),enabled:false});}

async function send(request:Request,scope:Scope,message:Message):Promise<boolean>{
  try{
    await request('/payments/share',{text:message.body.slice(0,2000),expected_business_id:scope.business_id,expected_user_id:scope.user_id,source:'sms'});
    return true;
  }catch(error){
    // Already added, not a payment (OTP, offer, balance) or unreadable: skip it and keep going.
    if(error instanceof ApiError&&(error.status===409||error.status===422))return false;
    throw error;
  }
}

let running:Promise<number>|null=null;
/** Sends new payment messages since the last sync. Returns how many became payments to review. */
export function syncSms(request:Request,scope:Scope):Promise<number>{
  if(running)return running;
  running=(async()=>{
    const saved=smsSettings(scope);
    if(!saved.enabled||!(await smsAccess()))return 0;
    const {messages}=await Sms.read({since:saved.since,limit:150});
    let added=0,newest=saved.since;
    for(const message of [...messages].sort((a,b)=>a.date-b.date)){
      if(await send(request,scope,message))added++;
      newest=Math.max(newest,message.date);
      save(scope,{enabled:true,since:newest});  // progress survives a dropped connection
    }
    return added;
  })().finally(()=>{running=null;});
  return running;
}

/** While the app is open, a new payment SMS is added at once. */
export async function listenForSms(request:Request,scope:Scope,added:(count:number)=>void):Promise<()=>void>{
  const handle=await Sms.addListener('walletSms',message=>{
    if(!smsSettings(scope).enabled)return;
    void send(request,scope,message).then(ok=>{if(ok){save(scope,{enabled:true,since:Math.max(smsSettings(scope).since,message.date)});added(1);}}).catch(()=>{});
  });
  return ()=>void handle.remove();
}
