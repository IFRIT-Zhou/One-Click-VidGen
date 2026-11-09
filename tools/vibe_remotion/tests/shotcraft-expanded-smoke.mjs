import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {expandedFixture,fixtureBatches} from './shotcraft-expanded-fixture.mjs';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..'),repo=path.resolve(root,'../..');
const browser=path.join(repo,'runtime/hyperframes/.cache/hyperframes/chrome/chrome-headless-shell/win64-131.0.6778.85/chrome-headless-shell-win64/chrome-headless-shell.exe');
fs.mkdirSync(path.join(root,'.cache'),{recursive:true});
const output=fs.mkdtempSync(path.join(root,'.cache','shotcraft-twelve-'));
// Known local original image, no remote download and no model call.
const arg=process.argv.indexOf('--asset'),asset=arg>=0?path.resolve(process.argv[arg+1]):path.join(repo,'tools/vibe_shotcraft_library/previews/spotlight-hero-card.jpg');
if(!fs.existsSync(asset))throw new Error('Supply a local --asset for original-image regression');
const input=expandedFixture({dark:process.argv.includes('--dark'),maximum:process.argv.includes('--maximum'),asset});
console.log('SHOTCRAFT_TWELVE_OUTPUT='+output);
for(const [i,batch] of fixtureBatches(input).entries()){
  const inputPath=path.join(output,`input_${i}.json`);fs.writeFileSync(inputPath,JSON.stringify(batch,null,2));
  const args=[path.join(root,'render.mjs'),'--browser',browser,'--input',inputPath,'--output',path.join(output,`render_${i}`)];
  if(!process.argv.includes('--video'))args.push('--stills-only');
  console.log(execFileSync(process.execPath,args,{cwd:root,encoding:'utf8',timeout:300000,maxBuffer:4*1024*1024}));
}
