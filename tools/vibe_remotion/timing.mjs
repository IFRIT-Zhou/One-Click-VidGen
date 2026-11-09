export const entranceSeconds=length=>Math.min(.85,length*.35);
export const exitSeconds=length=>Math.min(.3,length*.1);
export const readingSeconds=length=>Math.min(1,length*.40);
export function defaultStagger(length,count) {
  const latest=Math.max(0,length-entranceSeconds(length)-readingSeconds(length)-exitSeconds(length));
  return Math.min(.18,latest/Math.max(1,count-1));
}
export const itemTime=(item,index,length=60,count=1)=>item.at??index*defaultStagger(length,count);
export function activeEvidence(items,time) {
  return items.map((item,index)=>({item,at:itemTime(item,index),index}))
    .filter(v=>v.item.asset_id&&v.at<=time)
    .sort((a,b)=>b.at-a.at||b.index-a.index)[0]??null;
}
