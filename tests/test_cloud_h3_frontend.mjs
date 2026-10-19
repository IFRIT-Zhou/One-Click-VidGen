import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {test} from 'node:test'
import {createRequire} from 'node:module'
import {h3MaximumDimensions,fitsH3Dimensions} from '../frontend/src/cloudH3Limits.js'
const require=createRequire(new URL('../frontend/package.json',import.meta.url))
const {parse,compileScript}=require('@vue/compiler-sfc')
const {ref,computed,watch,reactive}=require('vue')
function setup(name,props,requestJSON=async()=>({}),emit=()=>{}){
 const source=readFileSync(new URL('../frontend/src/components/'+name,import.meta.url),'utf8')
 const {descriptor}=parse(source)
 const code=compileScript(descriptor,{id:'h3-test'}).content.replace(/^import .*$/gm,'').replace('export default','return')
 const create=new Function('ref','computed','watch','onMounted','onUnmounted','requestJSON','ModelInstallGuide','CloudH3Options','h3MaximumDimensions','fitsH3Dimensions',code)
 const component=create(ref,computed,watch,()=>{},()=>{},requestJSON,{}, {},h3MaximumDimensions,fitsH3Dimensions)
 return component.setup(props,{expose(){},emit})
}

test('unknown or failed task never displays stale running progress from an interrupted session',()=>{
 const source=readFileSync(new URL('../frontend/src/components/VideoStudio.vue',import.meta.url),'utf8')
 const code=source.slice(source.indexOf('const videoStatusLabel='),source.indexOf('const videoBatchSummary='))
 const {videoTask}=new Function('videoRunning',code+'\nreturn {videoTask}')({value:false})
 for(const [status,expected] of [['unknown','状态待核实，请继续查询原任务'],['failed','生成失败'],['stopped','已停止']]){
  const task=videoTask({kind:'video',video_status:status,video_progress_message:'running',video_error:'集群任务身份已保留'})
  assert.equal(task.status,status);assert.equal(task.message,expected)
 }
 assert.equal(videoTask({kind:'video',video_status:'running',video_progress_message:'GPU 正在生成'}).message,'GPU 正在生成')
})
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
test('cluster defaults to 1664x936 for up to 14 seconds and lists largest sizes first',async()=>{
 const state=setup('CloudH3Options.vue',{modelValue:{resolution:''},seconds:14},async()=>({resolutions:[{width:640,height:384},{width:936,height:1664},{width:1920,height:1080},{width:1664,height:936},{width:1536,height:864}]}))
 await state.refresh()
 assert.equal(state.selected.value,'1664x936')
 assert.deepEqual(state.sizes.value.map(x=>`${x.width}x${x.height}`),['1664x936','936x1664','1536x864','640x384'])
})

test('explicit cloud dimension selection sends exact supported pixels without lowering',async()=>{
 const events=[]
 const state=setup('CloudH3Options.vue',{modelValue:{backend:'cloud_h3',resolution:'custom',width:640,height:384}},async()=>({resolutions:[{width:1280,height:720}]}),(name,data)=>events.push(data))
 await state.refresh()
 state.choose({target:{value:'1280x720'}})
 assert.equal(events[0].resolution,'custom');assert.equal(events[0].width,1280);assert.equal(events[0].height,720)
})

test('cloud default uses declared safe orientation without overwriting explicit saved dimensions',async()=>{
 const resolutions=[{width:640,height:384},{width:384,height:640},{width:1536,height:864},{width:864,height:1536}]
 const defaults={landscape:resolutions[2],portrait:resolutions[3]}
 for(const [ratio,expected] of [['16:9',defaults.landscape],['9:16',defaults.portrait]]){
  const events=[],modelValue={backend:'cloud_h3',resolution:'custom',width:640,height:384}
  const state=setup('CloudH3Options.vue',{modelValue,ratio,seconds:14},async()=>({resolutions,default_resolutions:defaults}),(name,data)=>events.push(data))
  await state.refresh()
  assert.deepEqual(state.defaultDimensions.value,expected)
  assert.equal(state.selected.value,'640x384')
  assert.equal(state.invalidDefault.value,false)
  assert.equal(events.length,0)
 }
})

