import {parseMinor} from './api';
import type {Draft} from './types';
import {t} from './i18n';
import {todayIso} from './format';

export async function readPhotoText(image:string|File, language:string):Promise<string> {
  const {createWorker, OEM} = await import('tesseract.js');
  const worker = await createWorker(language === 'bn' ? 'ben+eng' : 'eng', OEM.LSTM_ONLY, {
    workerPath:'/ocr/worker.min.js', corePath:'/ocr/core.wasm.js', langPath:'/ocr', workerBlobURL:false
  });
  try {return (await worker.recognize(image)).data.text;}
  finally {await worker.terminate();}
}

const MONTHS=['jan','feb','mar','apr','may','jun','jul','aug','sep','oct','nov','dec'];
const pad=(n:number)=>String(n).padStart(2,'0');
/** The bill date, if one is printed: 2026-11-24, 24/11/2026, 24 Nov 2026 or Nov 24, 2026. Never a future date. */
function billDate(text:string):string|null{
  let y=0,m=0,d=0;
  const iso=text.match(/\b(20\d\d)-(\d{1,2})-(\d{1,2})\b/);
  const dmy=text.match(/\b(\d{1,2})[/.-](\d{1,2})[/.-](20\d\d)\b/);
  const named=text.match(/\b(\d{1,2})\s*([A-Za-z]{3})[a-z]*\.?,?\s*(20\d\d)\b/)||text.match(/\b([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2}),?\s*(20\d\d)\b/);
  if(iso){[y,m,d]=[+iso[1],+iso[2],+iso[3]];}
  else if(dmy){[d,m,y]=[+dmy[1],+dmy[2],+dmy[3]];}
  else if(named){
    const first=/^\d/.test(named[1]);const mon=MONTHS.indexOf((first?named[2]:named[1]).toLowerCase())+1;
    if(mon)[d,m,y]=[+(first?named[1]:named[2]),mon,+named[3]];
  }
  if(!y||m<1||m>12||d<1||d>31)return null;
  const value=`${y}-${pad(m)}-${pad(d)}`;
  return value<=todayIso()?value:null;
}

// Labels that name the amount actually paid or owed. "Subtotal", tax and discount lines are never the total.
const TOTAL=/(?<![a-z])(?:grand\s+total|net\s+total|total\s+paid|total\s+payable|total\s+amount|total\s+due|total\s+bill|amount\s+paid|amount\s+due|amount\s+payable|net\s+payable|payable(?:\s+amount)?|total|সর্বমোট|মোট(?:\s+(?:টাকা|বিল|প্রদেয়))?|প্রদেয়(?:\s+টাকা)?)\s*(?:paid)?\s*[:=-]?\s*(?:(?:BDT|Tk\.?|৳|USD|EUR|GBP|[$€£])\s*)?([\d,]+(?:\.\d{1,2})?)(?![\d,])/gi;
const SUB=/(?:sub\s*-?\s*total|vat|tax|discount|ছাড়|ভ্যাট)\s*$/i;

export function draftFromBill(text:string, currency:string):Draft|null {
  const normalized = text.normalize('NFC').replace(/[০-৯]/g, digit => String('০১২৩৪৫৬৭৮৯'.indexOf(digit)));
  const amounts = new Map<number,number>();  // amount -> how strongly it was labelled as the total
  for (const match of normalized.matchAll(TOTAL)) {
    const before = normalized.slice(Math.max(0, match.index! - 12), match.index!);
    if (SUB.test(before + match[0].split(/\s/)[0]) || /sub\s*-?\s*$/i.test(before)) continue;
    let minor:number;
    try {minor = parseMinor(match[1].replaceAll(',',''), currency);} catch {continue;}
    if (minor <= 0) continue;
    const strong = /grand|net|paid|payable|due|সর্বমোট|প্রদেয়/i.test(match[0]) ? 2 : 1;
    amounts.set(minor, Math.max(amounts.get(minor) || 0, strong));
  }
  if (!amounts.size) return null;
  // One clearly labelled total wins; several different plain "total" lines are ambiguous, so nothing is guessed.
  const best = Math.max(...amounts.values());
  const top = [...amounts].filter(([, weight]) => weight === best).map(([value]) => value);
  if (top.length !== 1) return null;
  const amount = top[0];
  if (amount > 1e12 || !Number.isSafeInteger(amount)) return null;
  const codes = new Set<string>();
  for (const [code, regex] of Object.entries({BDT:/\b(?:BDT|Tk)\b|৳|টাকা/i, USD:/\bUSD\b|\$/, EUR:/\bEUR\b|€/, GBP:/\bGBP\b|£/})) {
    if (regex.test(normalized)) codes.add(code);
  }
  if (codes.size && (codes.size !== 1 || !codes.has(currency))) return null;
  const date = billDate(normalized);
  const number = normalized.match(/\b(?:INV|BILL|REC|RCPT|MEMO)[-/ ]?\d[\w-]*/i)?.[0] || '';
  const paid = /\bpaid\b|পরিশোধিত|পরিশোধ করা হয়েছে/i.test(normalized) && !/\bunpaid\b|due date|বাকি/i.test(normalized);
  const warnings = [t('Check the total against the photo before adding.')];
  if (!date) warnings.push(t('No date was found on the bill, so today is used.'));
  const day = date || todayIso();
  if (paid) {
    // A paid receipt is money that already went out, not a bill still to pay.
    return {ok:true, kind:'cash', direction:'outflow', status:'cleared', amount_minor:amount, currency, workspace_currency:currency,
      currency_mismatch:false, effective_on:day, memo:number?`Receipt ${number}`:'', warnings, can_save:true, confidence:null};
  }
  return {ok:true, kind:'invoice', amount_minor:amount, currency, workspace_currency:currency, currency_mismatch:false,
    effective_on:day, due_on:'', vendor_name:'', invoice_number:number, memo:'', warnings:[...warnings,
      t('Enter the supplier and the due date before adding.')], can_save:true, confidence:null};
}
