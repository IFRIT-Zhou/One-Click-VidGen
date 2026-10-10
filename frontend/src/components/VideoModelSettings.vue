<script setup>
import {computed,onMounted,ref} from 'vue'
import {requestJSON} from '../api'

const model=ref(null),busy=ref(false),message=ref(''),error=ref('')
const workflow=ref(emptyWorkflow()),imageNodesText=ref(''),outputNodesText=ref(''),overridesText=ref('[]')
function emptyWorkflow(){return {workflow_id:'2108955980238266370',image_nodes:[],prompt_node:{node_id:'',field:'text'},duration_node:{node_id:'',field:'value'},output_nodes:[],preferred_output:'',instance_type:'default',use_personal_queue:false,overrides:[]}}
function useWorkflowPreset(preset){hydrateWorkflow(preset.config);message.value='已填入 '+preset.name+' 节点映射，点击保存后生效。';error.value=''}
function hydrateWorkflow(value){workflow.value={...emptyWorkflow(),...value,prompt_node:value?.prompt_node||{node_id:'',field:'text'},duration_node:value?.duration_node||{node_id:'',field:'value'}};if(!workflow.value.workflow_id)workflow.value.workflow_id='2108955980238266370';imageNodesText.value=(value?.image_nodes||[]).map(n=>`${n.node_id}.${n.field}`).join(', ');outputNodesText.value=(value?.output_nodes||[]).join(', ');overridesText.value=JSON.stringify(value?.overrides||[],null,2)}
function workflowPayload(){return {...workflow.value,image_nodes:imageNodesText.value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean).map(v=>{const pos=v.lastIndexOf('.');if(pos<1)throw Error('图片槽位请填写“节点编号.字段名”，多个槽位用逗号分隔');return {node_id:v.slice(0,pos),field:v.slice(pos+1)}}),output_nodes:outputNodesText.value.split(/[,，\n]/).map(v=>v.trim()).filter(Boolean),overrides:JSON.parse(overridesText.value||'[]')}}
const draft=ref({base_url:'',submit_path:'/openapi/v2/model/multimodal-video',query_path:'/openapi/v2/query',upload_path:'/openapi/v2/media/upload/binary',resolution:'720p',concurrency_mode:'auto',per_key_concurrency:1,total_concurrency:3,api_keys:['']})
const preview=computed(()=>{const count=model.value?.key_count||0,per=Math.max(1,+draft.value.per_key_concurrency||1),capacity=count*per;const effective=draft.value.concurrency_mode==='manual'?Math.min(capacity,Math.max(1,+draft.value.total_concurrency||1)):capacity;return count?`${count} 个 Key × 每 Key ${per} 路，预计 ${effective} 路并发`:'尚未保存视频 API Key'})
function hydrate(value){model.value=value;draft.value={protocol:value.protocol||'async_task',model:value.model||'doubao-seedance-2-0-260128',base_url:value.base_url||'',submit_path:value.submit_path||'',query_path:value.query_path||'/openapi/v2/query',upload_path:value.upload_path||'/openapi/v2/media/upload/binary',resolution:value.resolution||'720p',concurrency_mode:value.concurrency_mode||'auto',per_key_concurrency:value.per_key_concurrency||1,total_concurrency:value.total_concurrency||3,api_keys:['']}}
function chooseProtocol(){if(draft.value.protocol==='ark')Object.assign(draft.value,{base_url:'https://ark.cn-beijing.volces.com',submit_path:'/api/v3/contents/generations/tasks',query_path:'/api/v3/contents/generations/tasks',upload_path:'/api/v3',model:'doubao-seedance-2-0-260128'});else if(draft.value.protocol==='runninghub_workflow')Object.assign(draft.value,{base_url:'https://www.runninghub.ai',submit_path:'/openapi/v2/run/workflow/'+workflow.value.workflow_id,query_path:'/openapi/v2/query',upload_path:'/openapi/v2/media/upload/binary'});else Object.assign(draft.value,{submit_path:'/openapi/v2/model/multimodal-video',query_path:'/openapi/v2/query',upload_path:'/openapi/v2/media/upload/binary',resolution:'720p'})}
async function load(){try{const value=await requestJSON('/api/video-model');hydrate(value);hydrateWorkflow(value.workflow)}catch(e){error.value=e.message}}
async function save(reuse=false){busy.value=true;error.value='';message.value='';try{const payload={...draft.value,workflow:draft.value.protocol==='runninghub_workflow'?workflowPayload():(model.value?.workflow||{}),api_keys:draft.value.api_keys.map(v=>v.trim()).filter(Boolean),use_image_credentials:reuse};const value=await requestJSON('/api/video-model',{method:'PUT',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});hydrate(value);hydrateWorkflow(value.workflow);message.value=reuse?'已复用兼容的图像 API 账号和全部并行 Key。':'视频接口配置已保存。'}catch(e){error.value=e.message}finally{busy.value=false}}
async function removeKey(index){if(!confirm('删除这个视频 API Key？正在运行或需要续查的原任务可能仍依赖它。'))return;busy.value=true;error.value='';try{hydrate(await requestJSON('/api/video-model/keys/'+index,{method:'DELETE'}));message.value='视频 API Key 已删除。'}catch(e){error.value=e.message}finally{busy.value=false}}
function addKey(){if(draft.value.api_keys.length<10)draft.value.api_keys.push('')}
onMounted(load)
</script>

