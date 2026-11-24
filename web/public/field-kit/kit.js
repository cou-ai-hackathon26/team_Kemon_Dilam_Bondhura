'use strict';
const el=id=>document.getElementById(id);
const rows=[];
const clips=new Map();
const numbers=new Intl.NumberFormat('bn-BD');
let recorder=null, starting=false, lockedRow=null, timer=null;
el('day').value=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Dhaka',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
function status(text){el('notice').textContent=text;}
function download(blob,name){
  const url=URL.createObjectURL(blob);
  const a=document.createElement('a');a.href=url;a.download=name;a.click();
  setTimeout(()=>URL.revokeObjectURL(url),30000);
}
function render(){
  const selected=el('clip-row').value;
  el('items').replaceChildren();el('clip-row').replaceChildren();
  for(const row of rows){
    const item=document.createElement('li');
    item.append(document.createTextNode(row.text));
    const remove=document.createElement('button');
    remove.type='button';remove.textContent='মুছুন';remove.disabled=row.id===lockedRow;
    remove.onclick=()=>{rows.splice(rows.indexOf(row),1);clips.delete(row.id);render();};
    item.append(remove);el('items').append(item);
    el('clip-row').add(new Option(row.text,row.id));
  }
  if(rows.some(r=>r.id===selected))el('clip-row').value=selected;
  el('participant').readOnly=rows.length>0;
  el('count').textContent=numbers.format(rows.length)+'টি বাক্য';
  el('save').disabled=!rows.length;
  el('record').disabled=!rows.length||starting||!!recorder||!el('voice-consent').checked;
  el('clip-row').disabled=starting||!!recorder;
  el('stop').disabled=!recorder;
  el('downloads').replaceChildren();
  for(const [id,{blob,file,meta}] of clips){
    const entry=document.createElement('p');
    const audio=document.createElement('button');audio.type='button';audio.textContent='অডিও নামান · '+id;
    audio.onclick=()=>download(blob,file);entry.append(audio,' ');
    const labels=document.createElement('button');labels.type='button';labels.textContent='এই অডিওর বিবরণ নামান';
    labels.onclick=()=>download(new Blob([JSON.stringify(meta)+'\n'],{type:'application/json'}),id+'.json');entry.append(labels);el('downloads').append(entry);
  }
}
el('entry').onsubmit=e=>{
  e.preventDefault();
  const text=el('sentence').value.trim().normalize('NFC');
  const amount=el('amount').value.trim().replace(/[০-৯]/g,ch=>'০১২৩৪৫৬৭৮৯'.indexOf(ch));
  if(!/^\d{1,10}(\.\d{1,2})?$/.test(amount)){status('টাকার অঙ্ক পয়সাসহ সর্বোচ্চ দুই দশমিক ঘরে লিখুন।');return;}
  const [whole,frac='']=amount.split('.');
  const minor=Number(whole)*100+Number(frac.padEnd(2,'0'));
  if(!minor||minor>1e12||text.length<4){status('বাক্য ও টাকার অঙ্ক যাচাই করুন।');return;}
  const canonical=s=>s.normalize('NFC').trim().toLowerCase().replace(/\s+/g,' ');
  if(rows.some(r=>canonical(r.text)===canonical(text))){status('এই বাক্যটি আগেই রেখেছেন।');return;}
  const contributor=el('participant').value;
  if(!/^[a-z][a-z0-9_-]{1,23}$/.test(contributor)){status('ছোট হাতের অক্ষর দিয়ে কোড শুরু করুন।');return;}
  rows.push({id:contributor+'-'+crypto.randomUUID(),source:'team',language:el('language').value,text,as_of:el('day').value,amount_minor:minor,direction:el('direction').value,party:el('party').value.trim()||null,contributor_ref:contributor});
  el('sentence').value='';el('amount').value='';el('party').value='';el('consent').checked=false;
  el('direction').value='';render();status('বাক্যটি তালিকায় আছে।');el('sentence').focus();
};
el('save').onclick=()=>download(new Blob([rows.map(row=>JSON.stringify(row)).join('\n')+'\n'],{type:'application/x-ndjson'}),'holdout-'+el('participant').value+'.jsonl');
el('voice-consent').onchange=render;
el('record').onclick=async()=>{
  if(starting||recorder||!el('voice-consent').checked)return;
  const row=rows.find(r=>r.id===el('clip-row').value);
  if(!row)return;
  if(!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){
    status('এই ব্রাউজারে রেকর্ড করা যাচ্ছে না। ফোনের রেকর্ডারে পড়ে অডিওটির নামে বাক্যের আইডি দিন।');return;
  }
  lockedRow=row.id;starting=true;render();
  let stream;
  try{
    stream=await navigator.mediaDevices.getUserMedia({audio:true});
    if(!el('voice-consent').checked){stream.getTracks().forEach(t=>t.stop());starting=false;lockedRow=null;render();return;}
    const rec=new MediaRecorder(stream), chunks=[];let failed=false;
    rec.ondataavailable=e=>{if(e.data.size)chunks.push(e.data);};
    rec.onerror=()=>{failed=true;stream.getTracks().forEach(t=>t.stop());status('রেকর্ডিং ব্যর্থ হয়েছে। আবার চেষ্টা করুন।');};
    rec.onstop=()=>{
      clearTimeout(timer);stream.getTracks().forEach(t=>t.stop());
      const mime=rec.mimeType||chunks[0]?.type||'application/octet-stream';
      const blob=new Blob(chunks,{type:mime});
      if(!failed&&blob.size&&blob.size<=2*1024*1024&&el('voice-consent').checked){
        const ext=mime.includes('mp4')?'m4a':mime.includes('ogg')?'ogg':mime.includes('webm')?'webm':'audio';
        const file=row.id+'.'+ext;
        clips.set(row.id,{blob,file,meta:{...row,audio_file:file,mime_type:mime,consent_scope:'speech_evaluation_only',consent_date:new Date().toISOString().slice(0,10)}});
        el('clip-state').textContent='অডিও ও তার বিবরণ আলাদাভাবে নামিয়ে নিন।';
      }else el('clip-state').textContent='অডিও রাখা হয়নি। সম্মতি দিয়ে ছোট করে আবার চেষ্টা করুন।';
      recorder=null;lockedRow=null;render();
    };
    rec.start();recorder=rec;starting=false;
    el('clip-state').textContent='রেকর্ড চলছে…';render();
    timer=setTimeout(()=>{if(rec.state==='recording')rec.stop();},30000);
  }catch{stream?.getTracks().forEach(t=>t.stop());starting=false;lockedRow=null;recorder=null;render();status('মাইক্রোফোন চালু করা যায়নি। ব্রাউজারের মাইক্রোফোন অনুমতি দেখুন।');}
};
el('stop').onclick=()=>{if(recorder?.state==='recording')recorder.stop();};
render();
