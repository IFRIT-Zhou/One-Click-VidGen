export function autoSplitWindow(shots, selected, neighbors=false){
 if(selected<0||!shots[selected]||shots[selected].kind!=='video')return []
 let first=selected,last=selected+1
 if(neighbors&&first>0&&shots[first-1].kind==='video')first--
 if(neighbors&&last<shots.length&&shots[last].kind==='video')last++
 return shots.slice(first,last).map((shot,offset)=>({shot,index:first+offset}))
}
export function needsAutoSplit(shot, maximum=14){return shot?.kind==='video'&&Math.ceil(Number(shot.duration)||0)>maximum}
