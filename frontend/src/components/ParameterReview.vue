<script setup>
import { computed, ref } from 'vue'
const props = defineProps({ request: { type: Object, default: () => ({}) }, busy:Boolean })
defineEmits(['duplicate'])
const panel = ref('')
const kind = computed(()=>props.request.subtitle_only?'subtitle':props.request.module1_only?'audio':'video')
const labels={indextts25:'本地 GPU · IndexTTS-2.5',indextts2:'本地 IndexTTS（历史记录）',cluster:'集群 GPU · IndexTTS-2.5',qwen:'Qwen TTS',urban_suspense:'都市惊悚',science_explainer:'口播科普',pure_science:'纯科普',general:'通用自定义',stable:'稳健还原',enhanced_beta:'叙事增强',auto:'按作品风格自动',slow:'舒缓',standard:'标准',fast:'紧凑',custom:'自定义',both:'双版本',raw:'无字幕',subtitles:'带字幕',preset:'预设音色',uploaded:'上传音色',happy:'开心',sad:'悲伤',angry:'生气',fear:'害怕',disgust:'厌恶',surprise:'惊讶',calm:'平静'}
function value(key, empty='未单独指定（沿用任务默认处理）'){
 if(!Object.hasOwn(props.request,key))return '该任务未记录'
 const v=props.request[key]
 if(v==null||v==='')return empty
 if(typeof v==='boolean')return v?'开启':'关闭'
 return labels[v]||String(v)
}
const soundFields=computed(()=>[
 ['tts_engine','执行方'],
 ...(props.request.tts_engine==='cluster'?[['cluster_voice_type','音色类型'],['cluster_voice_id','音色']]:props.request.tts_engine==='qwen'?[['qwen_tts_voice','音色']]:[['tts_voice_id','参考音色']]),
 ['tts_emotion','情绪'],['tts_emotion_weight','情绪强度'],['tts_speed','语速'],['tts_volume','音量'],['tts_pitch','音调'],['tts_parallelism','并行数']
])
function rows(key){return Math.min(8,Math.max(2,Math.ceil(String(props.request[key]||'').length/65)))}
</script>
<template>
 <div class="parameter-review creation review-creation">
  <div class="page-heading"><div><p class="eyebrow">项目参数回顾 · 只读</p><h1>{{request.project_name||'项目执行配置'}}</h1><p class="muted">查看本项目保存的执行配置，不会修改任务或当前新建页设置。</p></div><button :disabled="busy" @click="$emit('duplicate')">{{busy?'正在检查…':'以此配置新建'}}</button></div>
  <div class="create-copy-column">
   <div class="form-grid">
    <label class="project-name-field"><span>项目名称</span><input :value="value('project_name')" readonly /></label>
    <label v-if="kind!=='subtitle'" class="check-row source-mode-toggle"><input type="checkbox" :checked="request.skip_tts" disabled /><span>已有配音和文案，不需要 IndexTTS-2.5</span></label>
    <label v-if="request.source_audio_id" class="stack"><span>{{kind==='subtitle'?'识别素材':'已有配音'}} · 素材编号</span><input :value="request.source_audio_id" readonly /></label>
   </div>
   <label v-if="kind!=='subtitle'" class="stack"><span>口播文案</span><textarea :value="value('script','本任务未提供文案')" readonly :rows="Math.min(14,Math.max(4,Math.ceil(String(request.script||'').length/85)))"/><small class="script-character-count">{{String(request.script||'').length}} 字符</small></label>
   <template v-else><label class="check-row"><input type="checkbox" :checked="request.subtitle_use_correction" disabled /><span>使用字幕校对</span></label><label v-if="request.script||request.reference_text" class="stack"><span>参考文案</span><textarea :value="request.reference_text||request.script" readonly rows="5"/></label></template>
  </div>
  <div class="setting-summaries">
   <button v-if="kind!=='subtitle'" :class="{active:panel==='sound'}" @click="panel=panel==='sound'?'':'sound'"><span>◉</span><div><small>配音</small><b>{{value('tts_engine')}} · {{value('tts_emotion','参考原音频')}}</b></div><span>⌄</span></button>
   <button v-if="kind==='video'" :class="{active:panel==='style'}" @click="panel=panel==='style'?'':'style'"><span>▧</span><div><small>作品风格</small><b>{{value('content_mode')}}</b></div><span>⌄</span></button>
   <button v-if="kind==='video'" :class="{active:panel==='pacing'}" @click="panel=panel==='pacing'?'':'pacing'"><span>⊞</span><div><small>画面编排</small><b>{{value('visual_pacing_preset')}} · {{value('director_strategy')}}</b></div><span>⌄</span></button>
  </div>
  <div v-if="panel==='sound' && kind!=='subtitle'" class="tts-parameter-panel review-settings">
   <h3>声音设置 <small class="muted">只读</small></h3>
