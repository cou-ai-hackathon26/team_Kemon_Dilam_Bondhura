const assert=require('node:assert/strict'),path=require('node:path'),vm=require('node:vm');
const esbuild=require('../web/node_modules/esbuild');
const box={exports:{}};
const compiled=esbuild.buildSync({entryPoints:[path.join(__dirname,'../web/src/hisabTagger.ts')],bundle:true,format:'cjs',platform:'node',write:false});
vm.runInNewContext(compiled.outputFiles[0].text,{module:box,exports:box.exports});
const {features,tokenize,decode,interpret}=box.exports;
const {rows,vectors}=require('../ml/tagger/vectors.json');
for(const [text,tokens,ids] of vectors){
  assert.equal(JSON.stringify(tokenize(text)),JSON.stringify(tokens));
  assert.equal(JSON.stringify(features(tokenize(text))),JSON.stringify(ids.map(i=>rows[i])));
}
// Both paths have nonzero probability; an exhaustive two-token, two-label sum checks marginals.
const model={format:1,version:'test',labels:['A','B'],weights:{'w=x':{A:0.4},'w=y':{B:1.1}},transitions:{A:{A:0.7},B:{A:0.3}}};
const join=decode('x y',model);
const aa=Math.exp(1.1),ab=Math.exp(1.5),ba=Math.exp(0.3),bb=Math.exp(1.1),z=aa+ab+ba+bb;
assert.equal(join.map(x=>x.label).join(''),'AB');
assert.ok(Math.abs(join[0].confidence-(aa+ab)/z)<1e-10);
assert.ok(Math.abs(join[1].confidence-(ab+bb)/z)<1e-10);
const spans=(rows)=>rows.map(([token,label])=>({token,label,confidence:0.99}));
const words=spans([['Mita','PARTY'],['ke','O'],['4','AMOUNT'],['lakh','MULT'],['20','AMOUNT'],['hajar','MULT'],['dilam','DIR_OUT']]);
assert.equal(interpret(words,2).amountMinor,42000000);
words[2].confidence=0.79;
assert.equal(interpret(words,2),null);
assert.equal(interpret(spans([['due','DUE'],['paid','DIR_OUT'],['12','AMOUNT']]),2),null);
assert.equal(interpret(spans([['paid','DIR_OUT'],['12','AMOUNT'],['and','O'],['15','AMOUNT']]),2),null);
assert.equal(interpret(spans([['১','AMOUNT'],['.','AMOUNT'],['৫k','AMOUNT'],['দিলাম','DIR_OUT']]),2).amountMinor,150000);
assert.equal(interpret(spans([['12','AMOUNT'],[',','AMOUNT'],['34','AMOUNT'],['paid','DIR_OUT']]),2),null);
console.log(vectors.length+' Python/browser token and feature vectors, Viterbi and marginals passed');
