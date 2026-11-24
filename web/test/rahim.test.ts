import {afterEach,describe,expect,it,vi} from 'vitest';
import {sampleBusiness} from '../src/sampleBusiness';
import {parseRecordSentence} from '../src/recordParser';

afterEach(()=>vi.useRealTimers());
describe('Rahim’s fictional workspace',()=>{
  it('dates the sample according to the Bangladesh business day',()=>{
    vi.useFakeTimers();vi.setSystemTime(new Date('2026-11-20T19:30:00Z'));
    const ds=JSON.parse(sampleBusiness('BDT'));
    expect(ds.as_of).toBe('2026-11-21');
    expect(ds.events.find((e:{event_id:string})=>e.event_id==='SAMPLE-MATERIAL').effective_on).toBe('2026-11-27');
    expect(ds.events.find((e:{event_id:string})=>e.event_id==='SAMPLE-EXPORT').effective_on).toBe('2026-12-16');
  });
  it.each([['BDT',2],['JPY',0],['KWD',3]])('scales the same sample to the selected %s currency',(code,digits)=>{
    expect(JSON.parse(sampleBusiness(code as string)).opening_balance_minor).toBe(120000*10**Number(digits));
  });
  it('recovers a mixed-unit Banglish draft without a provider',()=>{
    const draft=parseRecordSentence('aj Nila Shop ke 9 hajar 250 taka dilam',{currency:'BDT',digits:2,today:new Date(2026,10,21,12)});
    expect(draft).toMatchObject({amount_minor:925000,currency:'BDT',effective_on:'2026-11-21',direction:'outflow',vendor_name:'Nila Shop'});
  });
});
