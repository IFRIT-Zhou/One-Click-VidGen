import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const source=readFileSync(new URL('../src/useWorkspace.js',import.meta.url),'utf8')
const body=source.split('async function saveTtsSubtitleBatch(rows) {')[1].split('\nasync function saveTtsSubtitleOnly')[0]
const state={visualEditorProjectId:{value:'project'},ttsBoundaryBusy:{value:false},
 ttsEditor:{value:{revision:4,segments:[{index:1,text:'癌症'},{index:2,text:'其他'}],task:{status:'idle'},subtitle_sync_indices:[1,2]}},
 ttsReadingDrafts:{1:'爱症',2:'其他读音'},ttsPauseDrafts:{1:.2},selectedTtsSegmentIndices:{value:[2]},
 confirmTtsHistoryCapacity:()=>true,refreshTtsRelatedSubtitles:async()=>{},
 loadTtsEditor:async()=>{state.ttsReadingDrafts[1]='从服务端恢复的旧朗读';state.selectedTtsSegmentIndices.value=[]},
 api:{saveTtsSubtitleOnly:async(id,updates,revision)=>{assert.equal(id,'project');assert.deepEqual(updates,{1:'肿瘤'});assert.equal(revision,4);return {message:'字幕已保存'}}}}
const save=new Function('ctx','with(ctx){return async function(rows){'+body+'}')(state)
const rows=[{item:{index:1},before:'癌症',after:'肿瘤'}]
assert.equal(await save(rows),true)
assert.deepEqual(state.ttsReadingDrafts,{1:'爱症',2:'其他读音'})
assert.deepEqual(state.ttsPauseDrafts,{1:.2})
assert.deepEqual(state.selectedTtsSegmentIndices.value,[2])
assert.deepEqual(state.ttsEditor.value.subtitle_sync_indices,[2])
assert.equal(state.ttsBoundaryBusy.value,false)
state.api.saveTtsSubtitleOnly=async()=>{throw Error('网络错误')}
await assert.rejects(save(rows),/网络错误/)
assert.equal(state.ttsReadingDrafts[1],'爱症');assert.equal(state.ttsBoundaryBusy.value,false)
await assert.rejects(save([{...rows[0],before:'过期字幕'}]),/字幕已发生变化/)
state.ttsBoundaryBusy.value=true;assert.equal(await save(rows),false)
console.log('Batch subtitle save preserves reading drafts, pauses and selection; stale/error/busy guards: passed')
