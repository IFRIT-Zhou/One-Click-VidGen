<script setup>
import { ref, watch, nextTick } from 'vue'
const props = defineProps({ lines: { type: Array, default: () => [] }, compact: Boolean, diagnosticAvailable: Boolean, diagnosticExporting: Boolean })
defineEmits(['export-diagnostic'])
const follow = ref(true), wrap = ref(true), viewport = ref(null), copied = ref(false)
async function bottom(){await nextTick();if(viewport.value)viewport.value.scrollTop=viewport.value.scrollHeight}
watch(()=>props.lines,()=>{if(follow.value)bottom()},{deep:true,immediate:true})
watch(follow,v=>{if(v)bottom()})
function scroll(){const el=viewport.value;if(el && el.scrollHeight-el.clientHeight-el.scrollTop>35)follow.value=false}
async function copy(){try{await navigator.clipboard.writeText(props.lines.join('\n'));copied.value=true;setTimeout(()=>copied.value=false,2000)}catch{copied.value=false}}
</script>
<template>
 <div class="raw-console" :class="{compact}">
  <div class="console-tools"><span><i class="dot"/> 后台输出 <small>实时任务日志</small></span><div><label><input type="checkbox" v-model="wrap"/> 自动换行</label><label><input type="checkbox" v-model="follow"/> 自动滚动</label><button @click="copy">{{copied?'已复制':'复制日志'}}</button></div></div>
  <div ref="viewport" class="console-output" :class="{nowrap:!wrap}" @scroll="scroll" tabindex="0" aria-label="后台日志输出"><div v-for="(line,i) in lines" :key="i" class="console-line" :class="{error:/ERROR|失败|Traceback/.test(line),warning:/WARN|超时|重试/.test(line)}">{{line}}</div><div v-if="!lines.length" class="console-empty">暂无日志，任务启动后将在这里显示输出。</div></div>
  <div class="console-footer"><span>{{lines.length}} 行 · {{follow?'跟随最新输出':'已暂停自动滚动，可向上查看'}} </span><div class="console-footer-actions"><button @click="follow=true;bottom()">跳到最新 ↓</button><button :disabled="!diagnosticAvailable||diagnosticExporting" @click="$emit('export-diagnostic')">{{diagnosticExporting?'正在导出…':'导出诊断包'}}</button></div></div>
 </div>
</template>

