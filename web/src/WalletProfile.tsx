import {useEffect,useRef,useState,type FormEvent} from 'react';
import {t} from './i18n';
import {ApiError} from './api';
import type {WalletRequest} from './WalletPayments';
type Merchant={provider:string;number:string};
type Profile={merchants:Merchant[];qr_payload:string|null;merchant_id:string|null;version:string};
const providers=[['bkash','bKash'],['nagad','Nagad'],['rocket','Rocket']] as const;

export function PaymentProfile({request,onSaved}:{request:WalletRequest;onSaved:()=>Promise<void>}){
  const [version,setVersion]=useState('');
  const [numbers,setNumbers]=useState<Record<string,string>>({});
  const [payload,setPayload]=useState('');
  const [merchantId,setId]=useState('');
  const [image,setImage]=useState('');
  const [password,setPassword]=useState('');
  const [useCode,setUseCode]=useState(false);
  const [error,setError]=useState('');
  const [message,setMessage]=useState('');
  const [busy,setBusy]=useState(false);
  const stable=useRef(request);
  function apply(profile:Profile){
    setVersion(profile.version);setNumbers(Object.fromEntries(profile.merchants.map(m=>[m.provider,m.number])));
    setPayload(profile.qr_payload||'');setId(profile.merchant_id||'');
  }
  useEffect(()=>{
    let active=true;
    stable.current<Profile>('/payments/profile').then(p=>{if(active)apply(p);}).catch(e=>{if(active)setError(e.message);});
    return ()=>{active=false;};
  },[]);
  useEffect(()=>{
    let active=true;setImage('');
    if(merchantId&&payload)void import('qrcode').then(q=>q.toDataURL(payload,{margin:4,width:240})).then(url=>{if(active)setImage(url);}).catch(()=>{});
    return ()=>{active=false;};
  },[merchantId,payload]);
  function errorText(error:unknown){
    return error instanceof ApiError&&error.code==='UNSUPPORTED_MERCHANT_QR'
      ?t('Use a static Bangla merchant QR in BDT with a readable merchant ID.'):error instanceof Error?error.message:t('Request failed.');
  }
  async function preview(){
    setBusy(true);setError('');
    try{setId((await request<{merchant_id:string}>('/payments/profile/preview-qr',{qr_payload:payload})).merchant_id);}
    catch(e){setError(errorText(e));}
    finally{setBusy(false);}
  }
  async function scan(file:File){
    setBusy(true);setError('');setPayload('');setId('');
    try{
      if(!['image/jpeg','image/png'].includes(file.type)||file.size>4*1024*1024){
        throw new Error(t('Choose a PNG or JPEG under 4 MB.'));
      }
      const image=await createImageBitmap(file);
      try{
        const factor=Math.min(1,1200/Math.max(image.width,image.height));
        const canvas=document.createElement('canvas');
        canvas.width=Math.max(1,Math.round(image.width*factor));canvas.height=Math.max(1,Math.round(image.height*factor));
        const ctx=canvas.getContext('2d',{willReadFrequently:true});
        if(!ctx)throw new Error(t('Unable to read this image. Paste its QR payload instead.'));
        ctx.drawImage(image,0,0,canvas.width,canvas.height);
        const pixels=ctx.getImageData(0,0,canvas.width,canvas.height);
        const jsQR=(await import('jsqr')).default;
        const found=jsQR(pixels.data,pixels.width,pixels.height);
        if(!found)throw new Error(t('No readable QR in this image.'));
        const verified=await request<{merchant_id:string}>('/payments/profile/preview-qr',{qr_payload:found.data});
        setPayload(found.data);setId(verified.merchant_id);
      }finally{image.close();}
    }catch(e){setError(errorText(e));}
    finally{setBusy(false);}
  }
  async function save(e:FormEvent){
    e.preventDefault();if(busy)return;
    setError('');setMessage('');setBusy(true);
    try{
      const merchants=providers.filter(([p])=>numbers[p]?.trim()).map(([provider])=>({provider,number:numbers[provider].trim()}));
      await request('/payments/profile',{merchants,qr_payload:payload||null,merchant_id:merchantId||null,...(useCode?{totp:password}:{password}),expected_version:version});
      apply(await request<Profile>('/payments/profile'));
      await onSaved();setMessage(t('Merchant details saved. Workspace members have been notified.'));
    }catch(e){setError(errorText(e));}
    finally{setBusy(false);setPassword('');}
  }
  return <form onSubmit={save}>
    {providers.map(([id,label])=><label key={id}>{label} · {t('Merchant number')}<input type="tel" value={numbers[id]||''} maxLength={16}
      onChange={e=>setNumbers({...numbers,[id]:e.target.value})}/></label>)}
    <label>{t('Upload a merchant QR image')}<input type="file" accept="image/png,image/jpeg" disabled={busy} onChange={e=>{
      const file=e.target.files?.[0];e.target.value='';if(file)void scan(file);
    }}/></label>
    <label>{t('Bangla QR payload (optional)')}<textarea rows={3} value={payload} maxLength={1024} onChange={e=>{setPayload(e.target.value.trim());setId('');}}/></label>
    {payload&&<button type="button" disabled={busy} className="secondary" onClick={()=>void preview()}>{t('Check QR merchant ID')}</button>}
    {merchantId&&<p>{t('Decoded merchant ID')} <strong>{merchantId}</strong></p>}
    {image&&<img alt={t('Merchant payment QR')} src={image} width="180" height="180"/>}
    <label className="check"><input type="checkbox" checked={useCode} onChange={e=>{setUseCode(e.target.checked);setPassword('');}}/>{t('Use an authenticator code')}</label>
    <label>{useCode?t('Authenticator code'):t('Current password')}<input type={useCode?'text':'password'} autoComplete={useCode?'one-time-code':'current-password'}
      inputMode={useCode?'numeric':undefined} maxLength={useCode?6:128} required value={password} onChange={e=>setPassword(e.target.value)}/></label>
    <button disabled={busy||!version||!password||!!payload&&!merchantId}>{t('Save merchant details')}</button>
    {error&&<p role="alert" className="error">{error}</p>}{message&&<p role="status">{message}</p>}
  </form>;
}
