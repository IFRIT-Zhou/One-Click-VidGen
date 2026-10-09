<script setup>
import {onMounted,onBeforeUnmount,ref} from 'vue'
import {requestJSON} from '../api'
import ModelInstallGuide from './ModelInstallGuide.vue'
const props=defineProps({mode:{type:String,default:'external'}})
const emit=defineEmits(['changed'])
const state=ref(null),busy=ref(false),error=ref(''),modelDirectory=ref(''),logs=ref(''),showLogs=ref(false)
const nodes=ref(null)
async function checkNodes(){try{nodes.value=await requestJSON('/api/comfyui/managed/nodes')}catch(e){error.value=e.message}}
async function openNodes(){await action('nodes/open-folder');await checkNodes()}
let timer=null,disposed=false
async function refresh(initial=false){
 try{state.value=await requestJSON('/api/comfyui/managed');if(initial)modelDirectory.value=state.value.model_directory||''}
 catch(e){error.value=e.message}
 finally{if(!disposed){clearTimeout(timer);timer=setTimeout(()=>refresh(),3000)}}
}
async function action(path,method='POST',body){
 busy.value=true;error.value=''
 try{await requestJSON('/api/comfyui/managed/'+path,{method,headers:{'Content-Type':'application/json'},...(body?{body:JSON.stringify(body)}:{})});await refresh();if(path==='selection')emit('changed')}
 catch(e){error.value=e.message}finally{busy.value=false}
}
async function readLogs(){showLogs.value=!showLogs.value;if(showLogs.value){try{logs.value=(await requestJSON('/api/comfyui/managed/logs')).text}catch(e){error.value=e.message}}}
onMounted(()=>{refresh(true);checkNodes()});onBeforeUnmount(()=>{disposed=true;clearTimeout(timer)})
</script>

<template>
 <article class="managed-engine">
  <header><div><h2>OCV 内置视频引擎 <span>预览版</span></h2><p>独立 Python 环境，由 OCV 启动；外部 ComfyUI 配置会保留。</p></div><b class="state">{{!state?'检查中':!state.installed?'未安装':({stopped:'未启动',starting:'正在启动',ready:'已就绪',failed:'启动异常'})[state.state]}}</b></header>
  <template v-if="state?.installed">
   <div class="engine-summary"><span>版本 {{state.version}}</span><span>{{state.models_ready?'所需模型已齐备':'模型尚未齐备'}}</span><span>{{mode==='managed'?'工作台连接：内置引擎':'工作台连接：外部 ComfyUI'}}</span></div>
   <div class="engine-actions">
    <button v-if="mode!=='managed'" class="primary" :disabled="busy" @click="action('selection','PUT',{mode:'managed'})">使用内置引擎与预置工作流</button>
    <button v-else :disabled="busy||state.active_tasks>0" @click="action('selection','PUT',{mode:'external'})">切回外部 ComfyUI</button>
    <button v-if="state.state!=='ready'" :disabled="busy||state.state==='starting'||!state.any_models_ready" @click="action('start')">{{state.state==='starting'?'正在启动…':'启动引擎'}}</button>
    <template v-else><a v-if="state.maintenance_enabled" :href="state.base_url" target="_blank" rel="noopener noreferrer">维护工作流 ↗</a><button :disabled="busy||state.active_tasks>0" @click="action('stop')">关闭引擎并释放显存</button></template>
    <button @click="readLogs">{{showLogs?'收起日志':'查看引擎日志'}}</button>
   </div>
   <p class="hint">生成时自动启动内置引擎。ComfyUI 功能包解压到 OCV 根目录，拓展节点位于 comfyui/custom_nodes。</p>
   <ModelInstallGuide />
   <details><summary>高级：复用已有模型目录</summary><p>默认读取整合包 models/comfyui，兼容旧版 runtime/comfyui/models。也可复用已有 ComfyUI 的 models 文件夹。</p><div class="model-path"><input v-model.trim="modelDirectory" placeholder="现有模型根目录（可选）" :disabled="state.state==='ready'||state.state==='starting'"><button :disabled="busy||state.state==='ready'||state.state==='starting'" @click="action('settings','PUT',{model_directory:modelDirectory})">保存模型目录</button></div></details>
  </template>
  <details class="user-nodes"><summary>拓展节点 · comfyui/custom_nodes</summary>
   <p>配套节点包直接解压到 OCV 根目录。手动安装时，节点应位于 comfyui/custom_nodes/节点名称/__init__.py。安装后关闭并重新启动内置引擎，再检查是否加载成功。</p>
   <div class="engine-actions"><button :disabled="busy" @click="openNodes">打开节点目录</button><button @click="checkNodes">重新检查节点</button><a href="https://github.com/facok/comfyui-SelfLift" target="_blank" rel="noopener noreferrer">SelfLift 作者仓库 ↗</a></div>
   <p v-if="nodes">{{nodes.directory}}</p>
   <p v-for="profile in nodes?.profiles||[]" :key="profile.id">{{profile.name}}：{{!profile.checked?'启动引擎后可检查':profile.missing_nodes.length?'未加载：'+profile.missing_nodes.join('、'):'所需节点已加载'}}</p>
   <p>SelfLift 由用户自行安装。插件缺少 Python 依赖或与内置节点冲突时，请查看引擎日志；依赖应安装到内置引擎的 Python 环境。</p>
  </details>
  <p v-if="state&&!state.installed">尚未安装内置引擎。将 ComfyUI 功能包解压到 OCV 根目录，形成 comfyui/engine 后重新检查；模型包也解压到 OCV 根目录。</p>
  <p v-if="error||state?.error" role="alert" class="engine-error">{{error||state.error}}</p>
  <pre v-if="showLogs">{{logs}}</pre>
 </article>
</template>

<style scoped>
.managed-engine{padding:22px;border:1px solid var(--border,#34403e);border-radius:12px;background:var(--panel,#1c2221)}header{display:flex;align-items:center;justify-content:space-between;gap:16px}h2{margin:0;font-size:18px}h2 span{font-size:11px;font-weight:400;padding:3px 7px;border:1px solid var(--border,#34403e);border-radius:6px}p,.engine-summary{color:var(--muted,#9aaba5);font-size:12px;line-height:1.7}.state{font-size:12px;white-space:nowrap;color:var(--accent,#83dec5)}.engine-summary,.engine-actions{display:flex;gap:12px;flex-wrap:wrap;align-items:center}.engine-summary{margin:12px 0}.engine-actions a{color:var(--accent,#83dec5);font-size:12px;padding:8px}.hint{margin:14px 0}details{border-top:1px solid var(--border,#34403e);padding-top:12px}summary{cursor:pointer;font-size:13px}.model-path{display:flex;gap:10px}.model-path input{flex:1;min-width:0}ul{padding:0;list-style:none}li{display:flex;gap:12px;justify-content:space-between;padding:5px 0;font-size:12px;overflow-wrap:anywhere}li small{white-space:nowrap}.engine-error{color:#e6a08d}pre{max-height:300px;overflow:auto;white-space:pre-wrap;font-size:11px;background:#101413;padding:12px;border-radius:8px}@media(max-width:600px){header,.model-path{flex-direction:column;align-items:stretch}}
</style>
