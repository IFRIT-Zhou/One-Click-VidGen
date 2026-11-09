// Opt-in real-browser smoke test. Creates only synthetic fixtures under --output.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {spawnSync} from 'node:child_process';
import {BACKGROUND_KINDS} from '../background.mjs';

const args=process.argv.slice(2);
const option=name=>args[args.indexOf(name)+1];
const browser=option('--browser'),output=option('--output');
if(!browser||!output||!path.isAbsolute(browser)||!path.isAbsolute(output)) throw new Error('Absolute --browser and --output required');
if(fs.existsSync(output)&&fs.readdirSync(output).length) throw new Error('Smoke output must be empty');
fs.mkdirSync(output,{recursive:true});
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
for(const kind of BACKGROUND_KINDS) {
  const input={duration:4,fps:30,assets:{},plan:{version:'ocv-motion-v1',canvas:[1280,720],theme:'paper',
    palette:{background:'#081b30',ink:'#edf4fa',muted:'#a2bed5',surface:'#102941',line:'#4a718f',accent:'#e6ac52'},
    background:{kind},title:'原分镜配色与背景风格',subtitle:`${kind} · 离线渲染测试`,
    stages:[{id:'a',layout:'findings',start:0,end:4,headline:'深蓝色原生背景',
      items:[{id:'x',title:'保持配色',body:'背景层不依赖图片，不改变内容。'},{id:'y',title:'轻量纹理',body:'保留视觉风格，同时保证文字清楚。'}]}]}};
  const inputFile=path.join(output,kind+'.json');fs.writeFileSync(inputFile,JSON.stringify(input));
  const result=spawnSync(process.execPath,[path.join(root,'render.mjs'),'--input',inputFile,'--output',path.join(output,kind),
    '--browser',browser,'--stills-only','--concurrency','1'],{encoding:'utf8',windowsHide:true,timeout:120000});
  if(result.status!==0)throw new Error(result.stderr||result.error?.message||'Smoke render failed');
  const metadata=JSON.parse(fs.readFileSync(path.join(output,kind,'metadata.json'),'utf8'));
  if(metadata.width!==1280||metadata.height!==720||metadata.stage_frames.length!==1)throw new Error('Smoke metadata mismatch');
  console.log(`${kind}: rendered ${metadata.stage_frames[0].file}, ${metadata.render_seconds}s`);
}
