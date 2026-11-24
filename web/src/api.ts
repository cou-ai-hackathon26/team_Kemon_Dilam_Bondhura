import {recall,remember} from './offlineCache';
import {t,T} from './i18n';
import {apiFetch,installedApp} from './apiBase';
import {currencies} from './currencies';
const messages:Record<string,string> = {
  HISAB_NOT_AVAILABLE:'Hisab-LM is temporarily unavailable. Use Read it or enter the record manually.',
  HISAB_BUSY:'Hisab-LM is busy. Try again shortly or use Read it.',
  HISAB_BDT_ONLY:'Hisab-LM currently prepares cash records in BDT workspaces.',
  HISAB_ONE_CASH_RECORD:'Describe one completed payment or receipt with an exact amount. For example, I paid Rahman 500.',
  HISAB_REVIEW_NEEDED:'Hisab-LM could not verify the amount or direction. Use Read it or enter the record manually.',
  WORKSPACE_CURRENCY_LOCKED:'This workspace already has records. Create another workspace for a different currency.',
  IMPORT_CURRENCY_MISMATCH:'The import currency must match your workspace. Change the empty workspace currency first.',
  AI_PROVIDER_REJECTED:'The AI provider rejected the request. Check its model and credentials, or use the private helper.',
  DUPLICATE_INVOICE_BLOCKED:'This invoice may be a duplicate. Review its evidence before continuing.',
  INVOICE_ALREADY_PAID:'This invoice has already been recorded as paid.',
  INSUFFICIENT_AVAILABLE_CASH:'There is not enough available cash after the required reserve.',
  PROPOSAL_EXPIRED:'This proposal expired. Review the invoice and prepare a new proposal.',
  AI_QUOTA_EXHAUSTED:'The free AI quota is temporarily exhausted. Cash flow and payment recording still work.',
  AI_NOT_CONFIGURED:'The assistant is not available yet. You can still explore the other tools.',
  AI_TIMEOUT_OR_NETWORK_FAILURE:'The assistant took too long to respond. Please try again later.',
  REQUEST_BUDGET_EXCEEDED:'Too many requests. Please wait a minute before trying again.',
  DATABASE_UNAVAILABLE:'The workspace is temporarily unavailable. Please refresh in a moment.',
  HUMAN_APPROVER_REQUIRED:'A separate approver account must review this proposal.',
  QUOTE_CURRENCY_MISMATCH:'Quotes must use the workspace record currency.',
  QUOTE_DATE_AFTER_SNAPSHOT:'The quote date cannot be later than your records snapshot date.',
  QUOTE_TERMS_INVALID:'These quote terms cannot be repaid as entered. Check the rate, term and installments.',
  OWNER_OR_APPROVER_REQUIRED:'Only the owner or approver can run financing analysis.',
  SNAPSHOT_DATE_CHANGED_REFRESH_REQUIRED:'Your records changed. Refresh data and try again.',
};
export class ApiError extends Error {
  constructor(public code: string, public status: number, message = messages[code] || (friendly[code] ? t(friendly[code]) : '') || code.replaceAll('_', ' ').toLowerCase().replace(/^./, c => c.toUpperCase()) + '.', public providerStatus?:number) { super(message); }
}
export function client(token: string, signal: AbortSignal, onUnauthorized: () => void) {
  return async function request<T>(path: string, body?: unknown): Promise<T> {
    let response: Response;
    try {
      response = await apiFetch(`/api${path}`, { method: body === undefined ? 'GET' : 'POST', credentials:'same-origin', headers: { ...(token?{Authorization: `Bearer ${token}`} : {}), 'X-SafeLedger-Client':'web', ...(body === undefined ? {} : { 'Content-Type': 'application/json' }) }, body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.any([signal, AbortSignal.timeout(90000)]), cache: 'no-store' });
    } catch (error) {
      if (signal.aborted) throw error;
      const saved = body === undefined ? recall<T>(path) : null;  // Android app offline: the last data this screen loaded
      if (saved) { window.dispatchEvent(new CustomEvent('capzoryx:offline-data', {detail: saved.at})); return saved.data; }
      throw new ApiError('CONNECTION_FAILED', 0, 'Could not reach the server. Check the connection and retry. A timed-out action may have completed; refresh before retrying.');
    }
    const data = await response.json().catch(() => null);
    // An old session must not sign out a newly mounted workspace after cancellation.
    signal.throwIfAborted();
    if (!response.ok) {
      if (response.status === 401) onUnauthorized();
      const fields = data?.error?.fields as {field: string; message: string}[] | undefined;
      throw new ApiError(data?.error?.code || 'SERVER_ERROR', response.status, fields ? fields.map(x => `${String(x.field).split('.').pop()?.replaceAll('_', ' ')}: ${x.message}`).join('; ') : undefined,typeof data?.provider_http_status==='number'?data.provider_http_status:undefined);
    }
    if (!data || data.simulation_only !== true) throw new ApiError('INVALID_SERVER_RESPONSE', 502);
    if (body === undefined) { remember(path, data); if (installedApp) window.dispatchEvent(new Event('capzoryx:live-data')); }
    return data as T;
  };
}
export {currencyDigits,money,parseMinor} from './money';
import {currencyDigits} from './money';

