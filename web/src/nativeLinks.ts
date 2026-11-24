import {App} from '@capacitor/app';
import {CapacitorShareTarget} from '@capgo/capacitor-share-target';
import {publicOrigin} from './apiBase';

export async function listenToNativeLinks(){
  await App.addListener('appUrlOpen',({url})=>{
    const link=new URL(url);
    if(link.origin!==publicOrigin())return;
    let path='';
    if(/^\/verify\/[a-f0-9]{32}$/.test(link.pathname))path=link.pathname;
    const invitation=link.searchParams.get('join');
    if(link.pathname==='/' && invitation && /^[A-Za-z0-9_-]{20,100}$/.test(invitation))path='/?join='+invitation;
    if(path && path!==location.pathname+location.search)location.assign(path);
  });
  await CapacitorShareTarget.addListener('shareReceived',async event=>{
    const text=event.texts.join('\n').trim();
    if(!text || text.length>2000)return;
    // Native text shares enter the same IndexedDB preview queue as PWA shares.
    const store=(window as Window & {capzoryxStore?:{share:(text:string)=>Promise<string>}}).capzoryxStore;
    if(!store)return;
    const id=await store.share(text);
    location.assign('/share?id='+encodeURIComponent(id));
  });
}
