// Server result is authoritative, including reference-file availability checks.
export function sceneReferenceReadiness(project) {
  if (project?.scene_reference_readiness) return project.scene_reference_readiness
  const p=project?.creation_parameters||{}, settings=project?.settings||{}
  const enabled=(Object.hasOwn(p,'dynamic_text_mode')||Object.hasOwn(settings,'dynamic_text_mode')||p.director_strategy==='enhanced_beta')&&p.scene_references_enabled!==false
  const assets=new Map((project?.scene_assets||[]).map(a=>[a.id,a])), shots=project?.shots||[], blockers=[]
  const state=project?.scene_references_status
  if(['planning','generating','running','queued'].includes(state))blockers.push({code:'SCENE_RUNNING',message:'场景参考正在准备，请等待完成。'})
  for(const a of assets.values())if(['running','queued'].includes(a.image_task?.status))blockers.push({code:'SCENE_RUNNING',message:'场景参考图仍有运行中的生成任务。'})
  if(enabled){
    const needed=new Set()
    for(const shot of shots){const id=shot.scene_reference_id;if(id&&!shot.scene_reference_disabled&&!assets.get(id)?.disabled)needed.add(id)}
    for(const plan of project?.scene_reference_plan?.scenes||[]){
      const members=plan.members||[], valid=members.filter(i=>Number.isInteger(i)&&shots[i]).map(i=>shots[i]), active=valid.filter(s=>!s.scene_reference_disabled)
      if(valid.length&&!active.length)continue
      const matching=[...assets.values()].filter(a=>plan.scene_id!=null&&(a.scene_id===plan.scene_id||a.id===plan.scene_id))
      if(matching.length){for(const a of matching)if(!a.disabled)needed.add(a.id)}
      else if(!active.length||active.some(s=>!s.scene_reference_id))blockers.push({code:'SCENE_PLAN_PENDING',message:'场景计划尚未准备为可用参考图。'})
    }
    for(const id of needed){const a=assets.get(id);if(!a||a.image_status!=='completed'||['running','queued'].includes(a.image_task?.status))blockers.push({code:'SCENE_NOT_READY',scene_id:id,message:`场景“${a?.name||id}”尚未就绪，请修复绑定或重试。`})}
    if(!needed.size&&!assets.size&&['failed','stopped','pending'].includes(state)&&!blockers.length)blockers.push({code:'SCENE_PREPARATION_INCOMPLETE',message:'此前场景准备未完成，请检查原因后重试。'})
  }
  return {can_confirm:!blockers.length,blockers,state:blockers.length?'blocked':'not_applicable'}
}
