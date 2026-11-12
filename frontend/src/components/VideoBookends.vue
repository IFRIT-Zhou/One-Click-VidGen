<script setup>
import {ref} from 'vue'
import {api} from '../api'
const props=defineProps({settings:{type:Object,required:true},disabled:Boolean})
const busy=ref(''),error=ref('')
const rows=[{key:'intro_video_id',name:'片头'},{key:'outro_video_id',name:'片尾'}]
async function upload(event,row){
 const file=event.target.files?.[0];event.target.value='';if(!file)return
 busy.value=row.key;error.value=''
 try{const result=await api.uploadEditorAsset(file);if(result.asset.kind!=='video')throw new Error('请选择视频文件');props.settings[row.key]=result.asset.id}
 catch(e){error.value=e.message}finally{busy.value=''}
}
</script>
<template>
 <section class="bookends"><h3>片头与片尾 <small>可选 · 本地视频</small></h3>
  <p class="muted">导出时按「片头 → 正文 → 片尾」拼接，保留片段原声，不改变正文配音和分镜；画幅不同时完整显示并留边。修改后需重新合成。</p>
  <div class="bookend-grid"><div v-for="row in rows" :key="row.key">
   <strong>{{row.name}}</strong>
   <template v-if="settings[row.key]"><video controls preload="metadata" :src="'/api/editor/uploads/'+encodeURIComponent(settings[row.key])"/><small class="asset-name">{{settings[row.key].replace(/^[^_]+_/,'')}}</small><button type="button" :disabled="disabled||!!busy" @click="settings[row.key]=''">移除{{row.name}}</button></template>
   <label class="upload">{{busy===row.key?'正在上传…':settings[row.key]?'替换视频':'上传视频'}}<input type="file" accept="video/*,.mp4,.mov,.mkv,.webm" :disabled="disabled||!!busy" @change="upload($event,row)"/></label>
  </div></div><p v-if="error" role="alert">{{error}}</p>
 </section>
</template>
<style scoped>
.bookends{border:1px solid #53656366;border-radius:12px;padding:16px;margin:16px 0}.bookends h3{margin:0 0 8px}.bookends small{font-weight:normal;font-size:12px}.bookend-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.bookend-grid>div{display:flex;flex-direction:column;gap:8px;min-width:0}.bookends video{width:100%;max-height:170px;background:#111}.asset-name{overflow-wrap:anywhere}.upload{padding:10px;border:1px dashed #799c96;border-radius:8px;cursor:pointer}.upload input{display:block;max-width:100%;margin-top:6px}.bookends [role=alert]{color:#ff9a9a}@media(max-width:650px){.bookend-grid{grid-template-columns:1fr}}
</style>
