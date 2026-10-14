import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const source=readFileSync(new URL('../src/useWorkspace.js',import.meta.url),'utf8')
const start=source.indexOf('async function pollTtsEditorStatus() {')
const fn=source.slice(start,source.indexOf('\nfunction startTtsEditorPolling()',start))
function setup(fetch){
 const state={form:{step_mode:false},guidedStage:{value:''},activePage:{value:'studio'},module1Job:{value:null},visualEditorOpen:{value:false},visualEditorProjectId:{value:'a'},ttsEditor:{value:{revision:0,task:{status:'running',updated_at:1}}},api:{ttsEditorStatus:fetch},stopTtsEditorPolling:()=>{},resetTtsSegmentAudio:()=>{},selectedTtsSegmentIndices:{value:[3]},loadTtsEditor:async()=>{},loadGuidedSubtitles:async()=>{},loadVisualEditor:async()=>{},restoreTtsPreviewPlayers:()=>{}}
 const poll=new Function(...Object.keys(state),`${fn};return pollTtsEditorStatus`)(...Object.values(state))
 return {state,poll}
}
test('unified studio receives failure even with all legacy views closed',async()=>{
 const {state,poll}=setup(async()=>({task:{status:'failed',message:'字幕对齐失败',updated_at:2},revision:0}))
 await poll();assert.equal(state.ttsEditor.value.task.status,'failed');assert.equal(state.ttsEditor.value.task.message,'字幕对齐失败')
})
test('late response cannot overwrite another project',async()=>{
 let resolve;const {state,poll}=setup(()=>new Promise(r=>resolve=r));const pending=poll()
 state.visualEditorProjectId.value='b';resolve({task:{status:'failed',updated_at:2}});await pending
 assert.equal(state.ttsEditor.value.task.status,'running')
})
test('poll errors are visible and automatically clear on recovery',async()=>{
 let fails=true;const {state,poll}=setup(async()=>{if(fails)throw Error('offline');return {task:{status:'failed',updated_at:2}}})
 await poll();assert.match(state.ttsEditor.value.task.poll_error,/自动重试/)
 fails=false;await poll();assert.equal(state.ttsEditor.value.task.poll_error,'')
})
test('out of order response cannot revert terminal status to running',async()=>{
 let resolve;const {state,poll}=setup(()=>new Promise(r=>resolve=r));const pending=poll()
 state.ttsEditor.value.task={status:'failed',updated_at:3};resolve({task:{status:'running',updated_at:2}});await pending
 assert.equal(state.ttsEditor.value.task.status,'failed')
})
