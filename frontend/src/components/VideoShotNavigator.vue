<script setup>
import {computed,ref} from 'vue'
const props=defineProps({shots:{type:Array,default:()=>[]},selected:Number,imageUrl:Function,motion:Boolean,statusLabel:Function,hasWarning:Function})
const emit=defineEmits(['select'])
const filter=ref('all')
const rows=computed(()=>props.shots.map((shot,index)=>({shot,index})).filter(({shot})=>filter.value==='all'||(filter.value==='attention'?(props.hasWarning?.(shot)||['failed','unknown'].includes(shot.video_status)||shot.image_status==='failed'):shot.kind===filter.value)))
</script>
<template>
 <aside class="shot-navigator" aria-label="镜头导航">
  <div class="navigator-heading"><strong>镜头列表</strong><span>{{shots.length}} 镜</span></div>
  <select v-model="filter" aria-label="筛选镜头"><option value="all">全部镜头</option><option value="video">动态镜头</option><option value="static">静态镜头</option><option value="attention">需要留意</option></select>
  <div class="navigator-list">
   <button v-for="{shot,index} in rows" :key="shot.id" type="button" :aria-current="selected===index?'true':undefined" :class="{active:selected===index,warning:hasWarning?.(shot)||['failed','unknown'].includes(shot.video_status)||shot.image_status==='failed'}" @click="emit('select',index)">
    <img v-if="shot.image_status==='completed'&&imageUrl" :src="imageUrl(shot)" loading="lazy" alt=""><span v-else class="thumbnail-placeholder">{{String(index+1).padStart(2,'0')}}</span>
    <div class="shot-copy"><b>{{String(index+1).padStart(2,'0')}} <small>{{shot.kind==='video'?'动态':'静态'}}</small></b><time>{{shot.start.toFixed(1)}}–{{shot.end.toFixed(1)}}s</time><span class="shot-intent">{{shot.intent||'待设计镜头'}}</span><em v-if="motion">{{statusLabel?.(shot)}}</em><em v-else-if="hasWarning?.(shot)">待核对</em><em v-else-if="shot.image_status==='failed'">图片生成失败</em></div>
   </button>
   <p v-if="!rows.length" class="navigator-empty">没有符合条件的镜头</p>
  </div>
 </aside>
</template>
<style scoped>
.shot-navigator{position:sticky;top:100px;align-self:start;display:flex;flex-direction:column;gap:12px;max-height:calc(100vh - 132px);min-width:0!important;overflow:hidden!important}.navigator-heading{display:flex;justify-content:space-between;align-items:center;font-size:14px}.navigator-heading>span{font-size:12px;color:var(--muted)}.shot-navigator select{width:100%;font-size:12px;padding:8px 10px!important}.navigator-list{overflow-y:auto;overflow-x:hidden;scrollbar-width:thin;padding-right:4px}.shot-navigator .navigator-list button{display:grid!important;grid-template-columns:64px minmax(0,1fr);gap:10px!important;padding:11px!important;margin-bottom:8px!important;width:100%;text-align:left;border:1px solid var(--border,#35423f);border-radius:10px;background:transparent;color:inherit}.navigator-list button.active{background:color-mix(in srgb,var(--accent,#81d9bd) 12%,var(--panel,#1e2625));border-color:var(--accent,#81d9bd)}.navigator-list button.warning{border-left:3px solid #d8aa4b}.navigator-list img,.thumbnail-placeholder{width:64px;height:52px;object-fit:contain;background:#111917;border-radius:6px;align-self:start}.thumbnail-placeholder{display:grid!important;place-items:center;color:var(--muted);font-size:20px}.shot-copy{min-width:0;display:grid;gap:4px}.shot-copy b{display:flex;align-items:center;gap:7px;font-size:14px}.shot-copy small,.shot-copy time,.shot-copy em{font-size:11px;font-style:normal;color:var(--muted)}.shot-copy .shot-intent{display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden;font-size:12px;line-height:1.6}.navigator-empty{font-size:13px;color:var(--muted);padding:16px 0}
@media(max-width:900px){.shot-navigator{position:static;max-height:none!important}.navigator-list{display:flex;gap:8px;overflow-x:auto}.shot-navigator .navigator-list button{flex:0 0 215px;margin-bottom:3px!important}.shot-navigator select{max-width:180px}.navigator-heading{display:none}}
</style>
