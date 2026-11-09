import test from 'node:test'
import assert from 'node:assert/strict'
import {parsePreviewPlan, previewFileUrl, previewRoute, vibeBrief, layeredRequestOptions, layeredStrategyDescription, layeredTaskStrategy, freshCloudStartNotice, stepContinuationBudget, stepContinuationNotice, vibeCloudSubmissionError} from '../src/vibePreview.js'
import {requestJSON} from '../src/api.js'

test('imports a native request body without redirecting to a different project',()=>{
  const body={pack_id:'1'.repeat(32),source_sha256:'2'.repeat(64),plan:{sequence:[{asset_id:'layer_01',cue:'s1'}]}}
  assert.deepEqual(parsePreviewPlan('\ufeff'+JSON.stringify(body)),body)
  assert.equal(parsePreviewPlan(JSON.stringify({...body,input_stamp:'c'.repeat(64)})).input_stamp,'c'.repeat(64))
  assert.throws(()=>parsePreviewPlan(JSON.stringify({...body,input_stamp:'not a signature'})))
  for(const value of [null,{}, {...body,project_id:'another'}, {...body,pack_id:'../outside'},
    {...body,plan:{sequence:[]}}])assert.throws(()=>parsePreviewPlan(JSON.stringify(value)))
})
test('media links and instructions refer only to the selected project and shot',()=>{
  const id='3'.repeat(32), receipt={preview_id:id,preview_url:'https://wrong.example/'}
  assert.equal(previewFileUrl('local',receipt,'preview'),'/api/video-studio/local/vibe-previews/'+id+'/preview')
  assert.equal(previewFileUrl('local',{preview_id:'../bad'},'silent'),'')
  assert.equal(previewFileUrl('local',receipt,'unknown'),'')
  assert.equal(previewRoute('local','a/b'),'/api/video-studio/local/shots/a%2Fb')
  const brief=vibeBrief('abc','shot-A',19)
  for(const part of ['abc','19','shot-A','不替换','不调用付费模型'])assert.ok(brief.includes(part))
})

test('advanced subject separation is opt-in and only belongs to layered new attempts',()=>{
  assert.deepEqual(layeredRequestOptions('layered'),{advanced_subject_separation:false})
  assert.deepEqual(layeredRequestOptions('layered',false),{advanced_subject_separation:false})
  assert.deepEqual(layeredRequestOptions('layered',true),{advanced_subject_separation:true})
  for(const value of ['true',1,undefined,null])assert.deepEqual(layeredRequestOptions('layered',value),{advanced_subject_separation:false})
  for(const mode of ['local','cloud','shotcraft','unknown'])assert.deepEqual(layeredRequestOptions(mode,true),{})
  assert.match(layeredStrategyDescription(false),/复杂器官、输液袋.*留在底图.*人物按需独立/)
  assert.match(layeredStrategyDescription(true),/允许模型尝试分离复杂器官、输液袋.*返工/)
})

test('task strategy is reported from the recorded flag, without guessing legacy policy',()=>{
  assert.equal(layeredTaskStrategy(null),'')
  assert.equal(layeredTaskStrategy({template_mode:'shotcraft',advanced_subject_separation:true}),'')
  assert.match(layeredTaskStrategy({template_mode:'layered',advanced_subject_separation:false}),/稳定分层/)
  assert.match(layeredTaskStrategy({template_mode:'layered',advanced_subject_separation:true}),/进阶主体分离/)
  for(const value of [undefined,null,'true'])assert.match(layeredTaskStrategy({template_mode:'layered',advanced_subject_separation:value}),/旧版分层策略.*原任务/)
})

test('fresh start explains independence, old remaining budget and the explicit continuation alternative',()=>{
  const task={task_id:'old',status:'failed',max_model_calls:30,usage:{model_calls:Array.from({length:20},()=>({}))},can_continue_review:true}
  const before=structuredClone(task),notice=freshCloudStartNotice(task,null)
  assert.match(notice,/重新开始确认.*已有制作记录/)
  assert.match(notice,/不带入旧派生素材、代码或审核结论/)
  assert.match(notice,/已用 20\/30 次，剩余 10 次.*不转入本次.*不会从本次预算中扣除/)
  assert.match(notice,/旧记录及正式镜头保留.*不会删除/)
  assert.match(notice,/取消并使用“继续检查与返工”/)
  assert.deepEqual(task,before)
  assert.match(freshCloudStartNotice(null,{preview_id:'old-preview'}),/重新开始确认/)
  assert.match(freshCloudStartNotice(null,null),/新建独立任务/)
  assert.doesNotMatch(freshCloudStartNotice({task_id:'legacy'},null),/已用|剩余|继续检查与返工/)
  assert.match(freshCloudStartNotice({...task,can_continue_steps:true},null),/取消并使用“继续未完成步骤”/)
})

