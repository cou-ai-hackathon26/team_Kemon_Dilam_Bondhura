import {createHash} from 'node:crypto';
import {readFile, writeFile} from 'node:fs/promises';
const dist = new URL('../dist/', import.meta.url);
const html = await readFile(new URL('index.html',dist),'utf8');
const files = [...new Set(['/index.html','/offline.html','/offline.css','/favicon.svg','/manifest.webmanifest',
  '/icon-192.png','/icon-512.png','/icon-maskable.png','/locales/bn.json',
  ...Array.from(html.matchAll(/(?:src|href)="(\/[^"]+)"/g),match=>match[1])])];
const hash=createHash('sha256');
for(const file of files)hash.update(await readFile(new URL(file.slice(1),dist)));
const worker=new URL('sw.js',dist), source=await readFile(worker,'utf8');
hash.update(source);
const build=hash.digest('hex').slice(0,16);
await writeFile(worker,`self.CAPZORYX_BUILD=${JSON.stringify(build)};\nself.CAPZORYX_FILES=${JSON.stringify(files)};\n${source}`);
