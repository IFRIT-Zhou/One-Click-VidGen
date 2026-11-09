const visualLength=value=>[...(value??'')].reduce((sum,c)=>sum+(/[\u0000-\u00ff]/.test(c)?.55:1),0);

// Content density selects presentation, not the topic or a particular project.
// Semantics and wording remain exactly as supplied by the validated director.
export function presentationForStage(stage) {
  if(stage.layout==='evidence'&&!stage.visual) return 'evidence';
  if(stage.items.length===1) return 'hero';
  if(stage.layout==='findings' && stage.items.length===2 && stage.items.every(item=>
    !item.asset_id && visualLength(item.title)<=30 && visualLength(item.body)<=60 &&
    visualLength(item.value)<=25 && visualLength(item.unit)<=20 && visualLength(item.note)<=40)) return 'paired';
  return stage.layout==='process'?'process':'cards';
}

export function usesEditorialFocus(stage,stageCount) {
  const item=stage.items?.[0];
  return stageCount>=2&&!stage.visual&&stage.layout!=='evidence'&&stage.items.length===1&&
    !item.asset_id&&!item.value&&!item.unit;
}

export function editorialContext(stage,index) {
  const compact=text=>(text??'').replace(/[\s\p{P}]/gu,'');
  const headline=compact(stage.headline),title=compact(stage.items[0].title);
  const titleIsCovered=title&&headline.includes(title);
  const eyebrow=(stage.eyebrow??'').replace(new RegExp('(^|[^0-9])0?'+(index+1)+'\\s*$'),'$1').trim();
  return {ordinal:String(index+1).padStart(2,'0'),eyebrow,context:titleIsCovered?'':stage.items[0].title};
}

export function visualGeometry(width,height,scale,portrait,placement='left') {
  const gap=36*scale;
  if(portrait) {
    const visualH=(height-gap)*.46;
    const textH=height-gap-visualH;
    return placement==='right'
      ? {visual:{left:0,top:textH+gap,width,height:visualH},text:{left:0,top:0,width,height:textH}}
      : {visual:{left:0,top:0,width,height:visualH},text:{left:0,top:visualH+gap,width,height:textH}};
  }
  const visualW=(width-gap)*.46;
  const textW=width-gap-visualW;
  return placement==='right'
    ? {visual:{left:textW+gap,top:0,width:visualW,height},text:{left:0,top:0,width:textW,height}}
    : {visual:{left:0,top:0,width:visualW,height},text:{left:visualW+gap,top:0,width:textW,height}};
}

export function visualSpan(stages,index) {
  const current=stages[index]?.visual;
  if(!current) return null;
  const same=stage=>stage?.visual?.asset_id===current.asset_id &&
    (stage.visual.placement??'left')===(current.placement??'left') &&
    (stage.visual.treatment??'contain')===(current.treatment??'contain');
  let first=index,last=index;
  while(first>0&&same(stages[first-1]))first--;
  while(last<stages.length-1&&same(stages[last+1]))last++;
  return {start:stages[first].start,end:stages[last].end,first,last,asset_id:current.asset_id};
}
