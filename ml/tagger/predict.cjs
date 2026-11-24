// Produce the unassisted candidate's extraction fields without giving labels to the decoder.
const fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const esbuild=require('../../web/node_modules/esbuild');
const [modelFile,inputFile]=process.argv.slice(2);
if(!modelFile||!inputFile){console.error('Usage: node ml/tagger/predict.cjs MODEL.json DATA.jsonl');process.exit(2);}
const box={exports:{}};
const source=esbuild.buildSync({entryPoints:[path.join(__dirname,'../../web/src/hisabTagger.ts')],platform:'node',bundle:true,write:false,format:'cjs'});
vm.runInNewContext(source.outputFiles[0].text,{module:box,exports:box.exports});
const {decode,interpret}=box.exports,model=JSON.parse(fs.readFileSync(modelFile,'utf8'));
for(const line of fs.readFileSync(inputFile,'utf8').split(/\r?\n/).filter(Boolean)){
  const {id,text}=JSON.parse(line);
  const fields=interpret(decode(text,model),2);
  console.log(JSON.stringify({id,amount_minor:fields?.amountMinor??null,direction:fields?.direction??null,party:fields?.party||null}));
}
