<script setup>
import TtsPendingRecovery from './TtsPendingRecovery.vue'
defineProps({ projectId: String, editor: { type: Object, required: true } })
defineEmits(['adopted'])
</script>
<template>
  <div v-if="editor.task?.message" class="visual-task-message" :class="editor.task?.status">{{ editor.task.message }}</div>
  <div v-if="editor.task?.poll_error" class="visual-task-message failed" role="status">{{ editor.task.poll_error }}</div>
  <TtsPendingRecovery :project-id="projectId" :task-status="editor.task?.status" @adopted="$emit('adopted')" />
</template>
