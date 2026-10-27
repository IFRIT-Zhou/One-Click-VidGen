<script setup>
import TtsPendingRecovery from './TtsPendingRecovery.vue'
import {ref} from 'vue'
import {requestJSON} from '../api'
const props = defineProps({ projectId: String, editor: { type: Object, required: true } })
const emit = defineEmits(['adopted'])
const stopping = ref(false), stopError = ref('')
async function stopEdit() {
  if (stopping.value || !props.editor.task?.task_id) return
  stopping.value = true; stopError.value = ''
  try {
    const result = await requestJSON(`/api/jobs/${props.projectId}/tts-editor/cancel`, {
      method:'POST', headers:{'Content-Type':'application/json'},
      body:JSON.stringify({task_id:props.editor.task.task_id})
    })
    if (!result.accepted && result.reason) stopError.value = result.reason
    emit('adopted')
  } catch (error) { stopError.value = error.message }
  finally { stopping.value = false }
}
</script>
<template>
  <div v-if="editor.task?.message" class="visual-task-message" :class="editor.task?.status">{{ editor.task.message }}</div>
  <div v-if="editor.task?.status === 'running' && editor.task?.task_id" class="tts-edit-control">
    <span>正在制作候选配音 · 正式版本 {{ editor.task.adopted_version ?? editor.revision ?? 0 }} 保留</span>
    <button type="button" :disabled="stopping || editor.task.cancel_requested || editor.task.phase === 'committing'" @click="stopEdit">
      {{ editor.task.cancel_requested ? '正在安全停止…' : '停止本次配音编辑' }}
    </button>
  </div>
  <div v-if="stopError" class="visual-task-message failed" role="status">{{ stopError }}</div>
  <div v-if="editor.task?.upstream_may_still_run" class="visual-task-message failed" role="status">
    本地编辑已停止，原配音未替换；云端尚未确认结束，仍可能运行或计费，请核对云端任务状态。
  </div>
  <div v-if="editor.task?.poll_error" class="visual-task-message failed" role="status">{{ editor.task.poll_error }}</div>
  <TtsPendingRecovery :project-id="projectId" :task-status="editor.task?.status" @adopted="$emit('adopted')" />
</template>
<style scoped>
.tts-edit-control{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap;margin:8px 0;color:var(--muted);font-size:13px}
</style>
