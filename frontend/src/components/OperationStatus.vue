<script setup>
import {computed,ref,onMounted,onUnmounted} from 'vue'
const props=defineProps({task:Object,title:{type:String,default:'任务状态'},pending:Boolean,summary:String})
const now=ref(Date.now());let timer
onMounted(()=>{timer=setInterval(()=>now.value=Date.now(),1000)})
onUnmounted(()=>clearInterval(timer))
const active=computed(()=>props.pending||['running','queued','pending','stopping'].includes(props.task?.status))
const elapsed=computed(()=>{const raw=props.task?.started_at;if(!raw)return '';const start=typeof raw==='number'?raw*1000:Date.parse(raw);const end=active.value?now.value:(props.task?.finished_at?Number(props.task.finished_at)*1000:NaN);if(!Number.isFinite(start)||!Number.isFinite(end))return '';const seconds=Math.max(0,Math.floor((end-start)/1000));return `${Math.floor(seconds/60)}:${String(seconds%60).padStart(2,'0')}`})
const label=computed(()=>props.pending?'正在提交，请勿重复点击':props.task?.message||({running:'正在处理',queued:'已加入队列',completed:'已完成',failed:'处理失败',unknown:'状态待核实，请先查询原任务',stopped:'已停止'}[props.task?.status])||'等待操作')
</script>
<template><section v-if="pending||task?.status||summary" class="operation-status" :class="{active,failed:['failed','unknown'].includes(task?.status)}" role="status" aria-live="polite"><div class="status-main"><strong><span v-if="active" class="status-dot"/>{{title}}</strong><span v-if="elapsed" class="elapsed">已用时 {{elapsed}}</span><span v-if="summary" class="summary">{{summary}}</span><slot/></div><p>{{label}}</p><details v-if="task?.error"><summary>查看错误原因</summary><pre>{{task.error}}</pre><p>请先检查配置或查询原任务，确认失败后再重试，避免重复提交。</p></details></section></template>
<style scoped>
.operation-status{padding:12px 15px;margin:12px 0;border:1px solid var(--border,#35423f);border-radius:10px;background:var(--panel,#1e2625);font-size:13px;min-width:0}.status-main{display:flex;align-items:center;gap:12px;flex-wrap:wrap}.status-main strong{display:flex;align-items:center;gap:8px}.elapsed,.summary{color:var(--muted,#aab8b3)}p{margin:7px 0 0;line-height:1.6;overflow-wrap:anywhere}.active{border-color:var(--accent,#81d9bd)}.failed{border-color:#c99c4c}.status-dot{width:8px;height:8px;background:var(--accent,#81d9bd);border-radius:50%}details{margin-top:8px}summary{cursor:pointer}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit;max-height:180px;overflow:auto}
</style>
