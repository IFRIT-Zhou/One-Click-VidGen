import {fixture} from './shotcraft-fixture.mjs';
import {RECIPES,recipeRules,minimumFrames} from '../lib/shotcraft-contract.mjs';

export function expandedFixture({dark=false,asset,maximum=false}={}){
  const input=fixture();input.plan.stages=[];
  if(dark)input.plan.palette={background:'#102330',surface:'#182E3C',ink:'#EDF5F9',muted:'#A8C2D0',line:'#476373',accent:'#79D5EB'};
  input.plan.background={kind:dark?'soft_gradient':'paper'};
  input.assets={photo:asset??'offline-photo.png'};
  let frame=0;
  for(const [s,recipe] of RECIPES.entries()){
    const rule=recipeRules[recipe],n=maximum?rule.max_items:rule.min_items;
    const length=minimumFrames(recipe,n);
    const titles=['理解研究问题','核对原始证据','继续完善方法','保留真实信息'];
    const items=Array.from({length:n},(_,i)=>({id:`r${s}i${i}`,title:titles[i],body:'让每一项解释都有清晰依据，保留材料中的原始信息。',motif:'none',
      ...(!rule.text_only&&(i===0||rule.image_required)?{asset_id:'photo'}:{})}));
    if(recipe==='outro-group-photo-launch'||rule.item_body_max<=22)items.forEach(i=>i.body='保持原始信息完整');
    input.plan.stages.push({id:`recipe${s}`,recipe,start:frame/30,end:(frame+length)/30,eyebrow:rule.label,headline:'让信息清晰可见',body:'动作服务于内容，关键事实保留阅读时间。',footer:'离线适配器测试 · 非研究结论',items});frame+=length;
  }
  input.duration=input.plan.duration=frame/30;
  return input;
}

export function fixtureBatches(input){
  return Array.from({length:3},(_,i)=>{
    const batch=structuredClone(input),stages=batch.plan.stages.slice(i*4,i*4+4),offset=stages[0].start;
    for(const s of stages){s.start-=offset;s.end-=offset;}
    batch.plan.stages=stages;batch.duration=batch.plan.duration=stages.at(-1).end;
    return batch;
  });
}
