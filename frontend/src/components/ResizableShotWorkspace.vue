<script setup>
import {ref,onUnmounted} from 'vue'
import {useWorkspaceLayout} from '../workspaceLayout'
const props=defineProps({scope:{type:String,required:true}})
const {state}=useWorkspaceLayout(props.scope)
const root=ref(null),dragging=ref(false)
let startX=0,startWidth=0,target=null
function setWidth(width){state.width=Math.round(Math.max(180,Math.min(440,(root.value?.clientWidth||1000)*.45,width)))}
function down(event){if(event.button!==0)return;startX=event.clientX;startWidth=state.width;target=event.currentTarget;target.setPointerCapture(event.pointerId);dragging.value=true;event.preventDefault()}
function move(event){if(dragging.value)setWidth(startWidth+event.clientX-startX)}
function end(){dragging.value=false;target=null}
function key(event){if(['ArrowLeft','ArrowRight','Home'].includes(event.key)){event.preventDefault();setWidth(event.key==='Home'?248:state.width+(event.key==='ArrowLeft'?-16:16))}}
onUnmounted(end)
</script>
<template>
 <div ref="root" class="resizable-workspace" :class="{'is-resizing':dragging}" :style="{'--sidebar-width':state.width+'px'}">
  <div class="workspace-sidebar"><slot name="sidebar"/></div>
  <div class="workspace-divider" role="separator" aria-label="调整分镜列表宽度" aria-orientation="vertical" :aria-valuenow="state.width" :aria-valuemin="180" :aria-valuemax="440" tabindex="0" title="拖动调整宽度；双击恢复默认" @pointerdown="down" @pointermove="move" @pointerup="end" @pointercancel="end" @lostpointercapture="end" @keydown="key" @dblclick="state.width=248"><span/></div>
  <div class="workspace-content"><slot/></div>
 </div>
</template>
<style scoped>
.resizable-workspace{display:grid!important;grid-template-columns:minmax(180px,min(var(--sidebar-width),45%)) 16px minmax(0,1fr)!important;gap:0!important;align-items:start}.workspace-sidebar{min-width:0;position:sticky;top:100px}.workspace-content{min-width:0}.workspace-divider{align-self:stretch;min-height:160px;cursor:col-resize;touch-action:none;display:flex;justify-content:center;outline-offset:-3px}.workspace-divider span{width:3px;border-radius:3px;background:var(--border,#35423f);margin:4px 0;transition:background .15s}.workspace-divider:hover span,.workspace-divider:focus-visible span,.is-resizing .workspace-divider span{background:var(--accent,#81d9bd)}.is-resizing{user-select:none}.workspace-sidebar :deep(.shot-navigator){position:static;max-height:calc(100vh - 132px)}
@media(max-width:900px){.resizable-workspace{grid-template-columns:minmax(0,1fr)!important;gap:16px!important}.workspace-sidebar{position:static}.workspace-divider{display:none}}
</style>
