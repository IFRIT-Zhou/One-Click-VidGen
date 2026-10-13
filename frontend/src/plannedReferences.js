const kinds={character:'人物参考',person:'人物参考',style:'画风参考',scene:'场景参考',object:'物件参考'}

export function resolvePlannedReferences(shot,references=[],redrawReferences=[],referenceUrl){
 const assets=[...references,...redrawReferences]
 return (shot.reference_ids??shot.reference_image_ids??[]).map(id=>{
  const asset=assets.find(row=>String(row.id)===String(id))
  return {id,asset,name:asset?.description||asset?.label||asset?.name||String(id),purpose:kinds[asset?.kind]||asset?.kind||'参考素材',url:asset?referenceUrl(asset):'',state:asset?'':'未找到素材记录，请在重绘参考素材中重新选择'}
 })
}
