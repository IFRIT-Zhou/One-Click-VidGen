<script setup>
import {ref,useId} from 'vue'
import {useWorkspaceLayout} from '../workspaceLayout'
const props=defineProps({scope:{type:String,required:true},logCount:{type:Number,default:0}})
const {state,reset}=useWorkspaceLayout(props.scope)
const editing=ref(false),dragged=ref(''),over=ref('')
const uid=useId(),labels={preview:'画面预览',prompts:'提示词与画面设置',subtitles:'字幕与画面时序',logs:'任务日志'}
function move(id,offset){const order=[...state.order],from=order.indexOf(id),to=from+offset;if(to<0||to>=order.length)return;order.splice(from,1);order.splice(to,0,id);state.order=order}
function drop(id){if(dragged.value&&dragged.value!==id){const order=state.order.filter(item=>item!==dragged.value);order.splice(order.indexOf(id),0,dragged.value);state.order=order}dragged.value='';over.value=''}
function start(event,id){dragged.value=id;event.dataTransfer.effectAllowed='move';event.dataTransfer.setData('text/plain',id)}
</script>
<template>
 <div class="workspace-panels">
  <div class="layout-toolbar"><span>{{editing?'拖动标题前的手柄，或用箭头调整顺序':'布局自动保存到当前浏览器'}}</span><button type="button" :aria-pressed="editing" @click="editing=!editing">{{editing?'完成布局':'调整布局'}}</button><button type="button" @click="reset">恢复默认布局</button></div>
  <section v-for="(id,index) in state.order" :key="id" class="layout-panel" :class="{'drop-target':over===id,collapsed:state.collapsed[id]}" :data-panel="id" @dragover.prevent="editing&&(over=id)" @drop.prevent="editing&&drop(id)">
   <div class="panel-heading">
    <button v-if="editing" type="button" class="drag-handle" draggable="true" :aria-label="'拖动'+labels[id]" title="拖动调整顺序" @dragstart="start($event,id)" @dragend="dragged='';over=''">⠿</button>
    <button type="button" class="panel-toggle" :aria-expanded="!state.collapsed[id]" :aria-controls="uid+'-'+id" @click="state.collapsed[id]=!state.collapsed[id]"><span>{{state.collapsed[id]?'▸':'▾'}}</span>{{labels[id]}}<small v-if="id==='logs'">{{logCount}} 条</small></button>
    <div v-if="editing" class="panel-move"><button type="button" :disabled="index===0" :aria-label="'上移'+labels[id]" @click="move(id,-1)">↑</button><button type="button" :disabled="index===state.order.length-1" :aria-label="'下移'+labels[id]" @click="move(id,1)">↓</button></div>
   </div>
   <div v-show="!state.collapsed[id]" :id="uid+'-'+id" class="panel-content"><slot :name="id"/></div>
  </section>
 </div>
</template>
<style scoped>
.panel-content :deep(.video-logs){max-height:220px;overflow:auto;overflow-wrap:anywhere;margin:0;font-size:12px;line-height:1.8}
.workspace-panels{min-width:0}.layout-toolbar{display:flex;align-items:center;justify-content:flex-end;gap:8px;flex-wrap:wrap;margin:0 0 12px}.layout-toolbar>span{margin-right:auto;color:var(--muted,#aab8b3);font-size:12px}.layout-toolbar button,.panel-move button,.drag-handle{font-size:12px;padding:5px 9px!important;min-height:30px!important;border:1px solid var(--border,#35423f);border-radius:7px;background:transparent;color:inherit;cursor:pointer}.layout-panel{border:1px solid var(--border,#35423f);border-radius:10px;margin-bottom:12px;min-width:0;overflow:hidden}.panel-heading{display:flex;align-items:center;gap:6px;padding:7px 10px;background:color-mix(in srgb,var(--accent,#81d9bd) 4%,transparent)}.panel-toggle{display:flex;align-items:center;gap:8px;flex:1;text-align:left;min-width:0;font-size:13px;font-weight:600;background:none;border:0;color:inherit;padding:4px!important;cursor:pointer}.panel-toggle small{font-weight:400;color:var(--muted)}.panel-move{display:flex;gap:4px}.panel-move button:disabled{opacity:.35}.drag-handle{cursor:grab;font-size:19px;padding:0 7px!important}.panel-content{padding:14px;min-width:0}.drop-target{outline:2px solid var(--accent,#81d9bd);outline-offset:2px}.panel-toggle:focus-visible,.layout-toolbar button:focus-visible{outline:2px solid var(--accent,#81d9bd)}@media(max-width:600px){.layout-toolbar>span{flex-basis:100%}.panel-content{padding:10px}}
</style>
