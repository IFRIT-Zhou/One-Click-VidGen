import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const source = readFileSync(new URL('../src/useWorkspace.js', import.meta.url), 'utf8')
const body = source.slice(source.indexOf('function pendingTtsReadingEdits()'), source.indexOf('async function undoLastTtsEdit()'))
const make = new Function('ctx', 'with(ctx){'+body+'; return {rebuildTtsAudio,resolveTtsDrafts,closeTtsDraftResolution,saveTtsSubtitleOnly}}')

function fixture({checked=[2], draft='已生成的新文字', saved='已生成的新文字', fail=false}={}) {
  const calls=[]
  const ctx={
    visualEditorProjectId:{value:'job'},ttsBoundaryBusy:{value:false},
    selectedTtsSegmentIndices:{value:[1,2]},ttsDraftResolution:{open:false,items:[]},
    ttsEditor:{value:{revision:8,subtitle_sync_indices:checked,segments:[
      {index:1,text:'前句',pause_after:0},{index:2,text:'原字幕',tts_text:saved}
    ],task:{status:'idle'}}},
    ttsReadingDrafts:{2:draft},ttsPauseDrafts:{1:.5},
    confirmTtsHistoryCapacity:()=>true,resetTtsSegmentAudio(){},
    ttsBoundaryPreviewTimer:null,ttsBoundaryPreviewAudio:null,releaseTtsPreviewPlayers(){},
    visualEditorOpen:{value:false},
    async refreshTtsRelatedSubtitles(){},
    api:{
      rebuildTtsAudio:async(...args)=>{calls.push(args);if(fail)throw Error('请求失败');return {message:'补同步完成，未调用 TTS'}},
      saveTtsSubtitleOnly:async(...args)=>{calls.push(args);if(fail)throw Error('保存失败');return {message:'字幕已独立保存'}}
    },
    async loadTtsEditor(){
      ctx.ttsEditor.value.subtitle_sync_indices=[]
      for(const key of Object.keys(ctx.ttsReadingDrafts))delete ctx.ttsReadingDrafts[key]
      for(const item of ctx.ttsEditor.value.segments)ctx.ttsReadingDrafts[item.index]=item.tts_text||item.text
    }
  }
  return {calls,ctx,actions:make(ctx)}
}
const synced=fixture()
await synced.actions.rebuildTtsAudio()
assert.deepEqual(synced.calls,[['job',{1:.5},{2:'已生成的新文字'},8,{}]])
assert.equal(synced.ctx.ttsDraftResolution.open,false)
assert.deepEqual(synced.ctx.ttsEditor.value.subtitle_sync_indices,[2])
assert.equal(synced.ctx.ttsBoundaryBusy.value,false)

for(const checked of [[],[2]]){
  const pending=fixture({checked,draft:'新的字幕（注释）'})
  await pending.actions.rebuildTtsAudio()
  assert.equal(pending.calls.length,0)
  assert.equal(pending.ctx.ttsDraftResolution.open,true)
  assert.deepEqual(pending.ctx.ttsDraftResolution.items.map(i=>i.index),[2])
  await pending.actions.resolveTtsDrafts('subtitles')
  assert.deepEqual(pending.calls[0],['job',{1:.5},{},8,{2:'新的字幕（注释）'}])
  assert.equal(pending.ctx.ttsReadingDrafts[2],'已生成的新文字')
  assert.deepEqual(pending.ctx.ttsEditor.value.subtitle_sync_indices,[])
  assert.deepEqual(pending.ctx.selectedTtsSegmentIndices.value,[1])
  await pending.actions.rebuildTtsAudio()
  assert.equal(pending.ctx.ttsDraftResolution.open,false)
  assert.deepEqual(pending.calls[1][2],{})
}
const audio=fixture({draft:'尚未生成的发音修正'})
await audio.actions.rebuildTtsAudio()
await audio.actions.resolveTtsDrafts('audio')
assert.deepEqual(audio.calls[0],['job',{1:.5},{},8,{}])
assert.equal(audio.ctx.ttsReadingDrafts[2],'尚未生成的发音修正')
assert.deepEqual(audio.ctx.ttsEditor.value.subtitle_sync_indices,[2])

const cancel=fixture({draft:'未保存'})
await cancel.actions.rebuildTtsAudio()
cancel.actions.closeTtsDraftResolution()
assert.equal(cancel.calls.length,0)
assert.equal(cancel.ctx.ttsReadingDrafts[2],'未保存')

const stale=fixture({draft:'旧草稿'})
await stale.actions.rebuildTtsAudio()
stale.ctx.ttsReadingDrafts[2]='修改过的新草稿'
await stale.actions.resolveTtsDrafts('subtitles')
assert.equal(stale.calls.length,0)
assert.equal(stale.ctx.ttsDraftResolution.open,true)
assert.equal(stale.ctx.ttsDraftResolution.items[0].draft,'修改过的新草稿')

const save=fixture({draft:'只改字幕'})
await save.actions.saveTtsSubtitleOnly(save.ctx.ttsEditor.value.segments[1])
assert.deepEqual(save.calls[0],['job',{2:'只改字幕'},8])
assert.equal(save.ctx.ttsReadingDrafts[2],'已生成的新文字')
assert.deepEqual(save.ctx.ttsEditor.value.subtitle_sync_indices,[])
assert.deepEqual(save.ctx.selectedTtsSegmentIndices.value,[1])
assert.equal(save.ctx.ttsPauseDrafts[1],.5)

const failed=fixture({draft:'保存失败也不能丢草稿',fail:true})
await failed.actions.saveTtsSubtitleOnly(failed.ctx.ttsEditor.value.segments[1])
assert.equal(failed.ctx.ttsReadingDrafts[2],'保存失败也不能丢草稿')
assert.equal(failed.ctx.ttsBoundaryBusy.value,false)
assert.equal(failed.ctx.ttsEditor.value.task.status,'failed')
console.log('Conditional dialog, both choices, cancellation, stale state, subtitle-only save, failures: passed')
