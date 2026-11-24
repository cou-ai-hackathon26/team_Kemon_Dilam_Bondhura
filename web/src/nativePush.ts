import {registerPlugin,type PluginListenerHandle} from '@capacitor/core';
import {SecureStorage} from '@aparajita/capacitor-secure-storage';
import type {FirebaseMessagingPlugin} from '@capacitor-firebase/messaging';
import {nativeFetch} from './nativeSession';

// Android provides this plugin through Capacitor; the browser SDK is never used.
const FirebaseMessaging=registerPlugin<FirebaseMessagingPlugin>('FirebaseMessaging');
const settings=registerPlugin<{configuration():Promise<{available:boolean}>;stop():Promise<void>}>('CapzoryxPush');
const key='com.capzoryx.copilot.push.v1';
type Enrollment={scope:string;id:string;retired:string[]};
let queue=Promise.resolve();
let listener:PluginListenerHandle|undefined;
let version=0;

function serial<T>(fn:()=>Promise<T>):Promise<T>{
  const work=queue.then(fn);
  queue=work.then(()=>{},()=>{});
  return work;
}

const read=async()=>await SecureStorage.get(key) as Enrollment|null;
const call=(path:string,body?:unknown)=>nativeFetch('/api/notifications/'+path,{
  method:body===undefined?'GET':'POST',headers:{'Content-Type':'application/json'},
  body:body===undefined?undefined:JSON.stringify(body),signal:AbortSignal.timeout(30000)
});

async function revoke(id:string){
  const response=await call('subscriptions/'+encodeURIComponent(id)+'/revoke',{});
  if(!response.ok&&response.status!==404&&response.status!==401)throw Error('Could not remove Android notification registration.');
}
async function register(scope:string,token:string,old:Enrollment|null){
  const response=await call('subscriptions',{kind:'fcm',token});
  if(!response.ok)throw Error('Could not register Android notifications.');
  const {id,simulation_only}=await response.json();
  if(typeof id!=='string'||!id||simulation_only!==true)throw Error('Invalid notification registration.');
  const retired=[...new Set(old?[...old.retired,old.id]:[])].filter(previous=>previous!==id);
  await SecureStorage.set(key,{scope,id,retired});
  for(const previous of retired)await revoke(previous);
  if(retired.length)await SecureStorage.set(key,{scope,id,retired:[]});
}
async function permitted(){
  if(!(await settings.configuration()).available)return false;
  const response=await call('push-config');
  return response.ok && (await response.json()).fcm===true;
}
async function stop(){
  if(listener){await listener.remove();listener=undefined;}
  const old=await read();
  if(!old)return;
  // Invalidate the device token even when the backend has already expired the session.
  await settings.stop();
  for(const id of [old.id,...old.retired])await revoke(id);
  await SecureStorage.remove(key);
}

export function stopNativePush(){version++;return serial(stop);}

async function listen(scope:string){
  if(listener)await listener.remove();
  const generation=version;
  listener=await FirebaseMessaging.addListener('tokenReceived',({token})=>{
    void serial(async()=>{
      const old=await read();
      if(version===generation&&old?.scope===scope)await register(scope,token,old);
    }).catch(()=>{/* Resuming an enrolled workspace retries the current token. */});
  });
}
export async function nativePushStatus(scope:string){
  const old=await read();
  return {enabled:old?.scope===scope,available:await permitted()};
}

export function enableNativePush(scope:string){
  const generation=version;
  return serial(async()=>{
    if(generation!==version||!await permitted())throw Error('Android notifications are unavailable.');
    const {receive}=await FirebaseMessaging.requestPermissions();
    if(receive!=='granted')throw Error('Allow notifications in Android settings to receive reminders.');
    const old=await read();
    if(old && old.scope!==scope)await stop();
    try{
      const {token}=await FirebaseMessaging.getToken();
      await register(scope,token,old?.scope===scope?old:null);
      await listen(scope);
    }catch(error){
      if(!await read())await settings.stop();
      throw error;
    }
  });
}

export function resumeNativePush(scope:string){
  const generation=version;
  return serial(async()=>{
    const old=await read();
    if(!old || generation!==version)return;
    if(old.scope!==scope){await stop();return;}
    if((await FirebaseMessaging.checkPermissions()).receive!=='granted'){await stop();return;}
    if(!await permitted())return;
    const {token}=await FirebaseMessaging.getToken();
    await register(scope,token,old);
    await listen(scope);
  });
}
