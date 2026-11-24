import {copyFile, mkdir} from 'node:fs/promises';
const root = new URL('../', import.meta.url);
const target = new URL('public/ocr/', root);
await mkdir(target, {recursive:true});
for (const [source, name] of [
  ['tesseract.js/dist/worker.min.js', 'worker.min.js'],
  ['tesseract.js-core/tesseract-core-lstm.wasm.js', 'core.wasm.js'],
  ['tesseract.js-core/LICENSE', 'LICENSE-core.txt'],
  ['tesseract.js/LICENSE.md', 'LICENSE-worker.txt'],
  ...['eng','ben'].map(lang => [`@tesseract.js-data/${lang}/4.0.0_best_int/${lang}.traineddata.gz`, `${lang}.traineddata.gz`])
]) {
  await copyFile(new URL('node_modules/' + source, root), new URL(name, target));
}
