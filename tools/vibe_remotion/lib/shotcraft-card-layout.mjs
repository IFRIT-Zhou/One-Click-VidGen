// Deterministic typography geometry. No content is rewritten or truncated.
const amount=text=>[...text].reduce((n,c)=>n+(/[\u0000-\u00ff]/.test(c)?.58:1),0);
// CJK punctuation cannot always begin/end a line. Reserve one full glyph so
// browser line-breaking cannot turn an estimated two-line block into three.
const lineCount=(text,width,size)=>text.split('\n').reduce((n,line)=>n+Math.max(1,Math.ceil(amount(line)/Math.max(1,(width-size)/size))),0);

export function fittedSize(text,width,height,max,min=32){
  let size=max;
  while(size>min){
    if(lineCount(text,width,size)*size*1.28<=height)break;
    size-=1;
  }
  // Avoid an orphan last line while preserving every character and the minimum.
  if(!text.includes('\n')){
    const units=amount(text),capacity=width/size,rows=Math.ceil(units/capacity);
    if(rows>1&&units-(rows-1)*capacity<capacity*.2){
      const compact=Math.floor(width*(rows-1)/Math.max(1,units)*.98);
      if(compact>=min)size=Math.min(size,compact);
    }
  }
  return Math.max(min,size);
}

export const isTextOnlyCard=(item,asset)=>!asset&&item.motif==='none';

export function textOnlyCardGeometry(item,width,height,compact=false){
  const pad=compact?24:44,cw=width-pad*2,ch=height-pad*2;
  const hasBody=Boolean(item.body),gap=hasBody?(compact?18:28):0;
  const titleBudget=hasBody?ch*.58:ch;
  const titleSize=fittedSize(item.title,cw,titleBudget,compact?64:104);
  const titleHeight=Math.min(titleBudget,lineCount(item.title,cw,titleSize)*titleSize*1.28+2);
  const bodyBudget=Math.max(0,ch-titleHeight-gap);
  const bodySize=hasBody?fittedSize(item.body,cw,bodyBudget,compact?36:42):0;
  const bodyHeight=hasBody?Math.min(bodyBudget,lineCount(item.body,cw,bodySize)*bodySize*1.28+2):0;
  const top=(height-titleHeight-gap-bodyHeight)/2;
  return {pad,width:cw,title:{top,height:titleHeight,size:titleSize},body:{top:top+titleHeight+gap,height:bodyHeight,size:bodySize}};
}
