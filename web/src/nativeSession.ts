import {Capacitor} from '@capacitor/core';
import {SecureStorage} from '@aparajita/capacitor-secure-storage';
import {publicOrigin} from './apiBase';

type Tokens={access_token:string;refresh_token:string};
const sessionKey='com.capzoryx.copilot.session.v1';
const sessionRoutes=new Set(['/api/auth/signup','/api/auth/login','/api/auth/login/2fa','/api/auth/recover',
  '/api/account/join','/api/account/switch','/api/account/workspaces','/api/account/leave','/api/account/workspaces/clear']);
let authority=0;
let pending=Promise.resolve();

// Serialize key rotation and writes, including overlapping startup requests.
function locked<T>(work:()=>Promise<T>):Promise<T>{
  const result=pending.then(work);
  pending=result.then(()=>{},()=>{});
  return result;
}
function tokens(value:unknown):Tokens|null{
  if(!value||typeof value!=='object')return null;
  const fields=value as Partial<Tokens>;
  return typeof fields.access_token==='string' && fields.access_token.length>0 &&
    typeof fields.refresh_token==='string' && fields.refresh_token.length>0 ?
    {access_token:fields.access_token,refresh_token:fields.refresh_token}:null;
}
async function stored(){return tokens(await SecureStorage.get(sessionKey));}

function send(path:string,init:RequestInit,pair:Tokens|null){
  const headers=new Headers(init.headers);
  headers.set('X-SafeLedger-Client','app');
  headers.delete('Authorization');
  if(pair)headers.set('Authorization','Bearer '+pair.access_token);
  return fetch(publicOrigin()+path,{...init,headers,credentials:'omit',cache:'no-store',redirect:'error'});
}
async function refresh(previous:Tokens, generation:number):Promise<Tokens|null>{
  return locked(async()=>{
    if(generation!==authority)return null;
    const current=await stored();
    if(current?.access_token!==previous.access_token)return current;
    const response=await send('/api/auth/refresh',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({refresh_token:current.refresh_token}),signal:AbortSignal.timeout(90000)},null);
    if(response.status===401){
      await SecureStorage.remove(sessionKey);
      authority++;
      return null;
    }
    if(!response.ok)throw Error('Session refresh is temporarily unavailable.');
    const next=tokens(await response.json());
    if(!next)throw Error('Invalid session refresh.');
    await SecureStorage.set(sessionKey,next);
    return next;
  });
}

export async function nativeFetch(path:string, init:RequestInit):Promise<Response>{
  const destination=new URL(path,publicOrigin());
  if(!path.startsWith('/api/')||destination.origin!==publicOrigin()||!destination.pathname.startsWith('/api/')){
    throw Error('Invalid API destination.');
  }
  if(Capacitor.getPlatform()!=='android'||location.origin!=='https://app.capzoryx.local'){
    throw Error('Native session storage is unavailable.');
  }
  if(init.credentials==='omit'||path.startsWith('/api/public/')||path==='/api/auth/capabilities'){
    return send(path,init,null);
  }
  const generation=authority;
  const pair=await locked(stored);
  if(generation!==authority)throw Error('The active workspace changed.');
  if(path==='/api/session' && !pair){
    return Response.json({error:{code:'AUTHENTICATION_REQUIRED'}},{status:401});
  }
  const logout=path==='/api/auth/logout';
  if(logout && pair)init={...init,body:JSON.stringify({refresh_token:pair.refresh_token})};
  let response=await send(path,init,pair);
  if(!logout && pair && response.status===401){
    const body=await response.clone().json().catch(()=>null);
    if(body?.error?.code==='INVALID_OR_EXPIRED_SESSION'){
      const renewed=await refresh(pair,generation);
      if(renewed && generation===authority){
        init.signal?.throwIfAborted();
        response=await send(path,init,renewed);
      }
    }
  }
  if(logout && (response.ok||response.status===401)){
    await locked(async()=>{
      if(generation!==authority)return;
      await SecureStorage.remove(sessionKey);
      authority++;
    });
  }
  if(!response.ok||init.method!=='POST'||!sessionRoutes.has(path))return response;
  const body=await response.json();
  if(!body.mfa_required){
    const pair=tokens(body);
    if(!pair)throw Error('The API did not issue a native session.');
    await locked(async()=>{
      if(generation!==authority)throw Error('The active workspace changed.');
      await SecureStorage.set(sessionKey,pair);
      authority++;
    });
  }
  delete body.access_token;
  delete body.refresh_token;
  return Response.json(body,{status:response.status});
}
