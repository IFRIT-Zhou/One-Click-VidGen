// Isolated synthetic assets only. No project state or model/provider calls.
import fs from 'node:fs';
import path from 'node:path';
import {fileURLToPath} from 'node:url';
import {deflateSync} from 'node:zlib';
import {spawnSync} from 'node:child_process';
import assert from 'node:assert/strict';

const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const repo=path.resolve(root,'../..');
const browser=path.join(repo,'runtime/hyperframes/.cache/hyperframes/chrome/chrome-headless-shell/win64-131.0.6778.85/chrome-headless-shell-win64/chrome-headless-shell.exe');
const cache=path.join(root,'.cache');fs.mkdirSync(cache,{recursive:true});
const output=fs.mkdtempSync(path.join(cache,'layered-smoke-'));
const crc32=data=>{let c=0xffffffff;for(const b of data){c^=b;for(let n=0;n<8;n++)c=(c>>>1)^((c&1)?0xedb88320:0);}return(c^0xffffffff)>>>0;};
const chunk=(type,data)=>{const name=Buffer.from(type),size=Buffer.alloc(4),crc=Buffer.alloc(4);size.writeUInt32BE(data.length);crc.writeUInt32BE(crc32(Buffer.concat([name,data])));return Buffer.concat([size,name,data,crc]);};
function png(file,width,height,pixel){
  const ihdr=Buffer.alloc(13);ihdr.writeUInt32BE(width);ihdr.writeUInt32BE(height,4);ihdr[8]=8;ihdr[9]=6;
  const raw=Buffer.alloc(height*(width*4+1));
  for(let y=0;y<height;y++)for(let x=0;x<width;x++)raw.set(pixel(x,y),y*(width*4+1)+1+x*4);
  fs.writeFileSync(file,Buffer.concat([Buffer.from([137,80,78,71,13,10,26,10]),chunk('IHDR',ihdr),chunk('IDAT',deflateSync(raw)),chunk('IEND',Buffer.alloc(0))]));
}
const assets=Object.fromEntries(['background','professor','gold_patch'].map(id=>[id,path.join(output,id+'.png')]));
png(assets.background,320,180,(x,y)=>[24+Math.floor(x/25),47+Math.floor(y/18),63+Math.floor(x/40),255]);
png(assets.professor,160,260,(x,y)=>{
  const head=(x-80)**2+(y-52)**2<37**2,body=y>90&&Math.abs(x-80)<70*(1-(y-90)/700);
  return head?[213,180,147,255]:body?[193,211,218,255]:[0,0,0,0];
});
png(assets.gold_patch,300,70,(x,y)=>y>7+(x%29)/9&&y<63-(x%23)/11?[190+Math.floor(x/8)%35,149+Math.floor(y/7),65,255]:[0,0,0,0]);
const input={duration:4,fps:30,assets,plan:{version:'ocv-layered-v1',canvas:[1280,720],background:'#182F3F',fade_in:0,fade_out:0,elements:[
  {id:'background',type:'image',role:'background',box:[0,0,1280,720],at:0,enter:0,exit:0,motion:'none',asset_id:'background',fit:'cover'},
  {id:'professor',type:'image',box:[80,40,470,680],at:.1,enter:.6,motion:'fade',asset_id:'professor',fit:'contain',shadow:'soft'},
  {id:'gold_patch',type:'image',box:[74,552,486,112],at:.6,enter:.45,motion:'slide_up',asset_id:'gold_patch',fit:'contain',rotation:-2},
  {id:'name',type:'text',box:[125,571,390,70],at:.6,enter:.45,motion:'slide_up',text:'透明图层 · 原生文字',font_size:29,bold:true,fill:'#102536',rotation:-2},
  {id:'institutions',type:'text',box:[650,210,540,240],at:1.2,enter:.55,motion:'slide_right',text:'机构信息分层排版\n按最终配音逐步出现',font_size:44,bold:true,line_height:1.5,fill:'#F4EFE3'},
  {id:'note',type:'text',box:[650,490,540,90],at:1.8,enter:.5,text:'离线合成测试：背景、人物与贴片为三个独立资产。',font_size:24,line_height:1.5,fill:'#B8CCD6'},
]}};
const feather=process.argv.includes('--feather');
const readability=process.argv.includes('--readability');
const fit=process.argv.includes('--fit');
if(fit){
  input.plan.elements=[input.plan.elements[0],
    ...[211,231].flatMap((width,index)=>[
      {id:'plate_'+index,type:'rect',box:[250+index*500,230,300,220],fill:'#072D4D',
        gradient:{color:'#124E70',angle:180},stroke:'#43DBED',stroke_width:2,radius:18,glow:5},
      {id:'value_'+index,type:'text',box:[280+index*500,270,width,130],text:index?'12.1个月':'11.8个月',
        font_size:72,min_font_size:43,bold:true,line_height:1.2,align:'center',valign:'middle',fill:'#F9FDFF'},
    ])];
}
if(readability){
  png(assets.background,8,8,()=>[20,30,40,255]);
  input.plan.canvas=[640,240];
  input.plan.elements=[{...input.plan.elements[0],box:[0,0,640,240]},
    ...['left','center','right'].map((align,index)=>({
      id:'label_'+align,type:'text',box:[18+index*207,40,180,155],at:.6,enter:.4,motion:'slide_up',
      text:'ABCDE\nA',font_size:28,bold:true,line_height:1.8,align,fill:'#141E28',
      readability_halo:{color:'#E3EAF0',opacity:.92}
    }))];
}
if(feather){
  // 80:160 source in a 240:128 box -> 64px visible width, x=128..192;
  // a 25% fade should affect x=176..192, not the empty right-hand gutter.
  png(assets.professor,80,160,(_x,y)=>[228,140,80,y>=100?128:255]);
  input.plan.canvas=[320,180];
  input.plan.elements=[
    {...input.plan.elements[0],box:[0,0,320,180]},
    {id:'coat',type:'image',asset_id:'professor',box:[40,20,240,128],fit:'contain',enter:0,motion:'none',edge_fade_right:.25},
    {id:'chip',type:'rect',box:[184,58,10,12],fill:'#00ff00',enter:0,motion:'none'},
  ];
}
if(process.argv.includes('--overflow'))Object.assign(input.plan.elements[4],{text:'长文字不能静默截断。'.repeat(20),box:[650,210,140,40],font_size:44});
const inputPath=path.join(output,'input.json');fs.writeFileSync(inputPath,JSON.stringify(input,null,2));
const args=[path.join(root,'render.mjs'),'--browser',browser,'--input',inputPath,'--output',path.join(output,'render'),'--concurrency','1'];
if(!process.argv.includes('--video'))args.push('--stills-only');
const result=spawnSync(process.execPath,args,{cwd:root,encoding:'utf8',windowsHide:true,timeout:240000,maxBuffer:4*1024*1024});
try{
  if(process.argv.includes('--overflow')){
    assert.notEqual(result.status,0);assert.match(result.stdout+result.stderr,/OCV_LAYOUT_OVERFLOW/);
    console.log('PASS: expected layered text overflow rejection.');
  }else if(fit){
    assert.equal(result.status,0,result.stderr||result.error?.message);
    const metadata=JSON.parse(fs.readFileSync(path.join(output,'render','metadata.json'),'utf8'));
    assert.equal(metadata.checks.layout,'passed');
    assert.deepEqual(input.plan.elements.filter(e=>e.type==='text').map(e=>e.text),['11.8个月','12.1个月']);
    console.log('PASS: real Chromium renders both 211x130 and 231x130 numerical text frames with bounded fitting, gradient cards and centered text.');
  }else if(readability){
    assert.equal(result.status,0,result.stderr||result.error?.message);
    const pixels=name=>{
      const f=spawnSync(path.join(repo,'tools/ffmpeg/bin/ffmpeg.exe'),['-hide_banner','-loglevel','error','-i',path.join(output,'render',name),'-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','pipe:1'],{windowsHide:true,maxBuffer:1024*1024});
      assert.equal(f.status,0,f.stderr?.toString());return (x,y)=>f.stdout[3*(y*640+x)];
    };
    const r=pixels('detail_middle.png'),early=pixels('detail_early.png');
    for(let pos=0;pos<3;pos++){
      const x0=18+207*pos;
      const extent=(y1,y2)=>{
        const lit=Array.from({length:180},(_,x)=>x).filter(x=>{
          for(let y=y1;y<=y2;y++)if(r(x0+x,y)>110)return true;
          return false;
        });
        assert.ok(lit.length>5);return [lit[0],lit.at(-1)];
      };
      const [a,b]=extent(51,77),[c,d]=extent(102,128);
      assert.ok(b-a>d-c+45,'Each line follows its actual glyph span, not the longest line or label box');
      if(pos===0)assert.ok(a<8&&c<8&&d<45,'Left alignment');
      if(pos===1)assert.ok(Math.abs((a+b)/2-90)<5&&Math.abs((c+d)/2-90)<5,'Center alignment');
      if(pos===2)assert.ok(b>171&&d>171&&c>135,'Right alignment');
      assert.equal(early(x0+90,55),20,'Backing must not appear before the native animation');
    }
    assert.equal(r(200,180),20,'Unused label area must retain original pixels');
    assert.ok(r(70,36)>23&&r(70,36)<110,'Soft margin must blend rather than creating an opaque strip');
    console.log('PASS: Chromium: protected text and aligned per-line fades; empty space, pre-entry and surrounding pixels unchanged.');
  }else if(feather){
    assert.equal(result.status,0,result.stderr||result.error?.message);
    const ffmpeg=path.join(repo,'tools/ffmpeg/bin/ffmpeg.exe');
    const frame=spawnSync(ffmpeg,['-hide_banner','-loglevel','error','-i',path.join(output,'render','detail_middle.png'),'-frames:v','1','-f','rawvideo','-pix_fmt','rgba','pipe:1'],{windowsHide:true,maxBuffer:1024*1024});
    assert.equal(frame.status,0,frame.stderr?.toString());
    const at=(x,y)=>Array.from(frame.stdout.subarray(4*(320*y+x),4*(320*y+x)+3));
    const r=(x,y)=>at(x,y)[0];
    assert.ok(r(170,80)>224,'Unfeathered middle must keep its RGB');
    assert.ok(r(183,80)>120&&r(183,80)<145,'Middle of 16px feather must reveal background');
    assert.ok(r(191,80)<48&&r(196,80)<48,'Portrait edge should blend into background, with no rectangular rim');
    assert.ok(r(169,120)>122&&r(169,120)<145,'Existing partial transparency must survive');
    assert.deepEqual(at(188,62),[0,255,0],'A separate foreground component cannot inherit coat feather');
    console.log('PASS: real Chromium pixels validate content-space feathering, transparency and independent layers.');
  }else{
    assert.equal(result.status,0,result.stderr||result.error?.message);
    const metadata=JSON.parse(fs.readFileSync(path.join(output,'render','metadata.json'),'utf8'));
    assert.equal(metadata.renderer,'ocv-remotion-layered-v1');assert.equal(metadata.plan_version,'ocv-layered-v1');
    assert.equal(metadata.width,1280);assert.equal(metadata.height,720);assert.equal(metadata.frames,120);
    assert.equal(metadata.layer_frames.length,5);assert.equal(metadata.detail_times.early,0);
    assert.equal(metadata.detail_times.end,119/30);
    if(process.argv.includes('--video'))assert.ok(fs.statSync(path.join(output,'render','silent.mp4')).size>1000);
    console.log('PASS: independent raster layers, native text, persistent backdrop and standard Remotion output.');
    console.log(result.stdout.trim());
  }
}finally{console.log('LAYERED_SMOKE_OUTPUT='+output);}
