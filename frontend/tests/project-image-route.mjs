import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import vm from 'node:vm'
import { ref, reactive, watch, effectScope } from 'vue'
import { imageRouteSelection, imageRouteKey, imageRouteLabel, serviceImageRouteUpdate } from '../src/projectImageRoute.js'

test('settings changes hand back the chosen channel only to the originating project', () => {
 const before=imageRouteSelection({use_cloud_image_pool:true,method:'ican'})
 assert.equal(serviceImageRouteUpdate(before,{use_cloud_image_pool:true,method:'ican'},'p'),null)
 assert.equal(serviceImageRouteUpdate(before,{use_cloud_image_pool:true,method:'running'},''),null)
 const update=serviceImageRouteUpdate(before,{use_cloud_image_pool:true,method:'running'},'p')
 assert.equal(update.projectId,'p');assert.equal(update.selection.channel,'running')
 assert.equal(serviceImageRouteUpdate(before,{use_cloud_image_pool:false,image_profile_id:'my-api'},'p').selection.channel,'personal')
})
test('route summaries distinguish actual pool, API model and sync size',()=>{
 assert.match(imageRouteLabel(imageRouteSelection({use_cloud_image_pool:true,method:'ican',size:'2048x1152'})),/ICAN.*GPT Image 2.5.*2048×1152/)
 assert.match(imageRouteLabel(imageRouteSelection({use_cloud_image_pool:true,method:'running'})),/稳定版.*2K/)
 assert.match(imageRouteLabel(imageRouteSelection({image_profile_id:'own'}),{name:'我的 API',model_id:'fresh',protocol:'openai_sync',size:'1536x1024'}),/个人 API.*fresh.*1536x1024/)
 assert.notEqual(imageRouteKey(imageRouteSelection({use_cloud_image_pool:true,method:'ican'})),imageRouteKey(imageRouteSelection({use_cloud_image_pool:true,method:'running'})))
})
test('real settings-navigation watcher restores project and carries only explicit image edits',()=>{
 const studioSource=fs.readFileSync(new URL('../src/useStudio.js',import.meta.url),'utf8')
 const code=studioSource.slice(studioSource.indexOf('  const activeVideoProjectId ='),studioSource.indexOf("  let servicesReturnPage ="))
 const storage=new Map(),studioPage=ref('videos'),form=reactive({use_cloud_image_pool:true,method:'ican',image_resolution:'2k'})
 const context=vm.createContext({ref,watch,studioPage,w:{form},imageRouteSelection,serviceImageRouteUpdate,
  sessionStorage:{getItem:key=>storage.get(key)||null,setItem:(key,value)=>storage.set(key,value),removeItem:key=>storage.delete(key)}})
 const scope=effectScope()
 try{
  scope.run(()=>vm.runInContext(code+'\nglobalThis.fixture={activeVideoProjectId,pendingVideoImageRoute,imageRouteApplied,serviceImageProfileSaved}',context))
  const f=context.fixture;f.activeVideoProjectId.value='current'
  studioPage.value='services';form.method='running';studioPage.value='videos'
  assert.equal(f.pendingVideoImageRoute.value.projectId,'current')
  assert.equal(f.pendingVideoImageRoute.value.selection.channel,'running')
  assert.equal(storage.get('ocv-video-open'),'current')
  f.imageRouteApplied('other');assert.ok(f.pendingVideoImageRoute.value)
  f.imageRouteApplied('current');assert.equal(f.pendingVideoImageRoute.value,null)
  studioPage.value='services';studioPage.value='videos';assert.equal(f.pendingVideoImageRoute.value,null)
  studioPage.value='home';studioPage.value='services';form.method='ican';studioPage.value='home'
  assert.equal(f.pendingVideoImageRoute.value,null)
  form.use_cloud_image_pool=false;form.image_profile_id='own';studioPage.value='videos';studioPage.value='services'
  f.serviceImageProfileSaved('own');studioPage.value='videos'
  assert.equal(f.pendingVideoImageRoute.value.selection.image_profile_id,'own')
  assert.equal(f.pendingVideoImageRoute.value.selection.channel,'personal')
 }finally{scope.stop()}
})
const source=fs.readFileSync(new URL('../src/components/VideoStudio.vue',import.meta.url),'utf8')
function fixture(fail=false){
 const calls=[],project={value:{id:'p',revision:3}},ctx=vm.createContext({project,dirty:{value:false},save:async()=>{},
  ensureImageRoute:async()=>{calls.push('route');if(fail)throw Error('invalid route');project.value.revision++},
  run:async fn=>fn(),call:async(path,method,body)=>{calls.push({path,method,body});return project.value}})
 const code=source.match(/async function generateImages\(\)[^\n]+/)[0]
 vm.runInContext(code,ctx);return {ctx,calls}
}
test('retry saves new route first and submits with the refreshed revision',async()=>{
 const f=fixture();await f.ctx.generateImages();assert.equal(f.calls[0],'route')
 assert.equal(f.calls[1].path,'/p/images/generate');assert.equal(f.calls[1].body.revision,4)
})
test('failed channel switch never submits a paid retry to the old channel',async()=>{
 const f=fixture(true);await assert.rejects(f.ctx.generateImages(),/invalid route/);assert.deepEqual(f.calls,['route'])
})
test('all image generation entry points resolve pending channel and picker precedes language',()=>{
 for(const name of ['generateImages','review','redrawShot','generateSceneReferences']){
  assert.match(source.match(new RegExp(`async function ${name}\\([^\\n]+`))[0],/await ensureImageRoute\(\)/)
 }
 assert.ok(source.indexOf('<ProjectImageRoute')<source.indexOf('class="language-presets project-language-route"'))
 assert.match(source,/emit\('project-change',id\|\|''\),\{immediate:true\}/)
})
