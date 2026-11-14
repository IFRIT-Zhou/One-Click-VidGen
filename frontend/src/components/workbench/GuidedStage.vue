<script setup>
// Stage card / strip used inside the 配音 and 画面与字幕 workbenches.
// Same functions and conditions as the original guided-workflow shell in Studio.vue;
// the rarely used "取消本次分步任务" lives in a corner (GuidedCancel) instead of here.
import { computed, inject } from 'vue'
const props = defineProps({ compact: Boolean })
const ws = inject('ocv')
const ttsRunning = computed(() => ws.ttsEditor?.task?.status === 'running')
const guided = computed(() => ['video', 'dynamic'].includes(ws.studioKind) && ws.isGuidedWorkflowJob(ws.studioJob) && ws.studioJob?.status !== 'completed')
const dynamicAudioEdit = computed(() => !!ws.studioJob?.request?.dynamic_video && ws.studioTab === '配音')
const visible = computed(() => guided.value || dynamicAudioEdit.value)
defineExpose({ visible })
</script>

<template>
  <section v-if="visible" class="gs" :class="{ 'is-compact': compact }" aria-label="下一步">
    <div class="gs-text">
      <small v-if="guided">{{ ws.guidedStageEyebrow }}</small>
      <b>{{ guided ? ws.guidedStageTitle : '动态视频 · 配音编辑' }}</b>
      <p v-if="!compact && guided">{{ ws.guidedStageDescription }}</p>
      <p v-if="ws.guidedStageMessage" class="gs-msg" :class="{ 'is-error': ws.guidedStageError }">{{ ws.guidedStageMessage }}</p>
      <slot name="note" />
    </div>
    <div v-if="guided && ws.guidedStage === 'visual_setup' && !compact" class="gs-cost">
      <span>本阶段预估 <strong>约 {{ ws.guidedVisualEstimate.images }} 张图片</strong></span>
      <small>文本导演约 3–4 次请求；图片按实际成功张数计费。开始前请确认接口或号池余额。</small>
    </div>
    <div v-if="guided && ws.guidedRunning" class="gs-progress">
      <span><i :style="{ width: `${ws.activeJob?.progress || 0}%` }"></i></span>
      <small>{{ ws.activeJob?.message || '正在处理当前阶段' }} · {{ ws.activeJob?.progress || 0 }}%</small>
    </div>
    <div class="gs-actions">
      <template v-if="dynamicAudioEdit">
        <button v-if="ws.codexBridgeAvailable && ws.guidedStage === 'audio_review' && !ws.editingDynamicAudio" type="button" class="ghost-btn" :disabled="ws.studioBusy || ws.guidedSubtitleSaving || ttsRunning" @click="ws.handoffToCodex">确认配音，交给 AI 规划</button>
        <button v-if="!(guided && ws.guidedStage === 'audio_review')" class="primary-btn" type="button" :title="ws.canReturnDynamicStoryboard ? '保存并返回已有分镜' : '尚无分镜，请先确认配音并进入自动分镜'"
          :disabled="!ws.canReturnDynamicStoryboard || ws.studioBusy || ttsRunning || !ws.ttsEditor.available || !ws.ttsEditor.segments.length || !ws.studioAudio || ['queued', 'running'].includes(ws.studioJob?.status)"
          @click="ws.enterDynamicStoryboard">保存配音编辑，返回动态分镜 →</button>
      </template>
      <template v-if="guided">
        <button v-if="ws.guidedCanStop" class="ghost-btn" type="button" :disabled="ws.cancellingGeneration" @click="ws.cancelGeneration">{{ ws.cancellingGeneration ? '正在停止…' : '停止当前阶段' }}</button>
        <button v-if="ws.guidedCanResume" class="primary-btn" type="button" :disabled="ws.resumingGeneration" @click="ws.resumeGeneration">{{ ws.resumingGeneration ? '正在恢复…' : '继续当前阶段' }}</button>
        <button v-else-if="ws.guidedStage === 'audio_setup'" class="primary-btn" type="button" :disabled="!ws.canSubmitGeneration" @click="ws.submit">{{ ws.submitting ? '正在提交…' : '开始生成配音与字幕' }}</button>
        <template v-else-if="ws.guidedStage === 'audio_review' && ws.studioJob?.request?.dynamic_video">
          <button v-if="ws.codexBridgeAvailable && !dynamicAudioEdit" type="button" class="ghost-btn" :disabled="ws.studioBusy || ws.guidedSubtitleSaving || ttsRunning" @click="ws.handoffToCodex">确认配音，交给 AI 规划</button>
          <button class="primary-btn" type="button" :disabled="ws.studioBusy || ws.guidedSubtitleSaving || ttsRunning" @click="ws.enterDynamicStoryboard">确认配音与字幕，进入自动分镜 →</button>
        </template>
        <button v-else-if="ws.guidedStage === 'audio_review'" class="primary-btn" type="button" :disabled="ws.guidedAdvancing || ws.guidedSubtitleSaving || ttsRunning" @click="ws.advanceGuidedWorkflow('confirm_audio')">{{ ws.guidedAdvancing ? '正在确认…' : '确认配音与字幕 →' }}</button>
        <button v-else-if="ws.guidedStage === 'visual_setup'" class="primary-btn" type="button" :disabled="ws.guidedAdvancing" @click="ws.advanceGuidedWorkflow('start_visual')">{{ ws.guidedAdvancing ? '正在启动…' : '确认设置并开始作图 →' }}</button>
        <button v-else-if="ws.guidedStage === 'visual_review'" class="primary-btn" type="button" :disabled="ws.guidedAdvancing || ws.visualEditor.has_active_image_tasks || ttsRunning" @click="ws.advanceGuidedWorkflow('confirm_visual')">{{ ws.guidedAdvancing ? '正在确认…' : '确认画面与时序 →' }}</button>
        <button v-else-if="ws.guidedStage === 'render_setup'" class="primary-btn" type="button" :disabled="ws.guidedAdvancing || (ws.form.bgm_enabled && !ws.form.bgm_tracks.length)" @click="ws.advanceGuidedWorkflow('start_render')">{{ ws.guidedAdvancing ? '正在启动…' : '开始最终渲染 →' }}</button>
        <button v-else-if="ws.guidedStage === 'completed'" class="primary-btn" type="button" @click="ws.enterGuidedPostProduction">进入成片精修 →</button>
      </template>
    </div>
  </section>
