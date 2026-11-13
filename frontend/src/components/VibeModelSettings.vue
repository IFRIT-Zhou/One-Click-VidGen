<script setup>
import {computed,onMounted,ref} from 'vue'
import {requestJSON} from '../api'

const saved=ref(null),draft=ref({base_url:'https://api.deepseek.com',model:'deepseek-flash',max_image_requests:4}),key=ref('')
const busy=ref(''),error=ref(''),message=ref('')
const changed=computed(()=>!!key.value||!saved.value||['base_url','model','max_image_requests'].some(k=>draft.value[k]!==saved.value[k]))
function hydrate(value){saved.value=value;draft.value={base_url:value.base_url,model:value.model,max_image_requests:value.max_image_requests};key.value=''}
async function load(){busy.value='load';try{hydrate(await requestJSON('/api/vibe-model'))}catch(e){error.value=e.message}finally{busy.value=''}}
async function save(clear=false){
  if(clear&&!window.confirm('清除本账号已保存地址的 Vibe 专用密钥？不会撤销服务商密钥或更改其他接口，后续 Vibe 调用需要重新配置。'))return
  busy.value='save';error.value='';message.value=''
  try{
    const target=clear?{base_url:saved.value.base_url,model:saved.value.model,max_image_requests:saved.value.max_image_requests}:draft.value
    hydrate(await requestJSON('/api/vibe-model',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify({...target,api_key:clear?null:key.value||null,clear_key:clear})}))
    message.value=clear?'Vibe 密钥已清除。':'Vibe 专用配置已保存。'
  }catch(e){error.value=e.message}finally{busy.value=''}
}
async function test(){
  if(busy.value||changed.value||!saved.value?.configured)return
  if(!window.confirm('将向已保存的 Vibe 接口发送一张程序生成的纯色小图，测试连接和看图能力，可能产生少量模型费用。不发送项目素材。继续吗？'))return
  busy.value='test';error.value='';message.value=''
  try{message.value=(await requestJSON('/api/vibe-model/test',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({confirm_cloud:true})})).message}
  catch(e){error.value=e.message}finally{busy.value=''}
}
onMounted(load)
</script>
<template>
  <section class="vibe-model-settings" aria-label="Vibe 专用接口">
    <h3>Vibe 云端动效</h3>
    <p>用于看图、设计动画和检查渲染结果。独立于语言模型号池；关闭或切换号池不会改变这里的配置。</p>
    <div class="vibe-config-grid">
      <label>服务地址<input v-model.trim="draft.base_url" :disabled="!!busy" autocomplete="off" placeholder="https://api.deepseek.com"></label>
      <label>多模态模型<input v-model.trim="draft.model" :disabled="!!busy" autocomplete="off" placeholder="deepseek-flash"></label>
      <label class="vibe-key">API Key <small>{{saved?.configured?'已保存，留空保留原密钥':'尚未配置'}}</small><input v-model="key" :disabled="!!busy" type="password" autocomplete="new-password" placeholder="仅保存在本机，不写入项目"></label>
    </div>
    <p>云端模板动效使用本镜参考素材，可保真裁切，不自动抠图、不调用图像模型。原图分层动效默认使用稳定路线：复杂器官、输液袋及透明物体留在底图，人物按需使用本地 RMBG 分离，文字、文本框和简单标注由代码编排，无需启动 ComfyUI。</p>
    <p>需要尝试复杂主体分离时，可在本镜的原图分层动效中开启「进阶主体分离（试用）」，默认关闭。图像生成或编辑仅在单镜授权预算内执行，沿用「本项目图像模型」已保存的渠道，不自动切换号池。两种云端方式都在本机渲染；继续原素材任务仍保留其原有策略。</p>
    <div class="vibe-settings-actions">
      <button class="primary-btn" :disabled="!!busy||!changed" type="button" @click="save(false)">保存 Vibe 配置</button>
      <button :disabled="!!busy||changed||!saved?.configured" type="button" @click="test">测试连接与看图</button>
      <button v-if="saved?.configured" :disabled="!!busy" type="button" @click="save(true)">清除密钥</button>
    </div>
    <small>测试前请先保存。测试会发送程序小图并可能计费；实际云端制作会另行说明上传范围与模型调用。</small>
    <p v-if="busy||message" role="status">{{busy==='test'?'正在测试连接和图片识别…':busy?'正在处理…':message}}</p>
    <p v-if="error" class="vibe-config-error" role="alert">{{error}}</p>
  </section>
</template>
<style scoped>
.vibe-model-settings{display:grid;gap:14px}.vibe-model-settings h3,.vibe-model-settings p{margin:0}.vibe-model-settings p,.vibe-model-settings small{color:var(--muted,#aab8b3);font-size:13px;line-height:1.6}.vibe-config-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:14px}.vibe-model-settings label{display:grid;gap:8px}.vibe-key{grid-column:1/-1}.vibe-model-settings input{width:100%;box-sizing:border-box}.vibe-model-settings details{border:1px solid var(--border,#35423f);border-radius:9px;padding:12px}.vibe-model-settings summary{cursor:pointer}.vibe-model-settings details label{display:flex;align-items:center;gap:12px;margin:14px 0}.vibe-model-settings input[type=number]{max-width:100px}.vibe-settings-actions{display:flex;flex-wrap:wrap;gap:10px}.vibe-model-settings .vibe-config-error{color:var(--danger,#fa9797)}@media(max-width:650px){.vibe-config-grid{grid-template-columns:1fr}.vibe-key{grid-column:auto}}
</style>
