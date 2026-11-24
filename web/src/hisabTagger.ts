import dictionary from './taggerLexicon.json';

const labels=['O','AMOUNT','MULT','PARTY','DATE','DIR_IN','DIR_OUT','DUE','ITEM'];
const normalize=(s:string)=>s.normalize('NFC');
const key=(s:string)=>s.toLowerCase().replace(/[০-৯]/g,c=>String(c.charCodeAt(0)-0x9e6));
export const tokenize=(s:string)=>normalize(s).match(/[\p{L}\p{M}\p{N}]+|[^\s]/gu)||[];
const lexicon=Object.entries(dictionary).map(([name,words])=>({name,words:new Set(words.map(w=>key(normalize(w))))}));
export type Model={format:1;version:string;labels:string[];weights:Record<string,Record<string,number>>;transitions:Record<string,Record<string,number>>};
type Tag={token:string;label:string;confidence:number};

export function features(tokens:string[]):string[][]{
  const keys=tokens.map(key);
  return keys.map((word,i)=>{
    const letters=Array.from(word);
    const script=/^[0-9]+$/.test(word)?'digit':/^\p{Script=Bengali}+$/u.test(word)?'bn':/^[a-z]+$/.test(word)?'latin':'mixed';
    const shape=word.replace(/[0-9]/g,'d').replace(/[a-z]/g,'a').replace(/\p{Script=Bengali}/gu,'b');
    const out=['bias','w='+word,'script='+script,'shape='+shape];
    for(let n=1;n<=Math.min(4,letters.length);n++){
      out.push('p'+n+'='+letters.slice(0,n).join(''),'s'+n+'='+letters.slice(-n).join(''));
    }
    for(const d of [-2,-1,1,2])out.push('n'+d+'='+(i+d<0?'<BOS>':i+d>=keys.length?'<EOS>':keys[i+d]));
    if(/^[0-9]+$/.test(word))out.push('is_number');
    for(const group of lexicon)if(group.words.has(word))out.push('dict='+group.name);
    return out;
  });
}

function logSum(values:number[]){
  const high=Math.max(...values);
  return high+Math.log(values.reduce((total,value)=>total+Math.exp(value-high),0));
}

// Linear-chain CRF. Forward/backward gives one posterior probability for each Viterbi label.
export function decode(text:string,model:Model):Tag[]{
  const tokens=tokenize(text);
  if(!tokens.length||tokens.length>128)return [];
  const tags=model.labels;
  const state=features(tokens).map(row=>tags.map(label=>row.reduce((n,f)=>n+(model.weights[f]?.[label]||0),0)));
  const transition=tags.map(a=>tags.map(b=>model.transitions[a]?.[b]||0));
  const alpha=[state[0].slice()],back:number[][]=[];
  let maxima=state[0].slice();
  for(let i=1;i<tokens.length;i++){
    const next:number[]=[],sum:number[]=[],links:number[]=[];
    for(let b=0;b<tags.length;b++){
      const values=maxima.map((v,a)=>v+transition[a][b]);
      const best=values.indexOf(Math.max(...values));
      links.push(best);next.push(values[best]+state[i][b]);
      sum.push(logSum(alpha[i-1].map((v,a)=>v+transition[a][b]))+state[i][b]);
    }
    back.push(links);alpha.push(sum);maxima=next;
  }
  const path=[maxima.indexOf(Math.max(...maxima))];
  for(let i=back.length-1;i>=0;i--)path.push(back[i][path[path.length-1]]);
  path.reverse();
  const logZ=logSum(alpha[alpha.length-1]);
  let beta=tags.map(()=>0);
  const result:Tag[]=[];
  for(let i=tokens.length-1;i>=0;i--){
    const j=path[i];
    result.push({token:tokens[i],label:tags[j],confidence:Math.min(1,Math.exp(alpha[i][j]+beta[j]-logZ))});
    if(i)beta=tags.map((_,a)=>logSum(tags.map((__,b)=>transition[a][b]+state[i][b]+beta[b])));
  }
  return result.reverse();
}

