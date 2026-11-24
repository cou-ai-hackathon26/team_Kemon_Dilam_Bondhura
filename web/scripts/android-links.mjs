import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';

const directory=resolve(process.argv[2]||'dist','.well-known');
const fingerprint=process.env.CAPZORYX_ANDROID_CERT_SHA256?.trim().toUpperCase();
if(fingerprint&&!/^([A-F0-9]{2}:){31}[A-F0-9]{2}$/.test(fingerprint)){
  throw new Error('CAPZORYX_ANDROID_CERT_SHA256 must be a colon-separated SHA-256 certificate fingerprint.');
}
const links=fingerprint?[{
  relation:['delegate_permission/common.handle_all_urls'],
  target:{namespace:'android_app',package_name:'com.capzoryx.copilot',sha256_cert_fingerprints:[fingerprint]}
}]:[];
await mkdir(directory,{recursive:true});
await writeFile(resolve(directory,'assetlinks.json'),JSON.stringify(links,null,2)+'\n');
