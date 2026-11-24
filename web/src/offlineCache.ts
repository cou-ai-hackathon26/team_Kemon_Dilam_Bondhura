/** Android app only: the last data each screen loaded, so the app still opens and shows the books without a
 * connection. Kept in the app's private storage, per signed-in user and workspace, and removed on sign-out. */
import {installedApp as nativeBuild} from './apiBase';
import type {Session} from './types';

const PREFIX='capzoryx-cache:';
const SESSION='capzoryx-app-session';
const MAX_ENTRY=600_000;
let scope='';

export function cacheScope(session:Pick<Session,'business_id'|'user_id'>|null){scope=session?`${session.user_id}:${session.business_id}`:'';}

export function remember(path:string,data:unknown){
  if(!nativeBuild||!scope)return;
  try{
    const text=JSON.stringify({at:Date.now(),data});
    if(text.length<=MAX_ENTRY)localStorage.setItem(PREFIX+scope+':'+path,text);
  }catch{/* storage full: the live data still works */}
}

export function recall<T>(path:string):{at:number;data:T}|null{
  if(!nativeBuild||!scope)return null;
  try{return JSON.parse(localStorage.getItem(PREFIX+scope+':'+path)||'null');}catch{return null;}
}

/** The account the app last opened (no password or token), so it can open offline. */
export function savedSession():Session|null{
  if(!nativeBuild)return null;
  try{const s=JSON.parse(localStorage.getItem(SESSION)||'null') as Session|null;return s&&s.user_id&&s.business_id?s:null;}catch{return null;}
}
export function keepSession(session:Session){
  if(!nativeBuild)return;
  const {recovery_code:_drop,...safe}=session as Session&{recovery_code?:string};void _drop;
  try{localStorage.setItem(SESSION,JSON.stringify(safe));}catch{/* optional */}
}
export function forgetAll(){
  try{for(const key of Object.keys(localStorage))if(key===SESSION||key.startsWith(PREFIX))localStorage.removeItem(key);}catch{/* optional */}
  scope='';
}
