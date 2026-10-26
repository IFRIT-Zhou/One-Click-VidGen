import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
const read=path=>readFileSync(new URL(path,import.meta.url),'utf8')
for(const [file,count] of [['../src/Studio.vue',1],['../src/App.vue',3]]){
  const source=read(file)
  for(const name of ['TtsSentenceControls','TtsStructureControls','TtsRebuildButton','TtsEditorFeedback','TtsBoundaryEditor'])
    assert.equal((source.match(new RegExp('<'+name+'\\b','g'))||[]).length,count,file+' '+name)
  assert.equal((source.match(/<TtsDraftResolutionDialog\b/g)||[]).length,1)
  for(const tag of source.match(/<TtsSentenceControls[\s\S]*?\/>/g)||[]){
    assert.ok(!tag.includes('dynamic_video'))
    for(const binding of ['setTtsSubtitleSync','saveTtsSubtitleOnly','saveTtsPause','previewTtsBoundary','openTtsBoundaryEditor'])assert.ok(tag.includes(binding))
  }
  assert.ok(!source.includes('朗读文本只发送给配音引擎，字幕仍保留原文字。'))
}
const workspace=read('../src/useWorkspace.js')
const syncBody=workspace.split('function setTtsSubtitleSync(item, enabled) {')[1].split('\nfunction closeTtsBoundaryEditor')[0]
const ctx={ttsBoundaryBusy:{value:false},ttsEditor:{value:{task:{status:'idle'},subtitle_sync_indices:['2',3]}}}
const setSync=new Function('ctx','with(ctx){return function(item,enabled){'+syncBody+'}')(ctx)
setSync({index:2},false);assert.deepEqual(ctx.ttsEditor.value.subtitle_sync_indices,[3])
setSync({index:2},true);assert.deepEqual(ctx.ttsEditor.value.subtitle_sync_indices,[3,2])
setSync({index:2},true);assert.deepEqual(ctx.ttsEditor.value.subtitle_sync_indices,[3,2])
ctx.ttsBoundaryBusy.value=true;setSync({index:2},false);assert.deepEqual(ctx.ttsEditor.value.subtitle_sync_indices,[3,2])

const refreshBody=workspace.split('async function refreshTtsRelatedSubtitles() {')[1].split('\nfunction pendingTtsReadingEdits')[0]
const state={visualEditorProjectId:{value:'job'},form:{step_mode:true},guidedStage:{value:'audio_review'},activeJob:{value:{id:'job'}},
  guidedSubtitles:{value:[{slide_id:'a',text:'旧字幕'},{slide_id:'b',text:'另一句'}]},guidedSubtitleDrafts:{a:'旧字幕',b:'尚未保存的校对草稿'},visualEditorOpen:{value:false},
  async loadGuidedSubtitles(){state.guidedSubtitles.value=[{slide_id:'a',text:'新字幕'},{slide_id:'b',text:'另一句'}];Object.assign(state.guidedSubtitleDrafts,{a:'新字幕',b:'另一句'})}}
const refresh=new Function('ctx','with(ctx){return async function(){'+refreshBody+'}')(state)
await refresh()
assert.equal(state.guidedSubtitleDrafts.a,'新字幕')
assert.equal(state.guidedSubtitleDrafts.b,'尚未保存的校对草稿')
console.log('Illustrated, dynamic and legacy TTS controls share implementations; subtitle checkbox and guided refresh: passed')