<div class="tts-param-grid parameter-review-grid"><label v-for="[key,label] in soundFields" :key="key" class="stack" :class="{'review-emotion-inactive':key==='tts_emotion_weight' && !request.tts_emotion}"><span>{{label}}</span><select disabled :aria-label="label"><option>{{value(key,key==='tts_emotion'?'参考原音频':undefined)}}</option></select><small v-if="key==='tts_emotion_weight' && !request.tts_emotion" class="muted">参考原音频时不生效</small></label></div>
   <label v-if="request.tts_pronunciation" class="stack"><span>发音修正</span><textarea :value="request.tts_pronunciation" readonly :rows="rows('tts_pronunciation')"/></label>
   <label v-if="request.qwen_tts_instructions && request.tts_engine==='qwen'" class="stack"><span>配音指令</span><textarea :value="request.qwen_tts_instructions" readonly :rows="rows('qwen_tts_instructions')"/></label>
  </div>
  <div v-if="panel==='style' && kind==='video'" class="tts-parameter-panel visual-prompt-panel review-settings">
   <h3>作品风格 <small class="muted">只读</small></h3>
   <label v-for="[key,label] in [['visual_style_prompt','统一画面风格'],['global_character_prompt','全局人物设定'],['story_environment_prompt','故事世界与环境']]" :key="key" class="stack"><span>{{label}}</span><textarea v-if="request[key]" :value="request[key]" readonly :rows="rows(key)"/><p v-else class="muted review-default-note">{{value(key,'未单独指定；具体表现由该任务的模式和提示词规则决定。')}}</p></label>
   <p v-if="request.reference_image_ids?.length || request.protagonist_reference_image_id" class="muted">已附带角色参考素材：{{request.reference_image_ids?.length||1}} 张</p>
   <details><summary>进阶提示词 · 只读查看</summary><p class="muted">未单独记录的指令不使用当前版本的默认指令替代。</p><label v-for="[key,label] in [['agent0_prompt_system','Agent 0'],['agent1_prompt_system','Agent 1'],['visual_prompt_system','画面指令']]" :key="key" class="stack"><span>{{label}}</span><textarea v-if="request[key]" :value="request[key]" readonly :rows="rows(key)"/><p v-else class="muted">{{value(key,'未保存自定义指令，沿用任务默认规则。')}}</p></label></details>
  </div>
  <div v-if="panel==='pacing' && kind==='video'" class="tts-parameter-panel review-settings">
   <h3>画面编排 <small class="muted">只读</small></h3>
   <div class="parameter-review-grid"><label v-for="[key,label] in [['director_strategy','导演策略'],['visual_pacing_preset','节奏预设']]" :key="key" class="stack"><span>{{label}}</span><select disabled><option>{{value(key)}}</option></select></label></div>
   <div class="parameter-review-grid"><label v-for="[key,label] in [['visual_min_duration','最低停留（秒）'],['visual_target_duration','目标时长（秒）'],['visual_max_duration','最长时长（秒）'],['visual_max_slides','单图最多字幕片段']]" :key="key" class="stack"><span>{{label}}</span><input :value="value(key,'按节奏规则确定')" readonly /></label></div>
  </div>
  <details class="review-settings"><summary>其他执行设置 · 只读查看</summary><div class="parameter-review-grid"><label v-for="[key,label] in [['step_mode','逐步确认'],['auto_split_long_text','自动分段'],['split_text_threshold','每段最大字数'],['use_cloud_image_pool','使用号池'],['video_render_variant','成片版本'],['bgm_enabled','背景音乐'],['bgm_fade_enabled','音乐淡入淡出']].filter(([key])=>Object.hasOwn(request,key))" :key="key" class="stack"><span>{{label}}</span><input :value="value(key)" readonly /></label></div></details>
  <p class="muted review-footnote">以任务保存的记录为准，后续分步确认可能更新部分记录；未记录的历史配置无法完整还原。此处不显示 API 凭据，也不提供修改、上传或生成操作。</p>
 </div>
</template>
