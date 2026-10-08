<script setup>
import {computed,ref,watch,onBeforeUnmount} from 'vue'
import {requestJSON} from '../api'
const props=defineProps({kind:{type:String,default:'comfyui'},profileId:{type:String,default:''}})
const info=ref(null),error=ref(''),busy=ref(false),expanded=ref(false),selected=ref(''),copied=ref('')
let sequence=0,timer=null
const missing=computed(()=>info.value?.items.filter(x=>!x.ready)||[])
const items=computed(()=>[...(info.value?.items||[])].sort((a,b)=>Number(a.ready)-Number(b.ready)))
const chosen=computed(()=>props.profileId||selected.value)
async function refresh(){
 clearTimeout(timer)
 const ticket=++sequence;busy.value=true;error.value=''
 try{const result=await requestJSON('/api/comfyui/managed/models?'+new URLSearchParams({kind:props.kind,profile_id:chosen.value}));if(ticket===sequence)info.value=result}
 catch(e){if(ticket===sequence)error.value=e.message}
 finally{if(ticket===sequence){busy.value=false;if(info.value?.verification?.running)timer=setTimeout(refresh,1500)}}
}
async function verify(){try{await requestJSON('/api/comfyui/managed/models/verify',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:props.kind,profile_id:chosen.value})});await refresh()}catch(e){error.value=e.message}}
async function openFolder(path=''){
 try{await requestJSON('/api/comfyui/managed/models/open-folder',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({kind:props.kind,profile_id:chosen.value,path})})}catch(e){error.value=e.message}
}
async function copy(path){try{await navigator.clipboard.writeText(path);copied.value=path}catch{error.value='复制未成功，请选中路径手动复制。'}}
function size(bytes){return bytes?(bytes/1024**3>=.1?(bytes/1024**3).toFixed(2)+' GB':(bytes/1024**2).toFixed(1)+' MB'):''}
watch(()=>[props.kind,props.profileId,selected.value],()=>{info.value=null;refresh()},{immediate:true})
onBeforeUnmount(()=>{sequence++;clearTimeout(timer)})
</script>
<template>
 <section class="model-guide" :class="{missing:missing.length}" aria-label="模型安装指引">
  <div class="guide-heading"><strong>{{busy?'检查模型中…':info?.runtime_missing?.length?'基础环境未齐备':!info?'模型检查暂不可用':!info.items.length?'尚无模型清单':missing.length?`缺少或不完整：${missing.length} 项模型`:'模型文件已齐备'}}</strong><button type="button" @click="expanded=!expanded">{{expanded?'收起安装指引':'模型安装指引'}}</button><button type="button" :disabled="busy" @click="refresh">重新检查</button></div>
  <p v-if="error" role="alert">{{error}}</p>
  <div v-if="expanded&&info" class="guide-details">
   <label v-if="!profileId&&info.profiles.length">按工作流查看<select v-model="selected"><option value="">全部内置工作流</option><option v-for="p in info.profiles" :key="p.id" :value="p.id">{{p.name}}</option></select></label>
   <p>{{info.message}}</p>
   <p v-if="info.runtime_missing.length" role="alert">缺少基础环境：{{info.runtime_missing.join('、')}}。请安装对应的 OCV 引擎组件。</p>
   <div class="guide-path"><code>{{info.directory}}</code><button type="button" @click="openFolder()">打开模型文件夹</button><button type="button" @click="copy(info.directory)">{{copied===info.directory?'已复制':'复制路径'}}</button></div>
   <small v-if="info.directory!==info.default_directory">正在兼容使用原模型目录。新版统一目录：{{info.default_directory}}</small>
   <ol><li>按需下载所选功能的模型，保留模型包中的目录结构。</li><li>把文件放入下面标明的位置，避免多套一层 models 文件夹。</li><li>下载或解压完成后，点击“重新检查”，再开始生成。</li></ol>
   <div v-for="item in items" :key="item.path" class="model-item">
    <div><b>{{item.ready?'✓ 已找到':item.actual_bytes?'文件大小异常':'待补齐'}}</b><span>{{size(item.bytes)}}</span></div>
    <code>{{item.path}}</code><small v-if="item.used_by?.length">用于：{{item.used_by.join('、')}}</small>
    <small v-if="item.ready&&item.found_path">实际读取：{{item.found_path}}</small>
    <div class="file-actions"><a v-if="item.download_url" :href="item.download_url" target="_blank" rel="noopener noreferrer">模型下载页 ↗</a><span v-else-if="!item.ready">从完整配套模型包补齐</span><button type="button" @click="openFolder(item.path)">打开存放文件夹</button><button type="button" @click="copy(item.install_path)">{{copied===item.install_path?'已复制':'复制完整路径'}}</button></div>
   </div>
   <template v-if="kind==='comfyui'"><button type="button" :disabled="info.verification?.running||busy" @click="verify">{{info.verification?.running?'正在完整校验…':'完整校验文件'}}</button><p>完整校验会读取大文件，可能需要几分钟；不会加载模型或占用显存。缺少发布方校验值的文件会单独标明。</p><ul v-if="info.verification?.items?.length"><li v-for="item in info.verification.items" :key="item.name">{{item.name}}：{{({checking:'校验中',passed:'内容校验通过',missing:'缺少或大小不符',no_checksum:'仅通过大小检查，暂无校验值',mismatch:'内容不匹配，请重新下载',changed:'文件发生变化，请重新检查',unreadable:'无法读取文件'})[item.state]}}</li></ul></template>
   <p>日常检查文件存在和大小，不代表显存足够。只使用集群或 API 时，可以不下载本地模型。</p>
  </div>
 </section>
</template>
<style scoped>
:global(.local-tts-install-dialog){max-height:calc(100vh - 48px);overflow-y:auto}
.model-guide{margin:12px 0;padding:12px;border:1px solid var(--border,#40514a);border-radius:9px;font-size:12px;background:#18221f}.model-guide.missing{border-color:#8d784d}.guide-heading,.guide-path,.file-actions,.model-item>div{display:flex;align-items:center;gap:10px;flex-wrap:wrap}.guide-heading strong{flex:1;min-width:150px}.model-guide button,.model-guide a{font-size:12px;padding:6px 9px}.model-guide a{color:var(--accent,#83dec5)}.guide-details{margin-top:14px;max-height:65vh;overflow:auto}.guide-details label{display:grid;gap:7px}.guide-details p,.guide-details li,.guide-details small{color:var(--muted,#a9b7b1);line-height:1.7}.guide-path code{flex:1;min-width:160px}.model-guide code{overflow-wrap:anywhere;white-space:normal;user-select:text}.model-item{display:grid;gap:7px;margin-top:10px;padding:12px;border:1px solid var(--border,#40514a);border-radius:7px}.model-item>div:first-child{justify-content:space-between}.model-item b{color:var(--accent,#83dec5)}.file-actions button{background:transparent}
</style>
