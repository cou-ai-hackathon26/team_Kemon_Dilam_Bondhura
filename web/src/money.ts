import {currencies} from './currencies';
// Display only. All balances, costs and projections come from backend integer calculations.
export const currencyDigits = (currency:string) => currencies.find(c=>c.code===currency)?.digits ?? 2;
export const money = (minor:number,currency='BDT') => new Intl.NumberFormat(undefined,{style:'currency',currency,currencyDisplay:'code',minimumFractionDigits:currencyDigits(currency),maximumFractionDigits:currencyDigits(currency)}).format(minor / 10**currencyDigits(currency));
export function parseMinor(text:string,currency='BDT'):number {
  const digits=currencyDigits(currency);
  if(!new RegExp('^\\d{1,13}'+(digits?'(\\.\\d{1,'+digits+'})?':'')+'$').test(text))throw new Error(`Enter a nonnegative ${currency} amount with at most ${digits} decimal places.`);
  const [whole,fraction='']=text.split('.');
  const result=BigInt(whole)*10n**BigInt(digits)+BigInt(fraction.padEnd(digits,'0')||'0');
  if(result>1000000000000n)throw new Error('Amount exceeds the supported limit.');
  return Number(result);
}
