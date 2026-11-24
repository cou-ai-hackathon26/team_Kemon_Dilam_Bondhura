import {describe,expect,it} from 'vitest';
import {normalizeSpokenAmount} from '../src/spokenAmount';
import {parseRecordSentence} from '../src/recordParser';

describe('currency-bound amounts in editable voice transcripts',()=>{
  it.each([
    ['করিমের কাছ থেকে পাঁচশ টাকা পেলাম।','করিমের কাছ থেকে 500 টাকা পেলাম।',50000,'inflow'],
    ['আজ আমি করিমকে এক হাজার টাকা দিলাম।','আজ আমি করিমকে 1000 টাকা দিলাম।',100000,'outflow'],
    ['আজ আমি করিম কি এক হাজার টাকা দিলাম','আজ আমি করিম কি 1000 টাকা দিলাম',100000,'outflow'],
    ['গতকাল নীলাকে এক হাজার পাঁচশ পঁচিশ টাকা দিলাম','গতকাল নীলাকে 1525 টাকা দিলাম',152500,'outflow'],
    ['I received one hundred and twenty taka from One Market yesterday','I received 120 taka from One Market yesterday',12000,'inflow'],
    ['I paid Two Brothers two thousand and forty-five taka on 2026-11-21','I paid Two Brothers 2045 taka on 2026-11-21',204500,'outflow'],
  ])('keeps the party and date in %s',(text,expected,minor,direction)=>{
    const draftText=normalizeSpokenAmount(String(text),'BDT');
    expect(draftText).toBe(expected);
    expect(parseRecordSentence(draftText,{currency:'BDT',digits:2})).toMatchObject({amount_minor:minor,direction});
  });
  it.each([
    'ছয় তারিখে করিমকে দিলাম', 'পাঁচশ টাকা আর এক হাজার টাকা দিলাম','পাঁচশ অথবা এক হাজার টাকা দিলাম',
    'করিমকে সাড়ে পাঁচশ টাকা দিলাম','দুশো থেকে পাঁচশ টাকা দিলাম','করিমকে পাঁচ হাজার, একশ টাকা দিলাম',
    'আমি ২ হাজার পাঁচশ টাকা দিলাম','আমি পাঁচ টাকা পঁচিশ পয়সা পেলাম','পাঁচশ 40 টাকা পেলাম',
    'Paid three hundred or five hundred taka','Paid one thousand and and fifty taka',
    'Paid half of five hundred taka','Paid five hundred taka minus one hundred','Received five to ten taka','Paid minus fifty taka','Received ten thousand thousand taka',
    'Paid one hundred taka and 20 taka','Paid five hundred dollars','Paid Two Brothers today',
  ])('leaves ambiguous or inapplicable speech editable: %s',text=>{
    expect(normalizeSpokenAmount(text,'BDT')).toBe(text);
  });
  it.each([
    ['Received five dollars and fifty','USD'],['Received five dollars fifty','USD'],
    ['Received five dollars and 50','USD'],['Received five dollars and Fifty More','USD'],['Received five dollars or ten','USD'],
    ['I did not pay five dollars','USD'],['I didn’t pay five dollars','USD'],['Received five dollars in JPY','USD'],['পাঁচ টাকা পঞ্চাশ পেলাম','BDT'],
    ['করিমকে পাঁচশ টাকা নয়, ছয়শ দিলাম','BDT'],['পাঁচশ টাকা না ছয়শ দিলাম','BDT'],
    ['পাঁচশ টাকা দিলাম আর ২০ চেঞ্জ পেলাম','BDT'],['এক হাজার টাকা চেঞ্জ চাই','BDT'],['পাঁচশ টাকা চেঞ্জটা ফেরত পেলাম','BDT'],['করিমকে এক হাজার টাকা দিইনি','BDT'],
    ['পাঁচশ টাকা দিলাম, চারশো পেলাম','BDT'],['Paid 50, received one hundred taka','BDT'],
    ['Received five dollars plus one euro','USD'],['I paid five dollars and ten rupees','USD'],
    ['পাঁচশ টাকা আর দশ রুপি পেলাম','BDT'],['Received five dollars Canadian','USD'],
    ['করিমের কাছ থেকে পাঁচশ টাকা পেলাম','USD'],['Paid five hundred dollars','BDT'],
  ])('abstains on fractions, other quantities, corrections and currency changes: %s',(text,currency)=>{
    expect(normalizeSpokenAmount(text,currency)).toBe(text);
  });
  it('honours the selected workspace currency',()=>{
    expect(normalizeSpokenAmount('Paid five hundred dollars','USD')).toBe('Paid 500 dollars');
  });
});

describe('money that did not move',()=>{
  const opts={currency:'BDT',digits:2,today:new Date('2026-11-24T10:00:00')};
  it.each(['500 taka dilam na','500 টাকা দিলাম না','I did not pay Rahim 500','রহিম ৫০০ টাকা দেয়নি'])('makes no draft from %s',text=>{
    expect(parseRecordSentence(text,opts)).toBeNull();
  });
  it('still reads a correction ("na na") as the corrected payment',()=>{
    expect(parseRecordSentence('Rahim ke 500 na na 600 taka dilam',opts)).toMatchObject({direction:'outflow'});
  });
});
