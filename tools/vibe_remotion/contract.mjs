import fs from 'node:fs';
import path from 'node:path';
import {entranceSeconds,exitSeconds,readingSeconds,itemTime} from './timing.mjs';
import {validatePalette} from './palette.mjs';
import {validateBackground} from './background.mjs';

export const RENDERER = 'ocv-remotion-v1';
const LAYOUTS = new Set(['findings','comparison','process','evidence']);
const THEMES = new Set(['paper','midnight','clinical']);
const string = (x,max,name,required=false) => {
  if ((x===undefined || x===null) && !required) return;
  if (typeof x!=='string' || x.length>max || (required&&!x.trim())) throw new Error(`Invalid ${name}`);
};
const keys=(value,allowed,name)=> {
  if(!value || typeof value!=='object' || Array.isArray(value)) throw new Error(`Invalid ${name}`);
  for(const key of Object.keys(value)) if(!allowed.includes(key)) throw new Error(`Unsupported ${name}.${key}`);
};

export function validateInput(input) {
  input=structuredClone(input);
  keys(input,['plan','assets','duration','fps'],'input');
  const {plan,duration,fps}=input;
  if(!Number.isFinite(duration)||duration<.1||duration>60||fps!==30) throw new Error('Invalid duration/fps');
  keys(plan,['version','canvas','theme','title','subtitle','accent','palette','background','stages'],'plan');
  if(plan.version!=='ocv-motion-v1'||!THEMES.has(plan.theme)) throw new Error('Unsupported motion plan/theme');
  if(!Array.isArray(plan.canvas)||plan.canvas.length!==2||plan.canvas.some((v,i)=>!Number.isInteger(v)||v<(i===0?160:90)||v>1920||v%2)||plan.canvas[0]*plan.canvas[1]>1920*1080) throw new Error('Invalid canvas');
  string(plan.title,90,'title',true);string(plan.subtitle,120,'subtitle');
  if(plan.accent!==undefined&&!/^#[0-9a-fA-F]{6}$/.test(plan.accent)) throw new Error('Invalid accent');
  validatePalette(plan);
  validateBackground(plan.background);
  if(!Array.isArray(plan.stages)||plan.stages.length<1||plan.stages.length>8) throw new Error('Invalid stages');
  keys(input.assets,Object.keys(input.assets??{}),'assets');
  const ids=new Set();
  let end=0;
  for(const stage of plan.stages) {
    keys(stage,['id','layout','start','end','headline','eyebrow','footer','items','visual'],'stage');
    string(stage.id,80,'stage id',true);
    if(ids.has(stage.id)) throw new Error('Duplicate stage id');ids.add(stage.id);
    if(!LAYOUTS.has(stage.layout)||!Number.isFinite(stage.start)||!Number.isFinite(stage.end)||Math.abs(stage.start-end)>.002||stage.end<=stage.start||stage.end>duration+.002) throw new Error('Stages must continuously cover the narration');
    end=stage.end;
    string(stage.headline,90,'headline',true);string(stage.eyebrow,50,'eyebrow');string(stage.footer,140,'footer');
    if(stage.visual!==undefined) {
      keys(stage.visual,['asset_id','placement','treatment','caption'],'visual');
      string(stage.visual.asset_id,80,'visual asset_id',true);
      if(!Object.hasOwn(input.assets,stage.visual.asset_id)) throw new Error('Unbound visual asset: '+stage.visual.asset_id);
      stage.visual.placement??='left';stage.visual.treatment??='contain';stage.visual.caption??='';
      if(!['left','right'].includes(stage.visual.placement)||!['contain','cutout'].includes(stage.visual.treatment)) throw new Error('Invalid visual placement/treatment');
      string(stage.visual.caption,120,'visual caption');
    }
    if(!Array.isArray(stage.items)||!stage.items.length||stage.items.length>4) throw new Error('A stage needs 1–4 items');
    const itemIds=new Set();
    const length=stage.end-stage.start;
    const reserve=entranceSeconds(length)+readingSeconds(length)+exitSeconds(length);
    if(!stage.visual&&Math.min(...stage.items.map((item,index)=>itemTime(item,index,length,stage.items.length)))>Math.min(.5,length*.2)+.001) throw new Error('First meaningful item appears too late');
    for(const [index,item] of stage.items.entries()) {
      keys(item,['id','title','body','value','unit','note','asset_id','at'],'item');
      string(item.id,80,'item id',true);string(item.title,100,'item title',true);
      if(itemIds.has(item.id)) throw new Error('Duplicate item id');itemIds.add(item.id);
      string(item.body,260,'body');string(item.value,60,'value');string(item.unit,32,'unit');string(item.note,120,'note');
      if(item.asset_id && !Object.hasOwn(input.assets,item.asset_id)) throw new Error('Unbound asset: '+item.asset_id);
      const at=itemTime(item,index,length,stage.items.length);
      if(!Number.isFinite(at)||at<0||at+reserve>length+.001) throw new Error('Item appears too late for its stage; allow entrance and reading time');
      item.at=at;
    }
    if(stage.layout==='evidence'&&!stage.visual&&!stage.items.some(i=>i.asset_id)) throw new Error('Evidence stage requires a bound image');
  }
  if(Math.abs(end-duration)>.002) throw new Error('Stages must end at narration duration');
  return input;
}

export function loadAssets(mapping) {
  if(Object.keys(mapping).length>40) throw new Error('Too many assets');
  const out={};
  let total=0;
  for(const [id,file] of Object.entries(mapping)) {
    if(!/^[a-zA-Z0-9_:-]{1,80}$/.test(id)||typeof file!=='string'||!path.isAbsolute(file)) throw new Error('Assets must use bound IDs and absolute local files');
    const stat=fs.statSync(file);
    if(!stat.isFile()||stat.size>32*1024*1024||stat.size<10) throw new Error('Invalid asset file');
    total+=stat.size;if(total>96*1024*1024) throw new Error('Asset memory budget exceeded');
    const data=fs.readFileSync(file);
    const png=data.subarray(0,8).equals(Buffer.from([137,80,78,71,13,10,26,10]));
    const jpg=data[0]===255&&data[1]===216&&data[2]===255;
    const webp=data.subarray(0,4).toString()==='RIFF'&&data.subarray(8,12).toString()==='WEBP';
    if(!png&&!jpg&&!webp) throw new Error('Only raster PNG/JPEG/WebP assets are supported');
    out[id]=`data:image/${png?'png':jpg?'jpeg':'webp'};base64,${data.toString('base64')}`;
  }
  return out;
}

export function proofFrames(plan,duration,fps=30) {
  const max=Math.max(1,Math.round(duration*fps))-1;
  const idx=t=>Math.max(0,Math.min(max,Math.round(t*fps)));
  const clamp=(t,lo,hi)=>Math.max(lo,Math.min(hi,t));
  const stages=plan.stages.map((stage,i)=> {
    const latest=Math.max(...stage.items.map((v,j)=>itemTime(v,j,stage.end-stage.start,stage.items.length)));
    const t=clamp(stage.start+latest+1.0,stage.start+.1,stage.end-.35);
    return {id:stage.id,file:`stage_${String(i).padStart(2,'0')}.png`,frame:idx(t),time:idx(t)/fps};
  });
  const middleStage=plan.stages.findIndex(s=>duration/2>=s.start && duration/2<s.end);
  const middle=middleStage>=0 && (duration/2-plan.stages[middleStage].start<.7 || plan.stages[middleStage].end-duration/2<.4)
    ? stages[middleStage].time : duration/2;
  const details={early:idx(Math.min(duration*.25,.9)),middle:idx(middle),end:idx(Math.max(0,duration-.45))};
  return {details,stages};
}
