// Keep pauses in the visual timeline, just as the export renderer does.
export function visualPlaybackRange(shots,index,totalDuration=0){
 const shot=shots[index]
 const start=index?Number(shot.start):0
 const end=index+1<shots.length?Number(shots[index+1].start):Math.max(Number(shot.end),Number(totalDuration)||0)
 return {start,end,duration:Math.max(.001,end-start)}
}
export function previewRate(source,target){
 return Number.isFinite(source)&&source>0&&target>source+.03?source/target:1
}
