import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {execFileSync} from 'node:child_process';
import {fixture} from './shotcraft-fixture.mjs';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const repo=path.resolve(root,'../..');
const browser=path.join(repo,'runtime/hyperframes/.cache/hyperframes/chrome/chrome-headless-shell/win64-131.0.6778.85/chrome-headless-shell-win64/chrome-headless-shell.exe');
fs.mkdirSync(path.join(root,'.cache'),{recursive:true});
const output=fs.mkdtempSync(path.join(root,'.cache','shotcraft-smoke-'));
const input=fixture();
if(process.argv.includes('--dark')){
  input.plan.palette={background:'#102330',surface:'#182E3C',ink:'#EDF5F9',muted:'#A8C2D0',line:'#476373',accent:'#79D5EB'};
  input.plan.background={kind:'soft_gradient'};input.plan.typography='serif';
}
if(process.argv.includes('--asset')){
  input.assets.source=path.join(repo,'workspace/vibe_lab/vendor/video-shotcraft/gallery/media/poster/word-relay-filmstrip.jpg');
  input.plan.stages[0].items[0].asset_id='source';input.plan.stages[1].items[1].asset_id='source';
}
if(process.argv.includes('--text-only')){
  for(const stage of input.plan.stages)for(const item of stage.items)item.motif='none';
}
if(process.argv.includes('--minimum')){
  input.plan.canvas=[1280,800];
  let start=0;
  for(const stage of input.plan.stages){stage.start=start;stage.end=start+(stage.recipe==='spotlight-hero-card'?4:stage.recipe==='word-relay-filmstrip'?stage.items.length*1.7:2.5);start=stage.end;}
  input.duration=start;input.plan.duration=start;
}
if(process.argv.includes('--overflow'))input.plan.stages[0].body='完整保留文字，不允许静默省略。'.repeat(16);
const inputPath=path.join(output,'input.json');fs.writeFileSync(inputPath,JSON.stringify(input,null,2));
const args=[path.join(root,'render.mjs'),'--browser',browser,'--input',inputPath,'--output',path.join(output,'render')];
if(!process.argv.includes('--video'))args.push('--stills-only');
try{
  let outputText;
  try{outputText=execFileSync(process.execPath,args,{cwd:root,encoding:'utf8',timeout:240000,maxBuffer:4*1024*1024});}
  catch(error){
    if(process.argv.includes('--overflow')&&String(error.stderr??error.stdout??error).includes('OCV_LAYOUT_OVERFLOW')){
      console.log('Expected OCV_LAYOUT_OVERFLOW rejection confirmed.');outputText='';
    }else throw error;
  }
  if(process.argv.includes('--overflow')&&outputText)throw new Error('Overflow fixture unexpectedly rendered');
  console.log(outputText);
}
finally{console.log('SHOTCRAFT_SMOKE_OUTPUT='+output);}
