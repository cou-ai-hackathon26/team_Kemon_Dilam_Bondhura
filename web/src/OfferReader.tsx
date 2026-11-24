import {useState} from 'react';
import {t,useI18n} from './i18n';

export type OfferTerms={lender_name?:string;product_type?:'murabaha';principal_minor?:number;upfront_fee_minor?:number;markup_minor?:number;
  rate_type?:'flat_per_term'|'annual_reducing'|'murabaha_markup';rate_bps?:number;term?:number;unit?:'months'|'days';repayment?:'single_payment'|'equal_installments'};
type Request=<T,>(path:string,body?:unknown)=>Promise<T>;

export function OfferReader({request,fill}:{request:Request;fill:(terms:OfferTerms)=>void}){
  const [text,setText]=useState(''),[busy,setBusy]=useState(false),[problem,setProblem]=useState('');
  const {code}=useI18n();
  async function photo(file:File){
    setBusy(true);setProblem('');
    try{
      if(!['image/png','image/jpeg','image/webp'].includes(file.type)||file.size>4*1024*1024)throw new Error(t('Choose a PNG, JPEG or WebP under 4 MB.'));
      const {readPhotoText}=await import('./billPhoto');
      const result=await readPhotoText(file,code);
      if(!result.trim())throw new Error(t('No offer text found. Paste or type the quoted terms.'));
      if(result.length>12000)throw new Error(t('Choose a shorter excerpt with the offer terms.'));
      setText(result);
    }catch(e){setProblem(e instanceof Error?e.message:t('Could not read the offer.'));}
    finally{setBusy(false);}
  }
  async function review(){
    if(busy)return;
    setBusy(true);setProblem('');
    try{
      const r=await request<{terms:OfferTerms;warnings:string[]}>('/assist/offer',{text});
      if(r.warnings.includes('CURRENCY_MISMATCH'))throw new Error(t('The offer currency differs from this workspace.'));
      if(r.warnings.includes('INCONSISTENT_MARKUP'))throw new Error(t('The sale price and markup disagree. Check the original offer.'));
      if(Object.keys(r.terms).length===0)throw new Error(t('No explicit terms found. Enter the quote below.'));
      fill(r.terms);
    }catch(e){setProblem(e instanceof Error?e.message:t('Could not read the offer.'));}
    finally{setBusy(false);}
  }
  return <details className="panel"><summary>{t('Read an offer letter')}</summary>
    <label>{t('Offer text')}<textarea rows={5} maxLength={12000} value={text} onChange={e=>setText(e.target.value)}/></label>
    <label>{t('Photograph or choose an offer')}<input type="file" accept="image/jpeg,image/png,image/webp" disabled={busy}
      onChange={e=>{const file=e.target.files?.[0];e.target.value='';if(file)void photo(file);}}/></label>
    <button type="button" disabled={busy||!text.trim()} className="secondary" onClick={()=>void review()}>{busy?t('Reading offer…'):t('Fill quote for review')}</button>
    {problem&&<p role="alert">{problem}</p>}
  </details>;
}
