import {asciiDigits} from './recordParser';

export type Kind='inflow'|'outflow'|'customer_credit'|'customer_receipt'|'supplier_bill'|'';
export type KhataDraft={line:number;source:string;kind:Kind;day:string;amount:string;done:boolean};

export function readKhataRows(raw:string):KhataDraft[]{
  const lines=raw.replace(/\r/g,'').split('\n');
  const rows:KhataDraft[]=[];
  for(const [idx,original] of lines.entries()){
    const source=original.trim();
    if(!source)continue;
    const line=asciiDigits(source.normalize('NFC'));
    if(/^(?:তারিখ|date)\s*[|,:;\s]+(?:নাম|বিবরণ|description|name)\s*[|,:;\s]+(?:ধরন|type|amount|টাকা|পরিমাণ)(?:[|,:;\s]+(?:amount|টাকা|পরিমাণ))?$/i.test(line))continue;
    const d=line.match(/(?<!\d)(\d{4}-\d{2}-\d{2}|\d{1,2}[-/.]\d{1,2}[-/.]\d{4})(?!\d)/);
    let day='';
    if(d){
      const parts=d[1].split(/[-/.]/);
      const standard=parts[0].length===4?parts:parts.reverse();
      const candidate=standard.map((x,i)=>i===0?x:x.padStart(2,'0')).join('-');
      const timestamp=Date.parse(candidate+'T00:00:00Z');
      if(Number.isFinite(timestamp)&&new Date(timestamp).toISOString().slice(0,10)===candidate)day=candidate;
    }
    const remaining=d?line.replace(d[0],' '):line;
    const numbers=remaining.match(/(?<![\p{L}\p{N}.,])(?:[0-9]{1,3}(?:,[0-9]{3})+|[0-9]+)(?:\.[0-9]{1,3})?(?![\p{L}\p{N}.,])/gu)||[];
    const amount=numbers.length===1?numbers[0].replaceAll(',',''):'';
    const credit=/বাকি(?:তে)?\s*(?:বিক্রি|বিক্রয়)|credit sale/i.test(line);
    const purchase=/বাকি(?:তে)?\s*(?:ক্রয়|কেনা)|supplier bill|credit purchase/i.test(line);
    const receipt=/পাওনা\s*আদায়|বাকি\s*আদায়|customer receipt|debt collection/i.test(line);
    const income=/নগদ\s*(?:জমা|বিক্রি)|cash (?:in|sale)|money in/i.test(line);
    const expense=/নগদ\s*(?:খরচ|কেনা)|cash (?:out|purchase)|money out/i.test(line);
    const kinds=([['customer_credit',credit],['supplier_bill',purchase],['customer_receipt',receipt],['inflow',income],['outflow',expense]] as const)
      .filter(([,ok])=>ok).map(([kind])=>kind);
    rows.push({line:idx+1,source,kind:kinds.length===1?kinds[0]:'',day,amount,done:false});
  }
  return rows;
}
