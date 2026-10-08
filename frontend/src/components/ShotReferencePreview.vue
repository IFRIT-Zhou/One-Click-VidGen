<script setup>
import {computed,ref,watch} from 'vue'
const props=defineProps({shot:{type:Object,required:true},references:{type:Array,default:()=>[]},scene:Object,sceneEnabled:Boolean,referenceUrl:Function,imageUrl:Function})
const enlarged=ref(null)
const kinds={character:'人物参考',person:'人物参考',style:'画风参考',scene:'场景参考',object:'物件参考'}
const materials=computed(()=>{
 const ids=props.shot.reference_ids??props.shot.reference_image_ids??[]
 const rows=ids.map(id=>{
  const asset=props.references.find(row=>String(row.id)===String(id))
  return {id,asset,name:asset?.description||asset?.label||String(id),purpose:kinds[asset?.kind]||asset?.kind||'参考素材',url:asset?props.referenceUrl(asset):'',state:asset?'':'素材缺失，请检查'}
 })
 if(props.sceneEnabled&&props.scene)rows.push({id:'scene:'+props.scene.id,name:props.scene.name||'场景参考',purpose:'场景参考 · 自动追加',url:props.scene.image_status==='completed'?props.imageUrl(props.scene):'',state:props.scene.image_status==='failed'?'场景参考生成失败':'场景参考待生成'})
 return rows
})
watch(()=>props.shot.id,()=>{enlarged.value=null})
</script>

<template>
 <section class="planned-references" aria-label="本镜规划参考图">
  <header><strong>本镜规划参考图</strong><small>{{materials.length?`${materials.length} 张 · 按出图提交顺序`:'纯文生图 · 未使用参考图'}}</small></header>
  <p class="reference-help">{{materials.length?'点击图片放大核对；参考图用于约束内容，不代表最终画面。重绘时以“重绘参考素材”中的选择为准。':'本镜仅依据提示词生成，不会自动使用其他镜头或素材库中的图片。'}}</p>
  <div v-if="materials.length" class="planned-reference-grid">
   <button v-for="(row,index) in materials" :key="row.id+':'+index" type="button" :disabled="!row.url" @click="enlarged=row">
    <img v-if="row.url" :src="row.url" :alt="row.name"><span v-else class="reference-placeholder">{{row.state}}</span>
    <span class="reference-caption"><b>图{{index+1}} · {{row.purpose}}</b><small>{{row.name}}</small></span>
   </button>
  </div>
 </section>
 <Teleport to="body"><div v-if="enlarged" class="planned-reference-overlay" @click.self="enlarged=null" @keydown.esc="enlarged=null"><section role="dialog" aria-modal="true" :aria-label="enlarged.name"><header><strong>{{enlarged.name}}</strong><button type="button" autofocus @click="enlarged=null">关闭</button></header><img :src="enlarged.url" :alt="enlarged.name"></section></div></Teleport>
</template>

<style scoped>
.planned-references{padding:14px;border:1px solid var(--border,#35423f);border-radius:10px;margin-bottom:16px}.planned-references header{display:flex;gap:12px;align-items:center;flex-wrap:wrap}.planned-references small,.reference-help{color:var(--muted,#aab8b3);font-size:12px}.reference-help{margin:8px 0;line-height:1.6}.planned-reference-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.planned-reference-grid button{padding:0;text-align:left;overflow:hidden;border:1px solid var(--border,#35423f);border-radius:8px;background:var(--panel,#1e2625);color:inherit;max-width:260px}.planned-reference-grid button:disabled{opacity:1;cursor:default}.planned-reference-grid img,.reference-placeholder{width:100%;height:112px;object-fit:contain;background:#0c100f;display:block}.reference-placeholder{display:grid;place-items:center;color:#e6c37d;font-size:12px}.reference-caption{display:block;padding:9px}.reference-caption b,.reference-caption small{display:block;line-height:1.5;overflow-wrap:anywhere}.reference-caption b{font-size:12px}.planned-reference-overlay{position:fixed;inset:0;z-index:10000;background:#000c;display:grid;place-items:center;padding:24px}.planned-reference-overlay section{max-width:1100px;width:100%;background:var(--panel,#1e2625);padding:16px;border-radius:12px;color:var(--text,#eee)}.planned-reference-overlay header{display:flex;justify-content:space-between;align-items:center;gap:16px;margin-bottom:12px}.planned-reference-overlay img{display:block;width:100%;max-height:78vh;object-fit:contain}.planned-reference-overlay button{padding:8px 16px}
</style>
