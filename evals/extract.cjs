// Run the actual browser parser offline, with a fixed Dhaka calendar date per example.
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const esbuild = require('../web/node_modules/esbuild');
const moduleBox = { exports: {} };
const parser = esbuild.buildSync({
  entryPoints: [path.join(__dirname, '../web/src/recordParser.ts')], bundle: true,
  write: false, platform: 'node', format: 'cjs', target: 'es2022',
}).outputFiles[0].text;
vm.runInNewContext(parser, {module: moduleBox, exports: moduleBox.exports});
const rows = JSON.parse(fs.readFileSync(0, 'utf8'));
const output = rows.map(({id, text, as_of}) => {
  const [year, month, day] = as_of.split('-').map(Number);
  const draft = moduleBox.exports.parseRecordSentence(text, {
    today: new Date(year, month - 1, day, 12), currency: 'BDT', digits: 2,
  });
  return {
    id, amount_minor: draft?.amount_minor ?? null,
    direction: draft?.kind === 'invoice' ? 'due' : draft?.direction ?? null,
    party: draft?.vendor_name || null,
  };
});
process.stdout.write(JSON.stringify(output));