<template>
 <section class="video-model-settings">
  <header><div><strong>视频模型配置</strong><small>自定义多模态视频接口；配置作用于动态视频板块</small></div><span :class="{ready:model?.has_api_key}">{{model?.has_api_key?'已配置':'未配置'}}</span></header>
  <div class="video-settings-grid">
   <label><span>接入方式</span><select v-model="draft.protocol" @change="chooseProtocol"><option value="async_task">兼容异步视频接口</option><option value="ark">火山方舟官方 · Seedance</option><option value="runninghub_workflow">RunningHub 工作流（RH 币）</option></select></label>
   <label v-if="draft.protocol==='ark'"><span>模型调用名称</span><input v-model.trim="draft.model" placeholder="doubao-seedance-2-0-260128"></label>
   <label><span>API Base URL</span><input v-model.trim="draft.base_url" type="url" placeholder="https://api.example.com" autocomplete="off"></label>
   <template v-if="draft.protocol!=='runninghub_workflow'">
    <label><span>视频提交路径</span><input v-model.trim="draft.submit_path" placeholder="/openapi/v2/模型/…/multimodal-video" autocomplete="off"></label>
    <label><span>状态查询路径</span><input v-model.trim="draft.query_path" placeholder="/openapi/v2/query" autocomplete="off"></label>
    <label><span>素材上传路径</span><input v-model.trim="draft.upload_path" placeholder="/openapi/v2/media/upload/binary" autocomplete="off"></label>
    <label><span>原生分辨率</span><select v-model="draft.resolution"><option value="480p">480p</option><option value="720p">720p</option><option v-if="draft.protocol==='ark'" value="1080p">1080p · 标准版</option></select></label>
   </template>
  </div>
  <section v-if="draft.protocol==='runninghub_workflow'" class="workflow-config">
   <div class="video-settings-actions"><strong>工作流预设</strong><button v-for="preset in model?.workflow_presets||[]" :key="preset.id" type="button" class="ghost-btn" :disabled="busy" @click="useWorkflowPreset(preset)">使用 {{preset.name}}</button></div>
   <small v-if="workflow.workflow_id==='2108955980238266370'">aiwood 预设：单张核心图，16:9 横屏、约 1MP、24 帧；时长自动跟随本镜。参考音频未启用。可在下方固定参数中调整分辨率。</small>
   <div><strong>RH 币工作流</strong><p>按每镜的核心图、视频提示词和请求秒数运行已发布工作流。分辨率、采样及音频开关由工作流决定；请核对节点后保存。</p></div>
   <div class="video-settings-grid">
    <label><span>工作流 ID</span><input v-model.trim="workflow.workflow_id" placeholder="2108955980238266370"></label>
    <label><span>运行规格</span><select v-model="workflow.instance_type"><option value="default">default · 标准规格</option><option value="plus">plus · 大显存</option><option value="ultra">ultra · 超大显存</option></select></label>
    <label class="workflow-wide"><span>图片槽位（按参考图顺序，第一个为核心图）</span><input v-model="imageNodesText" placeholder="例如 51.image, 49.image；以你的工作流为准"></label>
    <label><span>提示词节点编号</span><input v-model.trim="workflow.prompt_node.node_id" placeholder="API 面板中的 nodeId"></label>
    <label><span>提示词字段</span><input v-model.trim="workflow.prompt_node.field" placeholder="text"></label>
    <label><span>时长节点编号（秒）</span><input v-model.trim="workflow.duration_node.node_id" placeholder="API 面板中的 nodeId"></label>
    <label><span>时长字段</span><input v-model.trim="workflow.duration_node.field" placeholder="value"></label>
    <label><span>视频保存节点（逗号分隔）</span><input v-model="outputNodesText" placeholder="自动将这些节点的 save_output 设为 true"></label>
    <label><span>成品输出节点</span><input v-model.trim="workflow.preferred_output" placeholder="填写最终视频节点，避免取到一采预览"></label>
   </div>
   <label class="workflow-checkbox"><input type="checkbox" v-model="workflow.use_personal_queue">使用个人队列</label>
   <details><summary>其他固定节点参数（可选）</summary><p>用于分辨率、采样等额外参数。格式：[{"node_id":"节点编号","field":"字段名","value":数值或文字}]。不填写时沿用工作流默认值。</p><textarea v-model="overridesText" rows="5" spellcheck="false" /></details>
   <small>不同工作流的节点编号不同，示例编号不可直接套用。未使用的额外素材槽位需要在工作流中关闭。运行费用及会员要求以 RunningHub 账号为准。</small>
  </section>
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
.workflow-config{display:grid;gap:14px;padding:18px;border:1px solid var(--border,#35423f);border-radius:12px;background:var(--panel,var(--bg,#141918))}.workflow-config p,.workflow-config small{color:var(--muted,#9db0aa);line-height:1.6;margin:6px 0}.workflow-wide{grid-column:1/-1}.workflow-checkbox{display:flex;align-items:center;gap:8px}.workflow-checkbox input{width:auto}.workflow-config textarea{width:100%;box-sizing:border-box;font-family:monospace}.workflow-config summary{cursor:pointer}
header,.video-settings-actions,.video-key-hints,.video-concurrency{display:flex;align-items:center;gap:10px;flex-wrap:wrap}header{justify-content:space-between}header>div{display:grid;gap:3px}header small,.video-model-settings>small,.video-concurrency small{color:var(--muted,#9db0aa);line-height:1.5}header>span{padding:4px 8px;border-radius:99px;background:#382d26;color:#e9b287;font-size:12px}header>span.ready{background:#223c34;color:#82d9bd}
.video-settings-grid{display:grid;grid-template-columns:1fr 1fr;gap:9px}.video-settings-grid label,.video-key-inputs label,.video-concurrency label{display:grid;gap:5px;font-size:12px}.video-settings-grid input,.video-settings-grid select,.video-key-inputs input,.video-concurrency input,.video-concurrency select{width:100%;box-sizing:border-box}
.saved-video-key{display:inline-flex;align-items:center;border-radius:6px;background:var(--bg,#141918);overflow:hidden}.video-key-hints code{padding:4px 7px}.saved-video-key button{border:0;border-left:1px solid var(--border,#35423f);padding:4px 7px;border-radius:0}.video-key-inputs{display:grid;gap:8px}.video-key-inputs>button{justify-self:start}.video-concurrency{padding:11px;border:1px solid var(--border,#35423f);border-radius:9px}.video-concurrency>div{display:grid;margin-right:auto}.video-concurrency label{min-width:100px}.video-setting-message{margin:0;color:#82d9bd}
@media(max-width:760px){.video-settings-grid{grid-template-columns:1fr}.video-concurrency{align-items:stretch}.video-concurrency label{width:100%}}
</style>
