<script setup>
import {computed,onMounted,ref} from 'vue'
import {requestJSON} from '../api'

const model=ref(null),busy=ref(false),message=ref(''),error=ref('')
const draft=ref({base_url:'',submit_path:'/openapi/v2/model/multimodal-video',query_path:'/openapi/v2/query',upload_path:'/openapi/v2/media/upload/binary',resolution:'720p',concurrency_mode:'auto',per_key_concurrency:1,total_concurrency:3,api_keys:['']})
const preview=computed(()=>{const count=model.value?.key_count||0,per=Math.max(1,+draft.value.per_key_concurrency||1),capacity=count*per;const effective=draft.value.concurrency_mode==='manual'?Math.min(capacity,Math.max(1,+draft.value.total_concurrency||1)):capacity;return count?`${count} 个 Key × 每 Key ${per} 路，预计 ${effective} 路并发`:'尚未保存视频 API Key'})
function hydrate(value){model.value=value;draft.value={base_url:value.base_url||'',submit_path:value.submit_path||'',query_path:value.query_path||'/openapi/v2/query',upload_path:value.upload_path||'/openapi/v2/media/upload/binary',resolution:value.resolution||'720p',concurrency_mode:value.concurrency_mode||'auto',per_key_concurrency:value.per_key_concurrency||1,total_concurrency:value.total_concurrency||3,api_keys:['']}}
async function load(){try{hydrate(await requestJSON('/api/video-model'))}catch(e){error.value=e.message}}
async function save(reuse=false){busy.value=true;error.value='';message.value='';try{const payload={...draft.value,api_keys:draft.value.api_keys.map(v=>v.trim()).filter(Boolean),use_image_credentials:reuse};hydrate(await requestJSON('/api/video-model',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)}));message.value=reuse?'已复用兼容的图像 API 账号和全部并行 Key。':'视频接口配置已保存。'}catch(e){error.value=e.message}finally{busy.value=false}}
async function removeKey(index){if(!confirm('删除这个视频 API Key？正在运行或需要续查的原任务可能仍依赖它。'))return;busy.value=true;error.value='';try{hydrate(await requestJSON('/api/video-model/keys/'+index,{method:'DELETE'}));message.value='视频 API Key 已删除。'}catch(e){error.value=e.message}finally{busy.value=false}}
function addKey(){if(draft.value.api_keys.length<10)draft.value.api_keys.push('')}
onMounted(load)
</script>

<template>
 <section class="video-model-settings">
  <header><div><strong>视频模型配置</strong><small>自定义多模态视频接口；配置作用于动态视频板块</small></div><span :class="{ready:model?.has_api_key}">{{model?.has_api_key?'已配置':'未配置'}}</span></header>
  <div class="video-settings-grid">
   <label><span>API Base URL</span><input v-model.trim="draft.base_url" type="url" placeholder="https://api.example.com" autocomplete="off"></label>
   <label><span>视频提交路径</span><input v-model.trim="draft.submit_path" placeholder="/openapi/v2/模型/…/multimodal-video" autocomplete="off"></label>
   <label><span>状态查询路径</span><input v-model.trim="draft.query_path" placeholder="/openapi/v2/query" autocomplete="off"></label>
   <label><span>素材上传路径</span><input v-model.trim="draft.upload_path" placeholder="/openapi/v2/media/upload/binary" autocomplete="off"></label>
   <label><span>原生分辨率</span><select v-model="draft.resolution"><option value="480p">480p</option><option value="720p">720p</option></select></label>
  </div>
  <div v-if="model?.key_hints?.length" class="video-key-hints"><span>已保存账号</span><span v-for="(hint,index) in model.key_hints" :key="hint+index" class="saved-video-key"><code>{{hint}}</code><button type="button" :disabled="busy" title="删除此视频 API Key" @click="removeKey(index)">×</button></span></div>
  <div class="video-key-inputs"><label v-for="(_,index) in draft.api_keys" :key="index"><span>API Key {{index+1}}</span><input v-model="draft.api_keys[index]" type="password" autocomplete="new-password" :placeholder="model?.has_api_key?'留空保留已有 Key':'填写服务商提供的 Key'"></label><button type="button" @click="addKey" :disabled="draft.api_keys.length>=10">＋ 添加并行 Key</button></div>
  <div class="video-concurrency"><div><strong>视频并发</strong><small>{{preview}}</small></div><label><span>模式</span><select v-model="draft.concurrency_mode"><option value="auto">自动</option><option value="manual">手动限制</option></select></label><label><span>单 Key 并发</span><input v-model.number="draft.per_key_concurrency" type="number" min="1" max="8"></label><label v-if="draft.concurrency_mode==='manual'"><span>总并发上限</span><input v-model.number="draft.total_concurrency" type="number" min="1" max="32"></label></div>
  <div class="video-settings-actions"><button class="primary-btn" type="button" :disabled="busy||!draft.base_url||!draft.submit_path" @click="save(false)">{{busy?'保存中…':'保存视频接口配置'}}</button><button v-if="model?.source==='image_compatible'" class="ghost-btn" type="button" :disabled="busy" @click="save(true)">复用兼容的图像 API 账号</button></div>
  <small>接口按异步任务协议工作；地址和路径以你的服务商文档为准。OCV 不指定或推荐第三方服务商。密钥只保存在本机，不会回显原文。</small>
  <p v-if="message" class="video-setting-message">{{message}}</p><p v-if="error" class="board-error">{{error}}</p>
 </section>
</template>

<style scoped>
.video-model-settings{display:grid;gap:13px;padding-top:16px;margin-top:16px;border-top:1px solid var(--border,#35423f)}
header,.video-settings-actions,.video-key-hints,.video-concurrency{display:flex;align-items:center;gap:10px;flex-wrap:wrap}header{justify-content:space-between}header>div{display:grid;gap:3px}header small,.video-model-settings>small,.video-concurrency small{color:var(--muted,#9db0aa);line-height:1.5}header>span{padding:4px 8px;border-radius:99px;background:#382d26;color:#e9b287;font-size:12px}header>span.ready{background:#223c34;color:#82d9bd}
.video-settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.video-settings-grid label,.video-key-inputs label,.video-concurrency label{display:grid;gap:5px;font-size:12px}.video-settings-grid input,.video-settings-grid select,.video-key-inputs input,.video-concurrency input,.video-concurrency select{width:100%;box-sizing:border-box}
.saved-video-key{display:inline-flex;align-items:center;border-radius:6px;background:var(--bg,#141918);overflow:hidden}.video-key-hints code{padding:4px 7px}.saved-video-key button{border:0;border-left:1px solid var(--border,#35423f);padding:4px 7px;border-radius:0}.video-key-inputs{display:grid;gap:8px}.video-key-inputs>button{justify-self:start}.video-concurrency{padding:11px;border:1px solid var(--border,#35423f);border-radius:9px}.video-concurrency>div{display:grid;margin-right:auto}.video-concurrency label{min-width:100px}.video-setting-message{margin:0;color:#82d9bd}
@media(max-width:760px){.video-settings-grid{grid-template-columns:1fr}.video-concurrency{align-items:stretch}.video-concurrency label{width:100%}}
</style>
