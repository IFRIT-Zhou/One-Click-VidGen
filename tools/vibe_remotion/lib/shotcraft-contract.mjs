import {contrastRatio,PALETTE_KEYS} from '../palette.mjs';
import {validateBackground} from '../background.mjs';
import rules from './shotcraft-recipes.json' with {type:'json'};

export const SHOTCRAFT_VERSION='ocv-shotcraft-v1';
export const SHOTCRAFT_RENDERER='ocv-remotion-shotcraft-v1';
export const RECIPES=Object.keys(rules);
export const recipeRules=rules;
export const minimumFrames=(recipe,count)=>Math.max(rules[recipe].min_frames,(rules[recipe].per_item_frames??0)*count);
const object=(value,allowed,label)=>{
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Invalid '+label);
  for(const key of Object.keys(value))if(!allowed.includes(key))throw new Error(`Unsupported ${label}.${key}`);
};
const text=(value,max,label,required=false)=>{
  if(value===undefined&&!required)return;
  if(typeof value!=='string'||value.length>max||(required&&!value.trim()))throw new Error('Invalid '+label);
};

// This is an allowlisted data contract, never a code/template execution surface.
export function validateShotcraftInput(raw){
  const input=structuredClone(raw);
  object(input,['plan','assets','duration','fps'],'input');
  const {plan,duration,fps}=input;
  object(plan,['version','canvas','duration','fps','palette','background','typography','stages'],'plan');
  if(plan.version!==SHOTCRAFT_VERSION)throw new Error('Unsupported Shotcraft version');
  if(!Number.isFinite(duration)||duration<1.5||duration>60||fps!==30||plan.fps!==fps||plan.duration!==duration)throw new Error('Shotcraft plan/input duration and fps must match (30 fps)');
  if(!Array.isArray(plan.canvas)||plan.canvas.length!==2||plan.canvas.some((v,i)=>!Number.isInteger(v)||v<(i===0?160:90)||v>1920||v%2)||plan.canvas[0]*plan.canvas[1]>1920*1080)throw new Error('Invalid canvas');
  if(plan.canvas[0]/plan.canvas[1]<1.6)throw new Error('Shotcraft currently requires landscape canvas >=16:10; portrait is not stretched');
  object(plan.palette,PALETTE_KEYS,'palette');
  for(const key of PALETTE_KEYS)if(!/^#[0-9a-fA-F]{6}$/.test(plan.palette[key]??''))throw new Error('Missing/invalid palette.'+key);
  for(const background of ['background','surface']){
    if(contrastRatio(plan.palette.ink,plan.palette[background])<4.5)throw new Error('Shotcraft ink/'+background+' contrast must be at least 4.5');
    if(contrastRatio(plan.palette.muted,plan.palette[background])<3)throw new Error('Shotcraft muted/'+background+' contrast must be at least 3');
  }
  validateBackground(plan.background);
  if(!plan.background)throw new Error('Shotcraft background is required');
  if(!['sans','serif'].includes(plan.typography))throw new Error('Invalid typography');
  object(input.assets,Object.keys(input.assets??{}),'assets');
  if(Object.keys(input.assets).length>40)throw new Error('Too many assets');
  if(!Array.isArray(plan.stages)||plan.stages.length<1||plan.stages.length>8)throw new Error('Invalid stages');
  const ids=new Set();let end=0;
  for(const stage of plan.stages){
    object(stage,['id','recipe','start','end','eyebrow','headline','body','footer','items'],'stage');
    text(stage.id,80,'stage id',true);
    if(ids.has(stage.id))throw new Error('Duplicate stage id');ids.add(stage.id);
    if(!RECIPES.includes(stage.recipe))throw new Error('Unsupported Shotcraft recipe');
    if(!Number.isFinite(stage.start)||!Number.isFinite(stage.end)||Math.abs(stage.start-end)>.002||stage.end<=stage.start||stage.end>duration+.002)throw new Error('Stages must continuously cover the narration');
    end=stage.end;
    text(stage.eyebrow,100,'eyebrow');text(stage.headline,120,'headline',true);text(stage.body,300,'stage body');text(stage.footer,180,'footer');
    if(!Array.isArray(stage.items)||stage.items.length<1||stage.items.length>4)throw new Error('A Shotcraft stage needs 1–4 items');
    if(stage.recipe==='spotlight-hero-card'&&stage.items.length!==1)throw new Error('Spotlight requires exactly one hero');
    const rule=rules[stage.recipe];
    if(stage.items.length<rule.min_items||stage.items.length>rule.max_items)throw new Error('Invalid item count for '+stage.recipe);
    if(rule.headline_max&&[...stage.headline].length>rule.headline_max)throw new Error('Headline too long for '+stage.recipe);
    if(rule.image_required&&stage.items.some(i=>!i.asset_id))throw new Error('Original image required for '+stage.recipe);
    if(rule.text_only&&stage.items.some(i=>i.asset_id||i.motif!=='none'))throw new Error('Text only recipe: '+stage.recipe);
    if(rule.body_required&&stage.items.some(i=>!i.body?.trim()))throw new Error('Flip back needs item.body');
    if(stage.items.some(i=>[...(i.body??'')].length>(rule.item_body_max??70)))throw new Error('Item body too long for '+stage.recipe);
    if(stage.recipe==='outro-group-photo-launch'&&stage.end-stage.start>6.001)throw new Error('Shotcraft outro must be at most 6 seconds: '+stage.id);
    const frames=Math.round(stage.end*fps)-Math.round(stage.start*fps);
    const minimum=minimumFrames(stage.recipe,stage.items.length);
    if(frames<minimum)throw new Error('Shotcraft stage too short for motion plus one-second reading hold: '+stage.id);
    const itemIds=new Set();
    for(const item of stage.items){
      object(item,['id','title','body','asset_id','motif'],'item');
      text(item.id,80,'item id',true);text(item.title,100,'item title',!item.asset_id);text(item.body,280,'item body');
      if(typeof item.title!=='string')throw new Error('Invalid item title');
      if(itemIds.has(item.id))throw new Error('Duplicate item id');itemIds.add(item.id);
      if(!['document','book','inspection','refinement','none'].includes(item.motif))throw new Error('Invalid item motif');
      if(item.asset_id!==undefined){text(item.asset_id,80,'asset_id',true);if(!Object.hasOwn(input.assets,item.asset_id))throw new Error('Unbound asset: '+item.asset_id);}
    }
  }
  if(Math.abs(end-duration)>.002)throw new Error('Stages must end at narration duration');
  return input;
}

export function shotcraftProofFrames(plan,duration,fps=30){
  const max=Math.round(duration*fps)-1;
  const idx=t=>Math.max(0,Math.min(max,Math.round(t*fps)));
  const stages=plan.stages.map((stage,i)=>{
    const frame=Math.max(idx(stage.start),Math.min(max,Math.round(stage.end*fps)-16));
    return {id:stage.id,file:`stage_${String(i).padStart(2,'0')}.png`,frame,time:frame/fps};
  });
  const details={early:idx(Math.min(.9,duration*.25)),middle:idx(duration/2),end:idx(duration-.45)};
  return {stages,details};
}

// All relay items, including the middle ones, receive a layout/evidence proof.
export function shotcraftItemFrames(plan,fps=30){
  return plan.stages.flatMap((stage,s)=>!rules[stage.recipe].sequential&&stage.recipe!=='card-flip-reveal'?[]:stage.items.map((item,i)=>{
    const start=Math.round(stage.start*fps),length=Math.round(stage.end*fps)-start;
    const frame=start+(stage.recipe==='card-flip-reveal'?35:Math.min(length-1,Math.floor((i+.8)*length/stage.items.length)));
    return {id:item.id,file:`item_${String(s).padStart(2,'0')}_${String(i).padStart(2,'0')}.png`,frame,time:frame/fps};
  }));
}
