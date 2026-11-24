// Loaded only after the user requests dictation. This worker receives no account identifiers.
const packs = {
  en:{version:'whisper-tiny-v1', core:'sherpa-onnx-wasm-main-vad-asr',
    models:['tiny-encoder.int8.onnx','tiny-decoder.int8.onnx','tiny-tokens.txt']},
  bn:{version:'vosk-bn-v1', core:'sherpa-onnx-wasm-main-asr',
    models:['encoder.onnx','decoder.onnx','joiner.onnx','tokens.txt']},
};
let recognizer;
let language;
let initialized = false;
// The runtime ships sample weights in .data. Supply an empty sample bundle and mount
// the selected pretrained model under its own names.
var Module = {getPreloadedPackage: () => new ArrayBuffer(0), print: () => {}, printErr: () => {}};

async function load(allowDownload, lang) {
  if (initialized) {
    if (language !== lang) throw new Error('Reopen speech pack when switching language');
    return 'ready';
  }
  const {version,core,models} = packs[lang], path='/voice-pack/'+version+'/';
  const binaries=[core+'.wasm',...models];
  const cache = await caches.open('capzoryx-speech-'+version);
  const missing = [];
  for (const name of binaries) if (!await cache.match(path + name)) missing.push(name);
  if (missing.length && !allowDownload) return 'download-needed';
  if (missing.length) {
    const response = await fetch(path + 'manifest.json');
    if (!response.ok) throw new Error('Speech pack unavailable');
    const manifest = await response.json();
    if (manifest.version !== version) throw new Error('Speech pack version mismatch');
    for (const name of missing) {
      const file = manifest.files[name];
      const response = await fetch(path + name, {credentials:'omit'});
      if (!response.ok) throw new Error('Speech download failed');
      const bytes = await response.arrayBuffer();
      const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)),
        value => value.toString(16).padStart(2, '0')).join('');
      if (bytes.byteLength !== file.bytes || digest !== file.sha256) throw new Error('Speech download incomplete');
      await cache.put(path + name, new Response(bytes));
    }
  }
  Module.wasmBinary = await (await cache.match(path + binaries[0])).arrayBuffer();
  await new Promise((resolve, reject) => {
    Module.onRuntimeInitialized = resolve;
    Module.onAbort = reject;
    importScripts(path + 'sherpa-onnx-asr.js', path + core + '.js');
  });
  for (const name of models) {
    const bytes = new Uint8Array(await (await cache.match(path + name)).arrayBuffer());
    Module.FS_createDataFile('/', 'pack-'+name, bytes, true, false, true);
  }
  const names=models.map(name=>'pack-'+name);
  if (lang==='bn') {
    const [encoder,decoder,joiner,tokens]=names;
    recognizer=new OnlineRecognizer({featConfig:{sampleRate:16000,featureDim:80},
      modelConfig:{tokens,transducer:{encoder,decoder,joiner},modelType:'zipformer2',numThreads:1,debug:0}},Module);
  } else {
    const [encoder,decoder,tokens]=names;
    recognizer=new OfflineRecognizer({modelConfig:{tokens,numThreads:1,debug:0,
      whisper:{encoder,decoder,language:'en',task:'transcribe'}}},Module);
  }
  if (!recognizer.handle) throw new Error('Device cannot start speech recognition');
  language=lang;initialized=true;
  return 'ready';
}

self.onmessage = async ({data}) => {
  try {
    if (data.type === 'prepare' && ['bn','en'].includes(data.lang)) {
      self.postMessage({type:await load(data.download === true, data.lang)});
      return;
    }
    if (data.type !== 'audio' || !recognizer || !(data.samples instanceof Float32Array)
      || data.samples.length < 4000 || data.samples.length > 30 * 16000) throw new Error('Invalid clip');
    const stream = recognizer.createStream();
    try {
      stream.acceptWaveform(16000, data.samples);
      if (language==='bn') {
        // The streaming encoder needs right-context frames to emit the last word.
        stream.acceptWaveform(16000,new Float32Array(12800));
        stream.inputFinished();
        while(recognizer.isReady(stream))recognizer.decode(stream);
      } else recognizer.decode(stream);
      const text = recognizer.getResult(stream).text.trim().slice(0, 400);
      // A Latin-script hallucination is not a Bengali dictation draft.
      self.postMessage({type:'transcript', text:language === 'bn' && !/[অ-হ]/u.test(text) ? '' : text});
    } finally {stream.free();}
  } catch {self.postMessage({type:'error'});}
};
