<template>
 <details class="scene-assets" @toggle="opened">
  <summary>场景参考资产 <small>独立于视频时间轴</small></summary>
  <p>编辑或替换只更新参考资产，不自动重绘已生成画面。相关镜头下次使用场景参考重绘时生效。</p>
  <p v-if="error" role="alert">{{error}}</p>
  <p v-if="!items.length">此项目暂无场景参考资产。</p>
  <article v-for="item in items" :key="item.id">
   <a :href="item.image_url" target="_blank" rel="noopener"><img :src="item.image_url+'?v='+revision" :alt="item.name" /></a>
   <div><strong>{{item.name}}</strong><small>引用镜头：{{item.used_by.join('、')}}</small>
   <textarea v-model="item.prompt" rows="4" placeholder="旧项目未保存场景提示词，可在此填写无人场景描述" />
   <button :disabled="item.task?.status==='running'||!item.prompt.trim()" @click="redraw(item)">按此提示词重绘场景（计费）</button>
   <label>上传替换<input type="file" accept="image/png,image/jpeg,image/webp" :disabled="item.task?.status==='running'" @change="upload(item,$event)" /></label>
   <small>{{item.task?.message}}</small></div>
  </article>
 </details>
</template>
<script setup>
import {ref,onBeforeUnmount,watch} from 'vue'
import {api} from '../api'
const props=defineProps({jobId:String})
const items=ref([]),error=ref(''),revision=ref(Date.now())
let timer=null,active=false
async function load(){try{const r=await fetch(`/api/jobs/${props.jobId}/scene-assets`,{credentials:'same-origin'});if(!r.ok)throw Error('无法读取场景资产');const data=await r.json();const drafts=new Map(items.value.map(i=>[i.id,i.prompt]));items.value=data.items.map(i=>({...i,prompt:drafts.get(i.id)??i.prompt}));revision.value=Date.now()}catch(e){error.value=e.message}}
function opened(e){active=e.target.open;clearInterval(timer);if(active){load();timer=setInterval(load,4000)}}
async function redraw(item){if(!confirm('仅重绘场景参考图，会产生一张图片的费用；不自动重绘视频画面。继续？'))return;try{await api.redrawVisualImage(props.jobId,item.id,item.prompt);item.task={status:'running',message:'场景参考重绘中'}}catch(e){error.value=e.message}}
async function upload(item,e){const file=e.target.files?.[0];if(!file)return;try{await api.uploadVisualImage(props.jobId,item.id,file);item.task={status:'running',message:'替换中'}}catch(err){error.value=err.message}finally{e.target.value=''}}
watch(()=>props.jobId,()=>{items.value=[];if(active)load()})
onBeforeUnmount(()=>clearInterval(timer))
</script>
<style scoped>
.scene-assets{margin:12px 0;padding:16px;border:1px solid #ffffff22;border-radius:12px}summary{cursor:pointer;font-weight:600}small{display:block;opacity:.7}article{display:grid;grid-template-columns:180px minmax(0,1fr);gap:16px;margin-top:16px}img{width:100%;border-radius:8px}textarea{width:100%;box-sizing:border-box;margin:8px 0}button,label{display:inline-block;margin:4px 12px 4px 0}input{max-width:220px}@media(max-width:700px){article{grid-template-columns:1fr}}
</style>
