const modelShot=shot=>shot?.kind==='video'&&!shot.vibe_reserved&&shot.video_request?.backend!=='vibe'
export function autoSplitWindow(shots, selected, neighbors=false){
 if(selected<0||!modelShot(shots[selected]))return []
 let first=selected,last=selected+1
 if(neighbors&&first>0&&modelShot(shots[first-1]))first--
 if(neighbors&&last<shots.length&&modelShot(shots[last]))last++
 return shots.slice(first,last).map((shot,offset)=>({shot,index:first+offset}))
}
export function needsAutoSplit(shot, maximum=14){return modelShot(shot)&&Math.ceil(Number(shot.duration)||0)>maximum}