test('15-second saved clips are blocked without overwriting their dimensions',async()=>{
 const resolutions=[{width:1664,height:936},{width:1532,height:832},{width:832,height:1532},{width:2000,height:500}]
 const props=reactive({modelValue:{backend:'cloud_h3',resolution:'custom',width:1664,height:936},ratio:'16:9',seconds:14})
 const events=[]
 const state=setup('CloudH3Options.vue',props,async()=>({resolutions}),(name,data)=>events.push(data))
 await state.refresh()
 assert.equal(state.unavailableSelected.value,false)
 props.seconds=15
 assert.equal(state.durationInvalid.value,true)
 assert.deepEqual(state.sizes.value,[])
 assert.equal(state.unavailableSelected.value,true)
 assert.equal(state.selected.value,'1664x936')
 state.choose({target:{value:'1532x832'}})
 assert.equal(events.length,0)
 props.seconds=14
 assert.equal(state.durationInvalid.value,false)
 assert.equal(state.unavailableSelected.value,false)
})

test('new clips default to 14-second limits and retain portrait frame alignment',async()=>{
 const resolutions=[{width:1664,height:936},{width:936,height:1664}]
 const default_resolutions={landscape:resolutions[0],portrait:resolutions[1]}
 for(const [ratio,expected] of [['16:9',resolutions[0]],['9:16',resolutions[1]]]){
  const state=setup('CloudH3Options.vue',{modelValue:{resolution:''},ratio,seconds:14},async()=>({resolutions,default_resolutions,fps:24,frame_divisor:17,frame_remainder:5}))
  await state.refresh()
  assert.deepEqual(state.defaultDimensions.value,expected)
  assert.equal(state.invalidDefault.value,false)
  assert.equal(state.generatedSeconds.value,'14.38')
 }
 assert.equal(fitsH3Dimensions(1664,936),true)
 assert.equal(fitsH3Dimensions(1664,936,14),true)
 assert.equal(fitsH3Dimensions(936,1664,14),true)
 assert.equal(fitsH3Dimensions(1664,936,14.01),false)
 assert.equal(fitsH3Dimensions(1532,832,15),false)
 assert.equal(fitsH3Dimensions(640,384,15),false)
 assert.equal(fitsH3Dimensions(1664,937,14),false)
 assert.equal(fitsH3Dimensions(2000,500,14),false)
})

test('older cloud capabilities choose largest supported orientation, invalid advertised defaults never fall back',async()=>{
 const resolutions=[{width:640,height:384},{width:384,height:640},{width:1280,height:720},{width:720,height:1280}]
 for(const [ratio,expected] of [['16:9',resolutions[2]],['9:16',resolutions[3]]]){
  const state=setup('CloudH3Options.vue',{modelValue:{resolution:''},ratio},async()=>({resolutions}))
  await state.refresh()
  assert.deepEqual(state.defaultDimensions.value,expected)
 }
 const state=setup('CloudH3Options.vue',{modelValue:{resolution:''},ratio:'16:9'},async()=>({resolutions,default_resolutions:{landscape:{width:1536,height:864}}}))
 await state.refresh()
 assert.equal(state.defaultDimensions.value,undefined)
 assert.equal(state.invalidDefault.value,true)
})