const friendly: Record<string,string> = {
  PRODUCT_EXISTS:T('You already added this product. Edit it in the list instead.'),
  PRODUCT_NOT_FOUND:T('That product no longer exists. Refresh the page.'),
  PRODUCT_LIMIT_REACHED:T('You have reached the product limit for this workspace.'),
  IDENTITY_REQUIRED_FIRST:T('Fill in the first step (business name, what you sell and where) first.'),
  RESEARCH_RECENT:T('Your market was researched a moment ago. New research runs automatically when your business details change.'),
  RESEARCH_CONSENT_REQUIRED:T('Online research is turned off. Turn it on in My business.'),
  PROFILE_REQUIRED:T('Set up your business first.'),
  SECOND_PERSON_APPROVAL_REQUIRED:T('This workspace has an approver, so they must approve this payment.'),
  SOLO_APPROVAL_CONFIRMATION_REQUIRED:T('Tick the box to confirm you checked the supplier and the amount.'),
  NOTE_LIMIT:T('You have shared the most business notes a workspace can hold. Delete old ones to add more.'),
  NOTE_NOT_FOUND:T('That business note was already deleted. Refresh the page.'),
  INDICATOR_NOT_WATCHED:T('That price is not followed for this business.'),
  NAME_COLUMN_REQUIRED:T('The file needs a column called Product (or Name).'),
  EMPTY_FILE:T('The file is empty.'),
  INVALID_USERNAME_OR_PASSWORD: 'Check your username and password. Usernames are not case sensitive.',
  USERNAME_UNAVAILABLE: 'That username is already in use. Try another one.',
  REQUEST_BUDGET_EXCEEDED: 'A few too many requests. Please wait a minute and try again.',
  INVITATION_INVALID_OR_EXPIRED: 'This invitation has expired or was already used. Ask the workspace owner for a new one.',
  INVALID_RECOVERY_DETAILS:'The username and recovery code do not match. Check your saved code.',
  ALREADY_A_WORKSPACE_MEMBER: 'You already belong to that workspace. Select it from the workspace list.',
  CURRENT_PASSWORD_INCORRECT: 'Your current password is not correct.',
  PASSWORD_TOO_WEAK: 'That password is too easy to guess. Choose a longer, less predictable one that is not your username.',
  PASSWORD_UNCHANGED: 'Choose a new password that is different from the current one.',
  INVALID_CODE: 'That code is not correct or has expired. Check it and try again.',
  INVALID_RESET_CODE: 'That code is not correct or has expired. Request a new one.',
  CHALLENGE_EXPIRED: 'This sign-in step expired. Enter your password again.',
  SECOND_FACTOR_REQUIRED: 'This account uses two-factor sign-in. Also enter a code from your authenticator app.',
  EMAIL_NOT_CONFIGURED: 'Email codes are not switched on for this site yet. Use your recovery key instead.',
  INVALID_EMAIL: 'Enter a valid email address.',
  EMAIL_ALREADY_USED: 'That email address already belongs to another account.',
  EMAIL_NOT_SET: 'Add an email address first.',
  TWO_FACTOR_ALREADY_ON: 'Two-factor sign-in is already on.',
  TWO_FACTOR_SETUP_REQUIRED: 'Start the two-factor setup again.',
  TOO_MANY_OPEN_INVITATIONS: 'You have too many open invitations. Cancel some first.',
  OWNER_CANNOT_BE_CHANGED: 'The owner of a workspace cannot be changed.',
  OWNER_CANNOT_LEAVE: 'Owners cannot leave their own workspace. Clear it instead.',
  MEMBER_NOT_FOUND: 'That person is no longer in this workspace.',
  WORKSPACE_CLEARED: 'This workspace was cleared. Choose another one.',
  RECORDKEEPER_REQUIRED: 'The owner and bookkeepers can add records.',
  WORKSPACE_OWNER_REQUIRED: 'Only the owner of a workspace can do this.',
  RESTORE_WINDOW_ENDED: 'This data was cleared more than 30 days ago and can no longer be restored.',
  SMART_ASSIST_OFF: 'Smart assist is turned off. Turn it on in Settings to use this.',
  WORKSPACE_OWNER_SMART_ASSIST_OFF: 'The owner of this workspace has turned smart assist off for their records.',
  AI_NOT_CONFIGURED: 'Smart assist is not available right now.',
  FX_UNAVAILABLE: 'Live exchange rates are not reachable right now. Try again shortly, or enter a rate yourself.',
  FX_PAIR_UNAVAILABLE: 'No live rate is available for that currency pair.',
  IMAGE_FORMAT_UNSUPPORTED: 'Use a JPG, PNG or WebP photo under about 1 MB.',
  PHOTO_FORMAT_UNSUPPORTED: 'Use a JPG, PNG or WebP picture.',
  SERVER_BUSY: 'The server is busy. Please try again in a moment.',
  TOO_MANY_FAILED_ATTEMPTS: 'Too many unsuccessful attempts. For your protection, wait 15 minutes and try again.',
  PLEASE_TRY_AGAIN: 'The server is busy securing another sign-in. Please try again in a few seconds.',
  ACCOUNT_CAPACITY_REACHED: 'New sign-ups are temporarily full. Please try again later.',
  UNTRUSTED_BROWSER_ORIGIN: 'This request came from an unrecognised site. Open Capzoryx Copilot from its official address.',
};
/** Sign-in and account errors carry their code so screens can translate them and show how long to wait. */
export class AuthError extends Error {
  constructor(message:string,public code:string,public status:number,public retryAfter=0){super(message);}
}
export async function accountRequest<T>(path:string, body?:unknown, signal?:AbortSignal):Promise<T> {
  let r:Response;
  try{
    r = await apiFetch('/api'+path,{method:body===undefined?'GET':'POST',credentials:'same-origin',headers:{'Content-Type':'application/json','X-SafeLedger-Client':'web'},body:body===undefined?undefined:JSON.stringify(body),signal:signal?AbortSignal.any([signal,AbortSignal.timeout(90000)]):AbortSignal.timeout(90000)});
  }catch(error){
    if(signal?.aborted)throw error;
    throw new AuthError('Could not reach the server. Check your connection and try again.','CONNECTION_FAILED',0);
  }
  const data=await r.json().catch(()=>null);
  if(!r.ok){
    const code=typeof data?.error?.code==='string'?data.error.code:'';
    // A bare 502/503/504 with no JSON error is the free server still waking behind the proxy, not a wrong password.
    if(!code&&[502,503,504].includes(r.status))throw new AuthError('The server is waking up. Please try again in a moment.','SERVER_WAKING',r.status);
    const retryAfter=typeof data?.error?.retry_after==='number'?data.error.retry_after:0;
    throw new AuthError(friendly[code] || (r.status===422&&data?.error?.fields?.some((f:{field?:string})=>/email/.test(f.field||''))?'Enter a valid email address.':null) || (r.status===422?(data?.error?.fields?.some((f:{message?:string})=>/less common/i.test(f.message||''))?'That password is too easy to guess. Choose a longer, less predictable one that is not your username.':'Check your username and use a password of at least 8 characters.'):code.replaceAll('_',' ') || 'The server is unavailable. Please try again.'),code,r.status,retryAfter);
  }
  if(data?.simulation_only!==true)throw new AuthError('Unexpected server response. Please try again.','INVALID_SERVER_RESPONSE',r.status);
  return data as T;
}

/** Live exchange rate for the currency view. Display only; the server fetches and caches it. */
export async function fxRate(from:string,to:string,signal?:AbortSignal){
  const r=await apiFetch(`/api/fx/rate?from=${encodeURIComponent(from)}&to=${encodeURIComponent(to)}`,{credentials:'same-origin',headers:{'X-SafeLedger-Client':'web'},signal:signal?AbortSignal.any([signal,AbortSignal.timeout(20000)]):AbortSignal.timeout(20000),cache:'no-store'});
  const data=await r.json().catch(()=>null);
  if(!r.ok)throw new Error(friendly[data?.error?.code]||'Live exchange rates are not reachable right now.');
  if(data?.simulation_only!==true||typeof data.rate!=='string')throw new Error('Unexpected exchange-rate response.');
  return data as import('./types').FxRate;
}
