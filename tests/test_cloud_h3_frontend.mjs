import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {test} from 'node:test'
import {createRequire} from 'node:module'
const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {parse,compileScript}=require('@vue/compiler-sfc')
const {ref,computed,watch}=require('vue')
function setup(name,props,requestJSON=async()=>({}),emit=()=>{}){
 const source=readFileSync(new URL('../frontend/src/components/'+name,import.meta.url),'utf8')
 const {descriptor}=parse(source)
 const code=compileScript(descriptor,{id:'h3-test'}).content.replace(/^import .*$/gm,'').replace('export default','return')
 const create=new Function('ref','computed','watch','onMounted','onUnmounted','requestJSON','ModelInstallGuide','CloudH3Options',code)
 const component=create(ref,computed,watch,()=>{},()=>{},requestJSON,{}, {})
 return component.setup(props,{expose(){},emit})
}
test('switching API720 to cluster clears preset but retains unrelated settings',()=>{
 const events=[]
 const state=setup('ShotVideoOptions.vue',{modelValue:{backend:'api',resolution:'720p',profile_id:'old'},profiles:[],ratio:'16:9'},undefined,(name,data)=>events.push(data))
 state.chooseRoute('cloud_h3')
 assert.equal(events[0].backend,'cloud_h3');assert.equal(events[0].resolution,'');assert.equal(events[0].profile_id,'h3-fast')
})
test('cloud size control preserves unsupported saved preset and shows capability default',async()=>{
 const props={modelValue:{backend:'cloud_h3',resolution:'720p'},ratio:'9:16',seconds:2}
 const state=setup('CloudH3Options.vue',props,async()=>({enabled:true,fps:24,frame_divisor:17,frame_remainder:5,resolutions:[{width:640,height:384},{width:384,height:640}],duration_seconds:{min:2,max:10}}))
 await state.refresh()
 assert.equal(state.selected.value,'720p');assert.equal(state.unavailableSelected.value,true)
 assert.deepEqual(state.defaultDimensions.value,{width:384,height:640})
 assert.equal(state.generatedSeconds.value,'2.33')
})
test('explicit cloud dimension selection sends exact pixels without lowering',()=>{
 const events=[]
 const state=setup('CloudH3Options.vue',{modelValue:{backend:'cloud_h3',resolution:''}},undefined,(name,data)=>events.push(data))
 state.choose({target:{value:'1280x720'}})
 assert.equal(events[0].resolution,'custom');assert.equal(events[0].width,1280);assert.equal(events[0].height,720)
})

// Exercise the actual batch handlers with in-memory API responses; never submit GPU work.
function batchEditor({fail=false,locked=false,confirmed=true}={}){
 const source=readFileSync(new URL('../frontend/src/components/VideoStudio.vue',import.meta.url),'utf8')
 const code=source.slice(source.indexOf('const batchOptionDraft='),source.indexOf('const shotConfigured='))
 const project=ref({id:'project-a',revision:7,shots:[
  {id:'a',kind:'video',video_generation_options:{backend:'api',resolution:'720p'},video:'keep.mp4'},
  {id:'b',kind:'video',video_generation_options:{backend:'cloud_h3',resolution:''}},
  {id:'c',kind:'image'}]})
 const shots=computed(()=>project.value.shots.filter(shot=>shot.kind==='video'))
 const drafts=ref({'project-a:a':{backend:'api'},'other-project:a':{backend:'api'}}),calls=[],error=ref('')
 const deps={ref,watch,computed,project,dynamicShots:shots,normalizedOptions:value=>({...value}),optionsFor:shot=>shot.video_generation_options,busy:ref(false),videoRunning:ref(locked),shotRenderLocked:()=>false,confirm:()=>confirmed,error,run:async fn=>{try{await fn()}catch(e){error.value=e.message}},call:async(url,method,body)=>{calls.push({url,method,body});if(fail)throw new Error('保存失败');return {...project.value,revision:8,shots:project.value.shots.map(shot=>shot.kind==='video'?{...shot,video_generation_options:body.options}:shot)}},shotOptionDrafts:drafts,selectedShot:computed(()=>shots.value[0]),comfyProfiles:ref([]),videoModel:ref(null)}
 const state=new Function(...Object.keys(deps),code+'\nreturn {batchOptionDraft,batchOptions,mixedBatchOptions,applyGenerationOptionsToAll,batchOptionsLocked}')( ...Object.values(deps))
 return {...state,project,drafts,calls,error}
}
test('batch selection stays a draft until applied and preserves video/static shots',async()=>{
 const state=batchEditor()
 assert.equal(state.mixedBatchOptions.value,true)
 state.batchOptions.value={backend:'cloud_h3',profile_id:'h3-fast',resolution:''}
 assert.equal(state.calls.length,0)
 assert.equal(state.project.value.shots[0].video_generation_options.backend,'api')
 await state.applyGenerationOptionsToAll(state.batchOptions.value)
 assert.equal(state.calls.length,1)
 assert.equal(state.calls[0].method,'PUT')
 assert.equal(state.calls[0].url,'/project-a/generation-options')
 assert.equal(state.calls[0].body.revision,7)
 assert.equal(state.project.value.shots[0].video,'keep.mp4')
 assert.equal(state.project.value.shots[2].video_generation_options,undefined)
 assert.equal(state.mixedBatchOptions.value,false)
 assert.equal(state.batchOptionDraft.value,null)
 assert.equal(state.drafts.value['project-a:a'],undefined)
 assert.ok(state.drafts.value['other-project:a'])
})
test('failed batch save retains draft and per-shot overrides',async()=>{
 const state=batchEditor({fail:true})
 state.batchOptions.value={backend:'cloud_h3',resolution:''}
 await state.applyGenerationOptionsToAll(state.batchOptions.value)
 assert.equal(state.error.value,'保存失败')
 assert.equal(state.batchOptionDraft.value.backend,'cloud_h3')
 assert.ok(state.drafts.value['project-a:a'])
 assert.equal(state.project.value.revision,7)
})
test('locked or cancelled batch configuration does not write settings',async()=>{
 for(const options of [{locked:true},{confirmed:false}]){
  const state=batchEditor(options)
  await state.applyGenerationOptionsToAll({backend:'cloud_h3'})
  assert.equal(state.calls.length,0)
 }
})
