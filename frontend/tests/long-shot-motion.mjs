import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { projectDurationIssues } from '../src/shotDurationIssues.js'
const source=fs.readFileSync(new URL('../src/components/VideoStudio.vue',import.meta.url),'utf8')
const code=source.slice(source.indexOf('const motionQueueStates='),source.indexOf('function motionQueueState('))
function fixture(duration=17.243,fail=false,backend='cloud_h3'){
 const shot={id:'s',kind:'video',duration,image:'keep.jpg'},project={value:{id:'p',revision:1,creation_parameters:{video_generation_backend:backend},shots:[shot]}},calls=[]
 const context=vm.createContext({project,ref:value=>({value}),busy:{value:false},dirty:{value:true},error:{value:''},
  motionDrafts:{value:{s:{kind:'video'}}},shotEditingStage:{value:true},structureFeedback:{value:''},
  durationIssueById:{get value(){return Object.fromEntries(projectDurationIssues(project.value.shots,project.value.creation_parameters).map(x=>[x.shot.id,x]))}},
  createSerialTaskQueue:worker=>worker,
  call:async()=>{calls.push('save');if(fail)throw Error('conflict');return {...project.value,revision:2}},
  acceptImageUpdate:updated=>{project.value=updated},autoFillMotion:async()=>{calls.push('plan')},setTimeout})
 vm.runInContext(code+'\nglobalThis.worker=motionQueue',context)
 return{project,calls,context,run:()=>context.worker({projectId:'p',shotId:'s',kind:'video',previousKind:'static'})}
}
test('overlong cluster draft saves and waits for split without unnecessary paid planning',async()=>{
 const f=fixture();await f.run();assert.deepEqual(f.calls,['save']);assert.equal(f.project.value.shots[0].kind,'video');assert.match(f.context.structureFeedback.value,/先自动拆分/)
})
test('normal cluster and unrestricted API shots still plan motion',async()=>{
 for(const [duration,backend] of [[10,'cloud_h3'],[20,'api'],[20,'comfyui']]){const f=fixture(duration,false,backend);await f.run();assert.deepEqual(f.calls,['save','plan'])}
})
test('failed type save restores display and draft instead of leaving fake dynamic state',async()=>{
 const f=fixture(17.243,true);await assert.rejects(f.run(),/conflict/);assert.equal(f.project.value.shots[0].kind,'static');assert.equal(f.context.motionDrafts.value.s.kind,'static');assert.deepEqual(f.calls,['save'])
})
