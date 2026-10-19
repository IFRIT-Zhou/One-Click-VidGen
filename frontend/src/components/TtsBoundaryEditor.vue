<script setup>
import { computed, ref, watch } from 'vue'
import { api } from '../api'
const props = defineProps({ state: Object, projectId: String, busy: Boolean, overLimit: Boolean })
const emit = defineEmits(['close', 'split', 'count', 'submit'])
const cursor = ref(null), suggesting = ref(false), suggestion = ref(null), error = ref('')
const joined = computed(() => props.state.leftReading + props.state.rightReading)
const characters = computed(() => Array.from(joined.value))
const cut = computed(() => Array.from(props.state.leftReading).length)
const excerpt = computed(() => `${characters.value.slice(Math.max(0, cut.value - 12), cut.value).join('')} │ ${characters.value.slice(cut.value, cut.value + 12).join('')}`)
watch(() => [props.state.startIndex, props.state.replaceCount, props.state.open], () => { suggestion.value = null; cursor.value = null; error.value = '' })
function capture(event, right = false) {
  cursor.value = (right ? cut.value : 0) + Array.from(event.target.value.slice(0, event.target.selectionStart)).length
}
function edited() { suggestion.value = null; props.state.proposedParts = null; emit('count') }
function split(value) { suggestion.value = null; props.state.merge = false; emit('split', Number(value)) }
async function suggest() {
  const original = joined.value
  const project = props.projectId, start = props.state.startIndex, count = props.state.replaceCount
  const stillCurrent = () => props.state.open && joined.value === original && props.projectId === project && props.state.startIndex === start && props.state.replaceCount === count
  suggesting.value = true; error.value = ''
  try {
    const result = await api.suggestTtsBoundaries(project, [original])
    if (!stillCurrent()) return
    if (result.parts?.join('') !== original) throw new Error('建议与当前文字不一致，未应用')
    suggestion.value = result.parts
  } catch (e) { if (stillCurrent()) error.value = e.message || '暂时无法生成建议，可继续手动调整' }
  finally { suggesting.value = false }
}
function applySuggestion() {
  props.state.proposedParts = [...suggestion.value]
  suggestion.value = null
  emit('count')
}
</script>

<template>
 <section class="boundary-editor" aria-label="调整配音交界" :aria-busy="busy || suggesting">
  <header><div><small>断句与停顿</small><h4>{{state.replaceCount===2?'调整交界':'本句内新增交界点'}}</h4></div><button type="button" :disabled="busy" @click="emit('close')">关闭</button></header>
  <template v-if="!state.proposedParts">
   <div v-if="!state.merge" class="boundary-slider">
    <div class="boundary-excerpt">{{excerpt}}</div>
    <div class="boundary-slider-row"><input type="range" min="1" :max="Math.max(1,characters.length-1)" step="1" :value="cut" aria-label="逐字调整交界位置" :disabled="busy||suggesting" @input="split($event.target.value)"/><button type="button" :disabled="busy||suggesting||cursor===null||cursor<=0||cursor>=characters.length" @click="split(cursor)">从光标处断开</button></div>
    <small>每格一个文字或标点；也可在下方文字中放置光标后断开。</small>
   </div>
   <label v-if="state.replaceCount===2" class="boundary-merge"><input v-model="state.merge" type="checkbox" :disabled="busy||suggesting" @change="edited"/>合并为一句</label>
   <div v-if="!state.merge" class="boundary-halves">
    <label><span>前半句 <small>{{state.counts[0] ?? '…'}} token</small></span><textarea v-model="state.leftReading" rows="4" :disabled="busy||suggesting" @input="edited" @click="capture($event)" @keyup="capture($event)" @select="capture($event)"/></label>
    <label><span>后半句 <small>{{state.counts[1] ?? '…'}} token</small></span><textarea v-model="state.rightReading" rows="4" :disabled="busy||suggesting" @input="edited" @click="capture($event,true)" @keyup="capture($event,true)" @select="capture($event,true)"/></label>
   </div>
   <p v-else class="boundary-merged">{{joined}}</p>
  </template>
  <div v-else class="boundary-proposal"><strong>将按以下 {{state.proposedParts.length}} 段重配</strong><ol><li v-for="(text,index) in state.proposedParts" :key="index">{{text}} <small>{{state.counts[index] ?? '…'}} token</small></li></ol><button type="button" @click="state.proposedParts=null;emit('count')">返回手动调整</button></div>
  <div v-if="overLimit" class="boundary-warning" role="status"><div><strong>部分文字超过建议的 {{state.limit}} token</strong><p>长段可能影响语音自然度与衔接。可直接重配（引擎会安全分段合成），或先查看智能断句建议。</p></div><button type="button" :disabled="suggesting||busy" title="调用断句 Agent，可能产生语言模型费用；只给建议，不自动重配" @click="suggest">{{suggesting?'正在分析…':'智能断句'}}</button></div>
  <div v-if="suggestion" class="boundary-proposal"><strong>建议分为 {{suggestion.length}} 段 · 原文保持不变</strong><ol><li v-for="(text,index) in suggestion" :key="index">{{text}}</li></ol><button type="button" @click="suggestion=null">不采用</button><button type="button" @click="applySuggestion">采用此分法</button></div>
  <p v-if="error" role="alert">{{error}}</p>
  <footer><small>改词会同步字幕、文案与后续时间轴</small><label v-if="!state.merge || state.proposedParts">额外停顿 <input v-model.number="state.pause" type="number" min="0" max="30" step="0.1"/> 秒</label><button class="primary-btn" type="button" :disabled="busy||suggesting||state.checking||Boolean(suggestion)" @click="emit('submit')">{{busy?'正在处理…':'确认并重配'}}</button></footer>
 </section>
