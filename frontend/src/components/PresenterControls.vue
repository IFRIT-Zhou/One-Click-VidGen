<script setup>
import {ref,computed,watch} from 'vue'
const props=defineProps({shot:Object,shots:Array,disabled:Boolean})
const emit=defineEmits(['apply'])
const open=ref(false),speaker=ref(''),ids=ref([])
const enabled=computed(()=>props.shot?.presenter_override?.enabled??props.shot?.visual_design?.presenter_speaking===true)
watch(()=>props.shot?.id,()=>{open.value=false;speaker.value='';ids.value=[props.shot?.id]},{immediate:true})
function apply(value){
 if(!ids.value.length)return
 if(!confirm(value?'将调用语言模型核对所选核心图，并仅更新对口型提示词和音频设置。保留配音、图片、时间轴和旧视频历史，不自动生成视频。是否继续？':'关闭所选镜头对口型，改为画外音方案。旧视频保留在历史中，需要手动重新生成。是否继续？'))return
 emit('apply',{ids:[...ids.value],enabled:value,speaker:speaker.value.trim()})
}
</script>
<template>
 <section v-if="shot?.kind==='video'" class="presenter-control">
  <div class="presenter-head"><strong>讲解员对口型 <small>{{enabled?'已开启':'未开启'}}</small></strong><button type="button" :disabled="disabled" @click="open=!open">{{open?'收起':'设置 / 批量设置'}}</button></div>
  <p v-if="shot.presenter_plan_status==='ready'">方案已就绪，生成视频时使用本镜最终配音。更换图片或讲话人物后请重新生成方案。</p>
  <p v-if="shot.presenter_plan_status==='audio_changed'" role="status">配音或时长已改变，旧视频口型需要重新生成；仅裁切或降速不能保证口型同步。</p>
  <template v-if="open">
   <p>使用实际核心图中的人物，无需另传照片。图片没有人物或嘴部遮挡时，请先重绘。</p>
   <label>讲话人物（单人可留空）<input v-model="speaker" maxlength="500" placeholder="例如：左侧穿白大褂的医生；多人时请明确指定" :disabled="disabled"></label>
   <details><summary>应用镜头 · 已选 {{ids.length}} 镜</summary><div class="presenter-shots"><label v-for="(item,index) in shots" :key="item.id"><template v-if="item.kind==='video'"><input v-model="ids" type="checkbox" :value="item.id" :disabled="disabled">第 {{index+1}} 镜</template></label></div></details>
   <div class="presenter-actions"><button type="button" class="presenter-plan" :disabled="disabled||!ids.length" @click="apply(true)">核对画面并生成对口型方案</button><button type="button" :disabled="disabled||!ids.length" @click="apply(false)">关闭所选镜头对口型</button></div>
   <small>仅更新所选镜头，不生成图片或视频；需使用支持参考音频的 ComfyUI 工作流。</small>
  </template>
 </section>
</template>
<style scoped>
.presenter-control{margin-top:14px;padding:14px;border:1px solid var(--border,#35423f);border-radius:10px;display:grid;gap:10px}.presenter-head,.presenter-actions{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap}.presenter-control p,.presenter-control small{font-size:12px;color:var(--muted,#aab8b3);line-height:1.6;margin:0}.presenter-control label{display:grid;gap:7px}.presenter-control input:not([type=checkbox]){width:100%;box-sizing:border-box;padding:10px}.presenter-shots{display:flex;flex-wrap:wrap;gap:12px;padding:12px 0}.presenter-shots label{display:flex;align-items:center}.presenter-control .presenter-plan{background:#b9a6ff;color:#251a42;border:1px solid #b9a6ff}.presenter-control button:disabled{opacity:.45}
</style>