export type TaggedFields={direction:'inflow'|'outflow'|'due'; amountMinor:number; party:string};
const units:Record<string,number>={k:1000,hajar:1000,hazar:1000,'হাজার':1000,thousand:1000,lakh:100000,lac:100000,lakhs:100000,'লাখ':100000,crore:10000000,'কোটি':10000000};
const amounts:Record<string,number>={one:1,two:2,five:5,ten:10,ek:1,dui:2,pach:5,dosh:10,'এক':1,'দুই':2,'পাঁচ':5,'দশ':10};

export function interpret(tags:Tag[],digits:number):TaggedFields|null{
  const economics=tags.filter(t=>['AMOUNT','MULT','DIR_IN','DIR_OUT','DUE','PARTY'].includes(t.label));
  if(economics.some(t=>t.confidence<0.8))return null;
  const flows=new Set(tags.filter(t=>['DIR_IN','DIR_OUT','DUE'].includes(t.label)).map(t=>t.label));
  if(flows.size!==1)return null;
  const indices=tags.flatMap((t,i)=>/^(AMOUNT|MULT)$/.test(t.label)?[i]:[]);
  if(!indices.length||indices.at(-1)!-indices[0]+1!==indices.length)return null;
  let total=0n, previousUnit=Infinity;
  const minorScale=10n**BigInt(digits);
  for(let pos=0;pos<indices.length;pos++){
    const t=tags[indices[pos]];
    if(t.label!=='AMOUNT')return null;
    // The shared tokenizer keeps punctuation separate. Join it only between
    // amount tokens, then validate grouping and decimal precision explicitly.
    let s=key(t.token);
    while(pos+2<indices.length && /^[.,]$/.test(tags[indices[pos+1]].token)
          && tags[indices[pos+1]].label==='AMOUNT' && tags[indices[pos+2]].label==='AMOUNT'){
      s+=tags[indices[pos+1]].token+key(tags[indices[pos+2]].token);pos+=2;
    }
    if(Object.hasOwn(amounts,s))s=String(amounts[s]);
    const number=/^((?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+))(?:\.([0-9]+))?(k)?$/.exec(s);
    if(!number)return null;
    let unit=number[3]?1000:1;
    const following=tags[indices[pos+1]];
    if(following?.label==='MULT'){
      if(number[3])return null;
      unit=units[key(following.token)];pos++;
    }
    if(!Number.isFinite(unit)||unit>=previousUnit)return null;
    const fraction=number[2]||'',denominator=10n**BigInt(fraction.length);
    const scaled=BigInt(number[1].replaceAll(',','')+fraction)*BigInt(unit)*minorScale;
    if(scaled<=0n||scaled%denominator!==0n)return null;
    total+=scaled/denominator;previousUnit=unit;
  }
  const amountMinor=Number(total);
  if(!Number.isSafeInteger(amountMinor)||amountMinor<=0||amountMinor>1e12)return null;
  const partyTags=tags.flatMap((t,i)=>t.label==='PARTY'?[i]:[]);
  if(partyTags.length&&partyTags.at(-1)!-partyTags[0]+1!==partyTags.length)return null;
  const party=partyTags.map(i=>tags[i].token).join(' ').replace(/কে$/u,'');
  const flow=[...flows][0];
  return {amountMinor,direction:flow==='DIR_IN'?'inflow':flow==='DIR_OUT'?'outflow':'due',party};
}

export async function loadCheckedModel(version:string,sha:string):Promise<Model>{
  if(!/^\d{8}$/.test(version)||!/^([a-f0-9]{64})$/.test(sha))throw new Error('Invalid tagger release');
  const response=await fetch('/models/hisab-tagger-'+version+'.json',{credentials:'omit',redirect:'error'});
  if(!response.ok||Number(response.headers.get('content-length'))>5_000_000)throw new Error('Tagger unavailable');
  const bytes=await response.arrayBuffer();
  if(bytes.byteLength>5_000_000)throw new Error('Tagger exceeds size limit');
  const checksum=Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256',bytes)),n=>n.toString(16).padStart(2,'0')).join('');
  if(checksum!==sha)throw new Error('Tagger checksum mismatch');
  const model=JSON.parse(new TextDecoder().decode(bytes)) as Model;
  if(model.format!==1||model.version!==version||!model.weights||!model.transitions||
     !Array.isArray(model.labels)||model.labels.length!==labels.length||new Set(model.labels).size!==labels.length||
     !model.labels.every(t=>labels.includes(t)))throw new Error('Invalid tagger model');
  return model;
}