test('manual step continuation uses original budget and requires explicit remaining image permission',()=>{
  const task={max_model_calls:30,max_image_requests:2,usage:{model_calls:[{}],image_requests:1},
    continue_steps_image_remaining:1,continue_steps_images_allowed:true}
  assert.deepEqual(stepContinuationBudget(task),{valid:true,modelUsed:1,modelLimit:30,modelRemaining:29,
    imageUsed:1,imageLimit:2,imageRemaining:1,imagesAllowed:true})
  assert.equal(stepContinuationBudget({...task,new_images_allowed:false}).imagesAllowed,false)
  assert.equal(stepContinuationBudget({...task,continue_steps_images_allowed:undefined}).imagesAllowed,false)
  assert.equal(stepContinuationBudget({...task,continue_steps_image_remaining:9}).imageRemaining,1,'server hint cannot raise original budget')
  assert.equal(stepContinuationBudget({...task,usage:{model_calls:[{}]}}).valid,false,'incomplete counters cannot bind consent')
  assert.equal(stepContinuationBudget({...task,max_model_calls:0}).modelRemaining,0)
  assert.match(stepContinuationNotice(task),/已用 1\/30 次.*剩余 29 次.*不追加次数/)
  assert.match(stepContinuationNotice(task),/不授权新增图片请求.*不会自动重放旧图片任务/)
  assert.match(stepContinuationNotice(task),/后续续接也不会恢复图片授权.*新增出图请另开新任务/)
  assert.match(stepContinuationNotice(task),/尚无清单或预览.*不自动重放已执行的工具/)
  assert.match(stepContinuationNotice(task,true),/图片已用 1\/2 次.*允许使用原授权剩余最多 1 次.*可能计费/)
  assert.match(stepContinuationNotice({...task,new_images_allowed:false},true),/不授权新增图片请求/)
  assert.match(stepContinuationNotice(task),/不覆盖正式镜头，不自动重试/)
})

test('old backend rejects fresh-start explicitly without silent standard/reuse fallback',()=>{
  const error=Object.assign(new Error('start_mode：Extra inputs are not permitted'),{status:422})
  assert.match(vibeCloudSubmissionError(error),/后端尚未加载.*start_mode/)
  assert.match(vibeCloudSubmissionError(error),/未启动制作.*不会删除参数后重试或悄悄沿用旧任务/)
  assert.equal(vibeCloudSubmissionError(Object.assign(new Error('start_mode：Input should be valid'),{status:422})),
    'start_mode：Input should be valid；未切换到其他模型或本地模式。')
})

test('old backend separation-field rejection explains restart without retry or fallback',()=>{
  for(const message of [
    'advanced_subject_separation：Extra inputs are not permitted',
    'advanced_subject_separation: extra_forbidden',
    '1 validation error for CloudRequest\nadvanced_subject_separation\n  Extra inputs are not permitted [type=extra_forbidden, input_value=false, input_type=bool]',
  ]){
    const error=Object.assign(new Error(message),{status:422})
    const display=vibeCloudSubmissionError(error)
    assert.match(display,/后端尚未加载.*advanced_subject_separation/)
    assert.match(display,/安全结束后重启 OCV 应用服务.*前后端为同一版本.*手动开始/)
    assert.match(display,/参数校验阶段被拒绝，未启动制作/)
    assert.match(display,/未删除参数、自动重试或切换模式/)
  }
})

test('separation compatibility message does not hide other validation or provider errors',()=>{
  for(const [message,status] of [
    ['other_field：Extra inputs are not permitted',422],
    ['advanced_subject_separation：Input should be a valid boolean',422],
    ['advanced_subject_separation：Input should be a valid boolean；other_field：Extra inputs are not permitted',422],
    ['advanced_subject_separation：Extra inputs are not permitted',500],
    ['Vibe 模型上游响应传输中断',502],
  ]){
    assert.equal(vibeCloudSubmissionError(Object.assign(new Error(message),{status})),message+'；未切换到其他模型或本地模式。')
  }
})

test('real request formatter recognizes legacy 422 without another network request',async()=>{
  const originalFetch=globalThis.fetch
  let calls=0
  globalThis.fetch=async()=>{
    calls++
    return new Response(JSON.stringify({detail:[{type:'extra_forbidden',loc:['body','advanced_subject_separation'],msg:'Extra inputs are not permitted',input:false}]}),{status:422,headers:{'Content-Type':'application/json'}})
  }
  try{
    await assert.rejects(requestJSON('/offline-vibe-test',{method:'POST',body:JSON.stringify({advanced_subject_separation:false})}),error=>{
      assert.equal(error.status,422)
      assert.match(vibeCloudSubmissionError(error),/后端尚未加载.*未启动制作/)
      return true
    })
    assert.equal(calls,1,'formatting cannot trigger capability probes, fallback or retry')
  }finally{globalThis.fetch=originalFetch}
})
