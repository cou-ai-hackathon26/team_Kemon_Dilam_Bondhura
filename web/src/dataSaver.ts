import {useSyncExternalStore} from 'react';

const key='capzoryx-data-saver';
const event='capzoryx-data-saver-changed';
const connection=(navigator as Navigator & {connection?:EventTarget & {saveData?:boolean}}).connection;
let override:boolean|undefined;

function enabled(){
  try{
    const value=localStorage.getItem(key);
    if(value==='on'||value==='off')return value==='on';
  }catch{/* Storage is optional. */}
  return override??Boolean(connection?.saveData);
}

function subscribe(change:()=>void){
  window.addEventListener(event,change);
  window.addEventListener('storage',change);
  connection?.addEventListener('change',change);
  return ()=>{
    window.removeEventListener(event,change);
    window.removeEventListener('storage',change);
    connection?.removeEventListener('change',change);
  };
}

export function useDataSaver(){return useSyncExternalStore(subscribe,enabled);}
export function setDataSaver(value:boolean){
  override=value;
  try{localStorage.setItem(key,value?'on':'off');}catch{/* Keep this tab's choice. */}
  window.dispatchEvent(new Event(event));
}
