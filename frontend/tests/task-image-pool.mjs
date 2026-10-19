import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import { taskImagePoolEnabled, taskImagePoolPreference } from '../src/taskImagePool.js'
import { planningPreferences } from '../src/shotPlanningLimit.js'
for (const loggedIn of [true, false]) for (const global of [true, false]) {
  const form = {use_cloud_image_pool:global,task_image_pool:null,video_generation_backend:'comfyui'}
  assert.equal(taskImagePoolEnabled(form, loggedIn), loggedIn && global)
  form.task_image_pool=false
  assert.equal(taskImagePoolEnabled(form,loggedIn),false)
  assert.equal(form.use_cloud_image_pool,global)
  assert.equal(form.video_generation_backend,'comfyui')
  form.task_image_pool=true
  assert.equal(taskImagePoolEnabled(form,loggedIn),loggedIn)
}
console.log('Task-local image pool: passed')
const source=readFileSync(new URL('../src/useWorkspace.js',import.meta.url),'utf8')
const body=source.split('function generationRequestPayload() {')[1].split('\nfunction guidedVisualParameters')[0]
const form={use_cloud_image_pool:true,task_image_pool:false,image_profile_id:'personal-model',dynamic_video:true,video_generation_backend:'comfyui'}
const result=new Function('ctx',`with(ctx){return function(){${body}}`)({form,cloudSession:{value:{authenticated:true}},effectiveCloudVoice:{value:null},ttsEngine:{value:'indextts25'},taskImagePoolEnabled,planningPreferences})()
assert.equal(result.use_cloud_image_pool,false)
assert.equal(result.image_profile_id,'personal-model')
assert.equal(result.language_route,'cloud')
assert.equal(result.video_generation_backend,'comfyui')
assert.equal(Object.hasOwn(result,'task_image_pool'),false)
console.log('Actual generation payload respects task image route: passed')
const studio=readFileSync(new URL('../src/useStudio.js',import.meta.url),'utf8')
const routeUpdate=studio.match(/const imagePoolChoice=taskImagePoolPreference\(w\.form\)[\s\S]*?(?=\n\s*Object\.assign\(parameters)/)[0]
for(const [form,base,expected] of [
  [{use_cloud_image_pool:true,task_image_pool:false},{use_cloud_image_pool:true},false],
  [{use_cloud_image_pool:false,task_image_pool:true},{use_cloud_image_pool:false},true],
  [{use_cloud_image_pool:true,task_image_pool:true},{use_cloud_image_pool:true},undefined],
]) {
  const parameters={}
  new Function('w','base','parameters','taskImagePoolPreference',routeUpdate)({form},base,parameters,taskImagePoolPreference)
  assert.equal(parameters.use_cloud_image_pool,expected)
}
console.log('Existing-project channel changes respect task-local switch: passed')
