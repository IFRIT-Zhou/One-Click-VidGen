import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {ref,watch,nextTick} from 'vue'

// Exercise the actual watcher from VideoStudio, including its async guard.
const source=readFileSync(new URL('../src/components/VideoStudio.vue',import.meta.url),'utf8')
const start=source.indexOf('watch([()=>project.value?.id,()=>project.value?.audio_version]')
assert.ok(start>=0)
const watcher=source.slice(start,source.indexOf('\n})',start)+3)
function setup(){
 const project=ref(null),selected=ref(0),view=ref('storyboard')
 const stop=new Function('watch','nextTick','project','selected','view','defaultProjectView',`return ${watcher}`)(watch,nextTick,project,selected,view,()=> 'motions')
 return {project,selected,view,stop}
}
async function flush(){await nextTick();await nextTick()}
test('polling does not override manually selected shot or design panel',async()=>{
 const s=setup()
 s.project.value={id:'a',audio_version:'v1',shots:[{audio_timing_adjustment:{needs_review:true}},{}]}
 await flush()
 s.selected.value=1;s.view.value='storyboard'
 s.project.value=JSON.parse(JSON.stringify(s.project.value))
 await flush()
 assert.equal(s.selected.value,1);assert.equal(s.view.value,'storyboard')
 s.project.value={...s.project.value,audio_version:'v2'}
 await flush()
 assert.equal(s.selected.value,0);assert.equal(s.view.value,'motions')
 s.stop()
})
test('stale deferred navigation cannot affect another project',async()=>{
 const s=setup()
 s.project.value={id:'a',audio_version:'v1',shots:[{audio_timing_adjustment:{needs_review:true}}]}
 await nextTick()
 s.project.value={id:'b',audio_version:'v1',shots:[]}
 s.view.value='storyboard'
 await flush()
 assert.equal(s.view.value,'storyboard')
 s.stop()
})
