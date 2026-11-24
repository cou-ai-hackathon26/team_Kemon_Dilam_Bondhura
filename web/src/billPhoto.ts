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

export function draftFromBill(text:string, currency:string):Draft|null {
  // A subtotal, tax amount or numeric invoice ID must never become the bill total.
  const normalized = text.normalize('NFC').replace(/[০-৯]/g, digit => String('০১২৩৪৫৬৭৮৯'.indexOf(digit)));
  const lines = normalized.split(/\r?\n/).map(line=>line.trim()).filter(Boolean);
  const amounts = new Set<number>();
  for (const line of lines) {
    const total = line.match(/^(?:grand\s+total|amount\s+due|total(?:\s+(?:amount|due))?|সর্বমোট|মোট(?: টাকা)?)\s*[:=-]?\s*(?:(?:BDT|Tk\.?|৳|USD|EUR|GBP|[$€£])\s*)?([\d,]+(?:\.\d+)?)\s*(?:টাকা|BDT|USD|EUR|GBP)?\s*$/i);
    if (!total) continue;
    try {amounts.add(parseMinor(total[1].replaceAll(',',''), currency));} catch {return null;}
  }
  if (amounts.size !== 1) return null;
  const [amount] = amounts;
  if (amount <= 0 || amount > 1e12) return null;
  const codes = new Set<string>();
  for (const [code, regex] of Object.entries({BDT:/\b(?:BDT|Tk)\b|৳|টাকা/i, USD:/\bUSD\b|\$/, EUR:/\bEUR\b|€/, GBP:/\bGBP\b|£/})) {
    if (regex.test(normalized)) codes.add(code);
  }
  if (codes.size && (codes.size !== 1 || !codes.has(currency))) return null;
  if (!Number.isSafeInteger(amount)) return null;
  return {ok:true, kind:'invoice', amount_minor:amount, currency, workspace_currency:currency, currency_mismatch:false,
    effective_on:todayIso(), due_on:'', vendor_name:'', invoice_number:'', memo:'', warnings:[
      t('Check the total against the photo and enter the supplier, invoice number and dates before adding.')
    ], can_save:true, confidence:null};
}