</template>

<style scoped>
.boundary-editor button:not(.primary-btn){padding:8px 12px;border:1px solid #ffffff22;border-radius:8px;background:#23333f;color:#d4e1eb;font:inherit;font-size:12px;cursor:pointer}.boundary-editor button:disabled{opacity:.5;cursor:not-allowed}.boundary-editor button:focus-visible{outline:2px solid #80d6c6;outline-offset:2px}
.boundary-editor{padding:20px;border:1px solid #35576b;border-radius:14px;background:color-mix(in srgb,var(--ocv-panel,#192636) 92%,#69c8eb);display:grid;gap:16px;margin-top:18px;min-width:0}.boundary-editor header,.boundary-editor footer{display:flex;gap:12px;align-items:center;justify-content:space-between;flex-wrap:wrap}.boundary-editor h4{margin:4px 0 0;font-size:17px}.boundary-editor small{font-size:12px;color:var(--ocv-muted,#a1b0bd)}.boundary-slider{display:grid;gap:8px}.boundary-excerpt{font-size:14px;color:#9cdeea;text-align:center;overflow-wrap:anywhere}.boundary-slider-row{display:flex;gap:12px;align-items:center}.boundary-slider-row input{flex:1;min-width:0;accent-color:#81d9c6;height:24px;padding:0}.boundary-slider-row button{white-space:nowrap}.boundary-halves{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.boundary-halves label{display:grid;gap:8px;min-width:0}.boundary-halves label>span{display:flex;justify-content:space-between}.boundary-halves textarea{width:100%;box-sizing:border-box;min-height:125px;resize:vertical;line-height:1.7;padding:12px;border-radius:9px}.boundary-merge{display:flex;align-items:center;gap:8px}.boundary-merge input{width:16px!important;height:16px!important;min-height:0!important}.boundary-warning{display:flex;gap:16px;align-items:center;border:1px solid #826d37;background:#b78a2414;padding:12px 14px;border-radius:10px;color:#e6cb88}.boundary-warning p{font-size:12px;line-height:1.6;margin:5px 0 0}.boundary-warning button{white-space:nowrap}.boundary-proposal{border:1px solid #42685e;border-radius:10px;padding:14px}.boundary-proposal li{line-height:1.7;margin:8px 0}.boundary-proposal button+button{margin-left:8px}.boundary-editor footer{border-top:1px solid #ffffff12;padding-top:14px}.boundary-editor footer small{margin-right:auto}.boundary-editor footer label{display:flex;align-items:center;gap:6px}.boundary-editor footer input{width:70px}.boundary-merged{line-height:1.8;margin:0}@media(max-width:720px){.boundary-halves{grid-template-columns:1fr}.boundary-warning{align-items:flex-start;flex-wrap:wrap}.boundary-editor{padding:14px}}
</style>
