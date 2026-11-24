import {t} from './i18n';
import type {Draft} from './types';

export type HisabDraftResult={draft:Draft;source?:'hisab_lm'|'api_backup'|'verified'};

export function hisabDraftMessage(source:HisabDraftResult['source']){
  if(source==='verified')return t('Draft prepared from the details in your message.');
  if(source==='api_backup')return t('Draft prepared with online backup. Check it before saving.');
  return t('Draft prepared by Hisab-LM.');
}

export function hisabPrivacyNote(allowBackup:boolean){
  if(allowBackup)return t('This sends the sentence to Hisab-LM on private Hugging Face hosting. If needed, the approved online backup can read the same sentence.');
  return t('Hisab-LM sends this sentence to a private Hugging Face service to draft one BDT cash record. Review it before saving.');
}
