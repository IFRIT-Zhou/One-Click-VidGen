<script setup>
// 接口与服务 · Vibe 动效：当前 Vibe 接口 → 编辑草稿。
// Same /api/vibe-model contract and payloads as before; independent of the
// language model and the cloud pool.
import './services/service-panel.css'
import {computed,nextTick,onMounted,reactive,ref,watch} from 'vue'
import {requestJSON} from '../api'
import SecretField from './services/SecretField.vue'
import {draftChanged,missingFields,snapshotDraft,vibeRules} from '../serviceDraft'

const emit=defineEmits(['state'])
const saved=ref(null),busy=ref(''),error=ref(''),message=ref('')
const open=ref(false),baseline=ref(null),firstField=ref(null)
const draft=reactive({base_url:'',model:'',max_image_requests:4,api_key:''})
const configured=computed(()=>!!saved.value?.configured)
const level=computed(()=>busy.value==='load'?'':error.value&&!saved.value?'error':configured.value?'ready':saved.value?'missing':'')
const dirty=computed(()=>open.value&&draftChanged(draft,baseline.value))
const missing=computed(()=>open.value?missingFields(draft,vibeRules({configured:configured.value,previousBaseUrl:saved.value?.base_url})):[])
async function load(){busy.value='load';error.value='';try{saved.value=await requestJSON('/api/vibe-model')}catch(e){error.value=e.message}finally{busy.value=''}}
let returnFocus=null
function openEditor(){
  returnFocus=document.activeElement
  Object.assign(draft,{base_url:saved.value?.base_url||'',model:saved.value?.model||'',max_image_requests:saved.value?.max_image_requests??4,api_key:''})
  baseline.value=snapshotDraft(draft);open.value=true;error.value='';message.value=''
  nextTick(()=>firstField.value?.focus())
}
function closeEditor(force=false){
  if(!force&&dirty.value&&!window.confirm('放弃未保存的 Vibe 配置修改？'))return
  open.value=false;baseline.value=null
  nextTick(()=>returnFocus?.isConnected&&returnFocus.focus())
}
async function save(clear=false){
  if(busy.value)return
  if(clear&&!window.confirm('清除本账号已保存地址的 Vibe 专用密钥？不会撤销服务商密钥或更改其他接口，后续 Vibe 调用需要重新配置。'))return
  if(!clear&&missing.value.length)return
  busy.value='save';error.value='';message.value=''
  try{
    const target=clear?{base_url:saved.value.base_url,model:saved.value.model,max_image_requests:saved.value.max_image_requests}:{base_url:draft.base_url,model:draft.model,max_image_requests:draft.max_image_requests}
    saved.value=await requestJSON('/api/vibe-model',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...target,api_key:clear?null:draft.api_key||null,clear_key:clear})})
    if(!clear)closeEditor(true)
    message.value=clear?'Vibe 密钥已清除。':'Vibe 专用配置已保存。'
  }catch(e){error.value=e.message}finally{busy.value=''}
}
async function test(){
  if(busy.value||dirty.value||!configured.value)return
  if(!window.confirm('将向已保存的 Vibe 接口发送一张程序生成的纯色小图，测试连接和看图能力，可能产生少量模型费用。不发送项目素材。继续吗？'))return
  busy.value='test';error.value='';message.value=''
  try{message.value=(await requestJSON('/api/vibe-model/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm_cloud:true})})).message}
  catch(e){error.value=e.message}finally{busy.value=''}
}
watch([level,dirty],()=>emit('state',{level:level.value,dirty:dirty.value}),{immediate:true})
onMounted(load)
</script>
<template>
  <section class="svc-panel vibe-model-settings" aria-label="Vibe 专用接口">
    <div class="svc-workspace">
    <div class="svc-main svc-main--split">
    <div class="svc-current" aria-live="polite">
      <div class="svc-current-top"><span class="svc-eyebrow">当前 Vibe 接口</span><span class="svc-scope">仅 Vibe 云端动效 · 独立于语言模型和号池</span></div>
      <div v-if="busy==='load'&&!saved" class="svc-skeleton" aria-label="正在读取 Vibe 接口…" />
      <p v-else-if="!saved&&error" class="svc-error" role="alert">{{error}}<button type="button" @click="load">重试</button></p>
      <template v-else-if="saved">
        <div class="svc-row-title"><span class="svc-current-main">{{saved.model||'未设置模型'}} · {{saved.base_url||'未设置地址'}}</span>
          <span v-if="configured" class="svc-badge ok">● 已配置</span><span v-else class="svc-badge warn">▲ 尚未保存 API Key</span></div>
        <div class="svc-current-sub">{{configured?'API Key 已保存在本机，不会回显原文。':'保存 API Key 后才能测试和使用云端动效。'}}</div>
        <div class="svc-row-actions">
          <button type="button" :class="{primary:!configured}" :disabled="!!busy||open" @click="openEditor">{{configured?'编辑配置':'配置 Vibe 接口'}}</button>
          <button type="button" :disabled="!!busy||dirty||!configured" :title="dirty?'请先保存或取消草稿':!configured?'请先保存 API Key':''" @click="test">{{busy==='test'?'测试中…':'测试连接与看图'}}</button>
          <button v-if="configured" type="button" class="svc-danger-text" :disabled="!!busy" @click="save(true)">清除密钥</button>
        </div>
        <small class="svc-note">测试前请先保存。测试会发送程序小图并可能计费；实际云端制作会另行说明上传范围与模型调用。</small>
      </template>
    </div>

    <p v-if="busy==='test'||busy==='save'||message" class="svc-status" role="status">{{busy==='test'?'正在测试连接和图片识别…':busy==='save'?'正在保存…':message}}</p>
    <p v-if="error&&saved" class="svc-error" role="alert">{{error}}</p>

    <div class="svc-section">
      <h3>用途与边界</h3>
      <p class="svc-note">用于看图、设计动画和检查渲染结果。独立于语言模型号池；关闭或切换号池不会改变这里的配置。</p>
      <p class="svc-note">云端模板动效使用本镜参考素材，可保真裁切，不自动抠图、不调用图像模型。原图分层动效默认使用稳定路线：复杂器官、输液袋及透明物体留在底图，人物按需使用本地 RMBG 分离，文字、文本框和简单标注由代码编排，无需启动 ComfyUI。</p>
      <p class="svc-note">需要尝试复杂主体分离时，可在本镜的原图分层动效中开启「进阶主体分离（试用）」，默认关闭。图像生成或编辑仅在单镜授权预算内执行，沿用「本项目图像模型」已保存的渠道，不自动切换号池。两种云端方式都在本机渲染；继续原素材任务仍保留其原有策略。</p>
    </div>
    </div>
    <aside class="svc-aside" aria-label="编辑区">
    <form v-if="open" class="svc-editor" aria-labelledby="svc-vibe-editor" @submit.prevent="save(false)">
      <div class="svc-editor-head"><h4 id="svc-vibe-editor">编辑 Vibe 接口</h4>
        <span :class="dirty?'svc-dirty':'svc-clean'">{{dirty?'● 草稿 · 有未保存修改':'草稿 · 保存前不影响任何任务'}}</span></div>
      <fieldset class="svc-group"><legend>基本信息</legend>
        <label class="svc-field" :class="{'is-missing':missing.includes('服务地址')}"><span class="svc-label-row"><span class="svc-label">服务地址</span><span class="svc-req">必填</span><span v-if="configured" class="svc-opt">更换地址需同时替换 Key</span></span>
          <input ref="firstField" v-model.trim="draft.base_url" :disabled="!!busy" autocomplete="off" spellcheck="false" placeholder="https://api.deepseek.com"></label>
        <label class="svc-field" :class="{'is-missing':missing.includes('多模态模型')}"><span class="svc-label-row"><span class="svc-label">多模态模型</span><span class="svc-req">必填</span></span>
          <input v-model.trim="draft.model" :disabled="!!busy" autocomplete="off" spellcheck="false" placeholder="deepseek-flash"></label>
      </fieldset>
      <SecretField v-model="draft.api_key" label="API Key" :saved="configured" :required="!configured" placeholder="仅保存在本机，不写入项目" :disabled="!!busy" input-name="ocv-vibe-api-secret" />
      <div class="svc-footer">
        <span v-if="missing.length" class="svc-missing" role="status">还缺：{{missing.join('、')}}</span>
        <button type="button" :disabled="!!busy" @click="closeEditor()">取消</button>
        <button type="submit" class="primary" :disabled="!!busy||!dirty||missing.length>0">{{busy==='save'?'保存中…':'保存 Vibe 配置'}}</button>
      </div>
    </form>
    <div v-else class="svc-aside-idle">
      <h4>编辑区</h4>
      <p>点左侧「编辑配置」，在这里修改服务地址、模型和 Key。改动先存为草稿，保存前不会影响任何任务。</p>
      <ul>
        <li>已保存的 Key 默认保留；更换服务地址时必须重新填写 Key。</li>
        <li>草稿未保存时不能测试连接；测试会发送一张程序生成的小图，可能产生少量费用。</li>
      </ul>
    </div>
    </aside>
    </div>
  </section>
</template>
<style scoped>
.vibe-model-settings .svc-section h3{margin:0;font-size:15px;font-weight:600}
</style>
