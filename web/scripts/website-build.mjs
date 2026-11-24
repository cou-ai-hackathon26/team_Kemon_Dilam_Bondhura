import {mkdir,writeFile} from 'node:fs/promises';

// Remote QA reads this from the frontend and compares it to the separately deployed API.
const commit=process.env.VERCEL_GIT_COMMIT_SHA||process.env.RENDER_GIT_COMMIT;
if(commit){
  if(!/^[a-f0-9]{40}$/.test(commit))throw new Error('Invalid website Git revision');
  await mkdir('dist/.well-known',{recursive:true});
  await writeFile('dist/.well-known/capzoryx-website.json',JSON.stringify({commit})+'\n');
}
