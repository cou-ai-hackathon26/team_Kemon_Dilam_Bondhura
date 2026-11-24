import {describe,expect,it,vi} from 'vitest';
vi.mock('../src/i18n',()=>({t:(s:string)=>s,T:(s:string)=>s}));
import {draftFromBill} from '../src/billPhoto';

describe('reading a bill photo on the device',()=>{
  it('reads a paid receipt with "Total Paid", its date and number',()=>{
    const d=draftFromBill('Invoice PAID\nInvoice No. Invoice Date\nINV-001 24 Nov 2026\nPaid by Mahin\nDescription Amount\nBiriyani BDT 500.00\nTotal Paid BDT 500.00\nSample invoice','BDT')!;
    expect(d.amount_minor).toBe(50000);expect(d.kind).toBe('cash');expect(d.direction).toBe('outflow');
    expect(d.effective_on).toBe('2026-11-24');expect(d.memo).toContain('INV-001');
  });
  it('works when the photo text comes back as one line',()=>{
    expect(draftFromBill('Invoice PAID Invoice No. INV-001 24 Nov 2026 Biriyani BDT 500.00 Total Paid BDT 500.00','BDT')?.amount_minor).toBe(50000);
  });
  it('prefers grand total over subtotal and VAT, and keeps an unpaid bill as a bill',()=>{
    const d=draftFromBill('Subtotal 10,000\nVAT 500\nGrand Total Tk 10,500/-\nDue date 30/11/2026','BDT')!;
    expect(d.amount_minor).toBe(1050000);expect(d.kind).toBe('invoice');
  });
  it('reads Bangla digits and labels',()=>{expect(draftFromBill('মোট টাকা ১,২৫০','BDT')?.amount_minor).toBe(125000);});
  it('guesses nothing when two plain totals disagree or the currency is different',()=>{
    expect(draftFromBill('Total 500\nTotal 700','BDT')).toBeNull();
    expect(draftFromBill('Total USD 50','BDT')).toBeNull();
  });
});
