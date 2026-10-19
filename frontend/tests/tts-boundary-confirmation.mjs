import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

const source = readFileSync(new URL('../src/useWorkspace.js', import.meta.url), 'utf8')
const body = source.split('async function submitTtsBoundary() {')[1].split('\nasync function saveTtsPause')[0]
const make = new Function('ctx', `with (ctx) { return async function () {${body}` + ' }')

async function run(accept, changed = true, engine = 'indextts25', merge = false, boundary = {}) {
  const calls = [], prompts = []
  const ctx = {
    visualEditorProjectId: { value: 'job' }, ttsBoundaryBusy: { value: false },
    ttsBoundaryOverLimit: () => false, confirmTtsHistoryCapacity: () => true,
    ttsBoundary: { leftReading: changed ? '是' : '成为', rightReading: '搭档',
      sourceText: '成为搭档', sourceReading: '成为搭档', leftText: '成为', rightText: '搭档',
      merge, startIndex: 1, replaceCount: 1, pause: .6, ...boundary },
    window: { confirm: text => { prompts.push(text); return accept } },
    ttsEditor: { value: { engine } }, ttsRefineForm: {},
    api: { resegmentTts: async (...args) => calls.push(args) },
    closeTtsBoundaryEditor() {}, startTtsEditorPolling() {},
  }
  await make(ctx)()
  return { calls, prompts }
}
assert.equal((await run(false)).calls.length, 1)
const confirmed = await run(true)
assert.equal(confirmed.prompts.length, 0)
assert.equal(confirmed.calls[0][1].sync_text, true)
assert.equal(confirmed.calls[0][1].parts[0].text, '是')
const unchanged = await run(true, false)
assert.equal(unchanged.prompts.length, 0)
assert.equal(unchanged.calls[0][1].sync_text, false)
const merged = await run(false, true, 'indextts25', true)
assert.equal(merged.calls[0][1].parts[0].text, '是搭档')
assert.equal(merged.calls[0][1].sync_text, true)
// The cost confirmation remains independent of script synchronization.
assert.equal((await run(false, true, 'cluster')).calls.length, 0)
const moved = await run(true, false, 'indextts25', false, {
  sourceText: '锅盖弹飞，板栗更狠，直接炸坑。',
  sourceReading: '锅盖弹飞。板栗更狠，直接炸坑。',
  leftText: '锅盖弹飞，板栗更狠，', rightText: '直接炸坑。',
  leftReading: '锅盖弹飞。', rightReading: '板栗更狠，直接炸坑。',
})
assert.deepEqual(moved.calls[0][1].parts.map(part => part.text), ['锅盖弹飞。', '板栗更狠，直接炸坑。'])
assert.equal(moved.calls[0][1].sync_text, true)
assert.equal(moved.prompts.length, 0)
const pronunciation = source.split('async function regenerateSelectedTtsSegments() {')[1]
assert.ok(pronunciation.includes('if (subtitleSyncIndices.has(Number(index))) subtitleTextOverrides[index] = readingText'))
console.log('TTS boundary automatic synchronization: passed')

const splitBody=source.split('function updateTtsBoundaryParts(selection) {')[1].split('\nfunction confirmTtsHistoryCapacity')[0]
const state={sourceReading:'原先的字',leftReading:'已改😀',rightReading:'后半句',caret:3}
new Function('ttsBoundary','refreshTtsBoundaryTokenCounts',`return function(selection){${splitBody}`)(state,()=>{})(3)
assert.equal(state.leftReading,'已改😀')
assert.equal(state.rightReading,'后半句')
const previewBody=source.split('async function previewTtsBoundary(item) {')[1].split('\nfunction stopTtsEditorPolling')[0]
const audios=[],delays=[]
const previewContext={
 ttsEditor:{value:{revision:9,segments:[{index:1,audio_url:'/new1.wav',pause_after:.7},{index:2,audio_url:'/new2.wav'}],task:{status:'idle'}}},
 ttsBoundaryBusy:{value:false},ttsBoundaryPreviewTimer:null,ttsBoundaryPreviewAudio:null,ttsPauseDrafts:{1:5},
 window:{clearTimeout(){},setTimeout(callback,delay){delays.push(delay);return 1}},
 Audio:class{constructor(url){this.url=url;this.duration=4;audios.push(this)}play(){return Promise.resolve()}pause(){}},
}
await new Function('ctx',`with(ctx){return async function(item){${previewBody}}`)(previewContext)({index:1,audio_url:'/old.wav'})
assert.match(audios[0].url,/new1.wav.*revision=9/)
audios[0].onended()
assert.equal(delays[0],700)
console.log('Unicode boundary edits and latest saved audio preview: passed')
