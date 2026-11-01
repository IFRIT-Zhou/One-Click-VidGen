<script setup>
import { computed, ref, watch } from 'vue'
import { findTtsReplacements } from '../ttsFindReplace'
const props = defineProps({editor:Object, drafts:Object, busy:Boolean, projectId:String, saveSubtitles:Function})
const emit = defineEmits(['reading','subtitle-sync','select'])
const open=ref(false), search=ref(''), replacement=ref(''), mode=ref('pronunciation'), notice=ref(''), saving=ref(false)
const locked=computed(()=>saving.value || props.busy || props.editor?.task?.status==='running' || !props.editor?.available)
const matches=computed(()=>findTtsReplacements(props.editor?.segments,props.drafts||{},search.value,replacement.value,mode.value))
const invalid=computed(()=>matches.value.some(row=>!row.after.trim() || row.after.length>1200))
watch(()=>props.projectId,()=>{open.value=false;search.value='';replacement.value='';mode.value='pronunciation';notice.value=''})
watch([mode,search,replacement],()=>{notice.value=''})
async function apply(){
  if(locked.value || invalid.value || !matches.value.length)return
  const rows=matches.value
  const projectId=props.projectId
  if(mode.value==='subtitle'){
    saving.value=true
    try{
      const result=await props.saveSubtitles(rows)
      if(projectId===props.projectId && result)notice.value=`已保存 ${rows.length} 句字幕；朗读文本、配音及时间轴未改变。已导出的视频需重新渲染。`
    }catch(error){if(projectId===props.projectId)notice.value=error.message || '字幕保存失败，请重试。'}
    finally{saving.value=false}
    return
  }
  for(const row of rows){
    emit('reading',row.item,{target:{value:row.after}})
    emit('subtitle-sync',row.item,mode.value==='both')
  }
  emit('select',rows.map(row=>row.item.index))
  notice.value=`已填入 ${rows.length} 句并勾选；请点击“重配选中句”。${mode.value==='both'?'重配成功后同步字幕。':'字幕保持原文。'}`
}
</script>
<template>
  <div class="tts-find-replace">
    <button type="button" :disabled="locked" :aria-expanded="open" @click="open=!open">发音 / 字幕查找替换</button>
    <section v-if="open" class="tts-replace-panel" aria-label="批量查找替换">
      <header><strong>批量查找替换</strong><button type="button" @click="open=false" aria-label="关闭查找替换">×</button></header>
      <label class="replace-mode">修改范围<select v-model="mode" :disabled="locked"><option value="pronunciation">仅修正发音</option><option value="subtitle">仅修改字幕</option><option value="both">发音与字幕一起改</option></select></label>
      <div class="replace-fields">
        <label>查找<input v-model="search" :disabled="locked" placeholder="字或词，如：癌" maxlength="1200" /></label>
        <label>替换为<input v-model="replacement" :disabled="locked" :placeholder="mode==='subtitle'?'输入希望显示的字幕文字':'输入希望朗读的同音字或词'" maxlength="1200" /></label>
      </div>
      <p v-if="mode==='subtitle'">只查找当前显示字幕，直接保存替换结果；不修改朗读文本、不勾选重配、不调用 TTS，配音时长和时间轴保持不变。</p>
      <p v-else>在全部句子的当前朗读文本中查找，保留已有其他修正。应用后仅勾选本次命中的句子，不会自动配音或扣费。{{mode==='both'?'重配成功后同步字幕。':'字幕保持原文。'}}</p>
      <p v-if="search">命中 {{matches.length}} 句、{{matches.reduce((n,row)=>n+row.count,0)}} 处</p>
      <div class="replace-results"><article v-for="row in matches" :key="row.item.index"><strong>第 {{row.item.index}} 句</strong><div class="before">{{row.before}}</div><div>{{row.after}}</div></article></div>
      <p v-if="invalid" role="alert">替换后文本不能为空或超过 1200 字，请调整替换内容。</p>
      <footer><button type="button" :disabled="locked || invalid || !matches.length" @click="apply">{{saving?'正在保存字幕…':mode==='subtitle'?'仅保存字幕替换':'填入替换并勾选命中句'}}</button></footer>
      <p v-if="notice" role="status">{{notice}}</p>
    </section>
  </div>
</template>
<style scoped>
.tts-find-replace{margin-left:auto;position:relative}.tts-replace-panel{position:absolute;right:0;top:calc(100% + 8px);width:min(580px,85vw);padding:18px;z-index:30;background:var(--panel,#1d2726);border:1px solid var(--border,#526361);border-radius:14px;box-shadow:0 12px 36px #0006;color:var(--text,#eee)}
.replace-mode{display:grid;gap:6px;margin-top:12px}.replace-mode select{width:100%}
header,footer{display:flex;align-items:center;justify-content:space-between;gap:12px}.replace-fields{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:12px 0}.replace-fields label{display:grid;gap:6px;min-width:0}.replace-fields input{width:100%;box-sizing:border-box}.replace-sync{display:flex;align-items:center;gap:8px}.replace-sync input{width:16px;height:16px;min-height:0;margin:0}p{font-size:12px;line-height:1.6;color:var(--muted,#afbfba)}.replace-results{max-height:260px;overflow:auto;overflow-wrap:anywhere}.replace-results article{padding:10px 0;border-top:1px solid #ffffff20;font-size:13px;line-height:1.6}.before{color:var(--muted,#afbfba);text-decoration:line-through}footer{justify-content:flex-end;margin-top:12px}@media(max-width:600px){.replace-fields{grid-template-columns:1fr}.tts-find-replace{margin-left:0}.tts-replace-panel{position:fixed;top:15vh;left:5vw;right:5vw;width:auto;max-height:70vh;overflow:auto}}
</style>