// Exercise the actual batch handlers with in-memory API responses; never submit GPU work.
function batchEditor({fail=false,locked=false,confirmed=true}={}){
 const source=readFileSync(new URL('../frontend/src/components/VideoStudio.vue',import.meta.url),'utf8')
 const code=source.slice(source.indexOf('const batchOptionsLocked='),source.indexOf('const shotConfigured='))
 const project=ref({id:'project-a',revision:7,shots:[
  {id:'a',kind:'video',video_generation_options:{backend:'api',resolution:'720p'},video:'keep.mp4'},
  {id:'b',kind:'video',video_generation_options:{backend:'cloud_h3',resolution:''}},
  {id:'c',kind:'image'}]})
 const shots=computed(()=>project.value.shots.filter(shot=>shot.kind==='video'))
 const drafts=ref({'project-a:a':{backend:'api'},'other-project:a':{backend:'api'}}),calls=[],error=ref('')
 const deps={ref,watch,computed,project,dynamicShots:shots,normalizedOptions:value=>({...value}),optionsFor:shot=>shot.video_generation_options,busy:ref(false),videoRunning:ref(locked),shotRenderLocked:()=>false,confirm:()=>confirmed,error,run:async fn=>{try{await fn()}catch(e){error.value=e.message}},call:async(url,method,body)=>{calls.push({url,method,body});if(fail)throw new Error('保存失败');return {...project.value,revision:8,shots:project.value.shots.map(shot=>shot.kind==='video'?{...shot,video_generation_options:body.options}:shot)}},shotOptionDrafts:drafts,selectedShot:computed(()=>shots.value[0]),comfyProfiles:ref([]),videoModel:ref(null)}
 const state=new Function(...Object.keys(deps),code+'\nreturn {applyAllShotOptions,applyGenerationOptionsToAll,batchOptionsLocked}')( ...Object.values(deps))
 return {...state,project,drafts,calls,error}
}
test('per-shot settings can still apply to all without replacing media',async()=>{
 const state=batchEditor()
 assert.equal(state.calls.length,0)
 assert.equal(state.project.value.shots[0].video_generation_options.backend,'api')
 await state.applyAllShotOptions()
 assert.equal(state.calls.length,1)
 assert.equal(state.calls[0].method,'PUT')
 assert.equal(state.calls[0].url,'/project-a/generation-options')
 assert.equal(state.calls[0].body.revision,7)
 assert.equal(state.project.value.shots[0].video,'keep.mp4')
 assert.equal(state.project.value.shots[2].video_generation_options,undefined)
 assert.equal(state.calls[0].body.options.backend,'api')
 assert.equal(state.drafts.value['project-a:a'],undefined)
 assert.ok(state.drafts.value['other-project:a'])
})
test('failed batch save retains draft and per-shot overrides',async()=>{
 const state=batchEditor({fail:true})
 await state.applyGenerationOptionsToAll({backend:'cloud_h3',resolution:''})
 assert.equal(state.error.value,'保存失败')
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

test('only per-shot generation settings remain, with no hidden batch draft blocker',()=>{
 const source=readFileSync(new URL('../frontend/src/components/VideoStudio.vue',import.meta.url),'utf8')
 assert.equal((source.match(/<ShotVideoOptions\b/g)||[]).length,1)
 assert.ok(source.includes('@apply-all="applyAllShotOptions"'))
 assert.ok(!source.includes('batchOptionDraft'))
})

test('cluster project planning payload and both creation screens use the fourteen-second ceiling',()=>{
 const read=name=>readFileSync(new URL('../frontend/src/'+name,import.meta.url),'utf8')
 assert.ok(read('useWorkspace.js').includes("form.video_generation_backend === 'cloud_h3' ? 14 : form.dynamic_max_shot_duration"))
 for(const name of ['App.vue','Studio.vue']){
  assert.ok(read(name).includes('集群 H3 · 请求最长 14 秒'))
  assert.ok(read(name).includes('集群 H3 按 14 秒拆分'))
  assert.ok(!read(name).includes('集群 H3 按 15 秒拆分'))
 }
 assert.ok(read('components/VideoStudio.vue').includes("optionsFor(shot).backend==='cloud_h3'?14:"))
})
