import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import {findTtsReplacements} from '../src/ttsFindReplace.js'
const segments=[{index:1,text:'癌与结，癌。'},{index:2,text:'结节'},{index:3,text:'正常'}]
assert.equal(findTtsReplacements(segments,{},'', '爱').length,0)
assert.equal(findTtsReplacements(segments,{},'癌','癌').length,0)
assert.deepEqual(findTtsReplacements(segments,{},'癌','爱').map(r=>[r.after,r.count]),[['爱与结，爱。',2]])
assert.equal(findTtsReplacements(segments,{1:'已修正'},'癌','爱').length,0)
assert.equal(findTtsReplacements([{index:1,text:'a.b$'}],{},'.','$&')[0].after,'a$&b$')
const source=readFileSync(new URL('../src/components/TtsFindReplace.vue',import.meta.url),'utf8')
const script=source.split('<script setup>')[1].split('</script>')[0].replace(/^import .*$/gm,'')
const props={editor:{available:true,segments,task:{status:'idle'}},drafts:{2:'结节读音修正'},busy:false}
const events=[]
const ui=new Function('defineProps','defineEmits','computed','ref','watch','findTtsReplacements',script+';return {search,replacement,mode,apply,matches,notice};')(
  ()=>props,()=> (...event)=>{events.push(event);if(event[0]==='reading')props.drafts[event[1].index]=event[2].target.value},
  fn=>({get value(){return fn()}}),v=>({value:v}),()=>{},findTtsReplacements)
ui.search.value='癌';ui.replacement.value='爱';await ui.apply()
assert.equal(props.drafts[1],'爱与结，爱。')
assert.equal(props.drafts[2],'结节读音修正')
assert.deepEqual(events.find(e=>e[0]==='select'),['select',[1]])
assert.equal(events.find(e=>e[0]==='subtitle-sync')[2],false)
events.length=0;await ui.apply();assert.equal(events.length,0)
ui.search.value='结';ui.replacement.value='洁';ui.mode.value='both';await ui.apply()
assert.deepEqual(events.filter(e=>e[0]==='subtitle-sync').map(e=>e[2]),[true,true])
events.length=0;props.editor.task.status='running';ui.search.value='洁';ui.replacement.value='结';await ui.apply();assert.equal(events.length,0)
props.editor.task.status='idle';ui.search.value=props.drafts[1];ui.replacement.value='';await ui.apply();assert.equal(events.length,0)
ui.mode.value='subtitle';ui.search.value='癌';ui.replacement.value='某字'
let saved=[]
props.saveSubtitles=async rows=>{saved=rows;return true}
const draftsBefore=JSON.stringify(props.drafts)
await ui.apply()
assert.equal(saved.length,1);assert.equal(saved[0].after,'某字与结，某字。')
assert.equal(JSON.stringify(props.drafts),draftsBefore);assert.equal(events.length,0)
assert.ok(ui.notice.value.includes('朗读文本、配音及时间轴未改变'))
props.saveSubtitles=async()=>{throw Error('保存失败')};await ui.apply();assert.equal(ui.notice.value,'保存失败')
for(const [file,count] of [['Studio.vue',1],['App.vue',3]]){
 const text=readFileSync(new URL('../src/'+file,import.meta.url),'utf8')
 assert.equal((text.match(/<TtsFindReplace\b/g)||[]).length,count)
 assert.ok(text.includes('@reading="updateTtsReadingDraft"'))
 assert.ok(text.includes('@subtitle-sync="setTtsSubtitleSync"'))
 assert.ok(text.includes(':save-subtitles="saveTtsSubtitleBatch"'))
}
const studio=readFileSync(new URL('../src/Studio.vue',import.meta.url),'utf8')
assert.match(studio,/:disabled="!canReturnDynamicStoryboard\|\|studioBusy/)
const state=readFileSync(new URL('../src/useStudio.js',import.meta.url),'utf8')
assert.ok(state.includes('editingDynamicAudio.value && dynamicAudioHasShots.value'))
assert.ok(state.includes('dynamicAudioHasShots.value=Boolean(project.shots?.length)'))
console.log('TTS literal replacement, draft preservation, subtitle opt-in, exact selection, locks and navigation: passed')