</template>

<style scoped>
.gs{display:grid;gap:10px;padding:14px 16px;border-radius:12px;border:1px solid color-mix(in srgb,var(--ocv-accent) 35%,transparent);background:linear-gradient(160deg,color-mix(in srgb,var(--ocv-accent) 14%,var(--ocv-panel)),var(--ocv-panel) 70%);color:var(--ocv-text)}
.gs-text{display:grid;gap:4px;min-width:0}.gs-text small{color:var(--ocv-muted);font-size:11px;letter-spacing:.08em}.gs-text b{font-size:16px}
.gs-text p{margin:0;font-size:12px;line-height:1.7;color:var(--ocv-muted)}
.gs-msg{color:var(--ocv-warning)!important}.gs-msg.is-error{color:var(--ocv-danger)!important}
.gs-cost{display:grid;gap:2px;font-size:12px;padding:8px 10px;border-radius:8px;background:color-mix(in srgb,var(--ocv-text) 5%,transparent)}.gs-cost small{color:var(--ocv-muted)}
.gs-progress{display:grid;gap:4px}.gs-progress span{display:block;height:5px;border-radius:9px;background:color-mix(in srgb,var(--ocv-text) 12%,transparent);overflow:hidden}.gs-progress i{display:block;height:100%;background:var(--ocv-accent);transition:width .4s}.gs-progress small{font-size:11px;color:var(--ocv-muted)}
.gs-actions{display:grid;gap:8px}.gs-actions .primary-btn{min-height:42px;width:100%}.gs-actions .ghost-btn{width:100%}
.gs:has(.gs-actions:empty){display:none}
.gs.is-compact{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:10px 14px;background:color-mix(in srgb,color-mix(in srgb,var(--ocv-accent) 14%,var(--ocv-panel)) 92%,transparent);backdrop-filter:blur(10px)}
.gs.is-compact .gs-text{flex:1;display:flex;align-items:baseline;gap:4px 10px;flex-wrap:wrap}.gs.is-compact .gs-text b{font-size:13px}
.gs.is-compact .gs-progress{flex-basis:100%;order:5}
.gs.is-compact .gs-actions{display:flex;flex-wrap:wrap}.gs.is-compact .gs-actions .primary-btn,.gs.is-compact .gs-actions .ghost-btn{width:auto;min-height:38px;white-space:nowrap}
@container (max-width:620px){.gs.is-compact .gs-actions{flex:1}.gs.is-compact .gs-actions .primary-btn{flex:1}}
</style>
