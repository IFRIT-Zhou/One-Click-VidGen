<script setup>
import { computed, ref, watch, onMounted } from 'vue'
import { api } from '../api'
import { ICAN_SIZES } from '../icanImageSizes'
import { imageRouteSelection, imageRouteKey, imageRouteLabel } from '../projectImageRoute'

const props = defineProps({ project: { type: Object, required: true }, disabled: Boolean, pending: Object, saveRoute: { type: Function, required: true } })
const profiles = ref([]), draft = ref(imageRouteSelection(props.project.creation_parameters)), saving = ref(false), message = ref('')
const persisted = computed(() => imageRouteSelection(props.project.creation_parameters))
const pending = computed(() => props.pending?.projectId === props.project.id ? props.pending : null)
const changed = computed(() => imageRouteKey(draft.value) !== imageRouteKey(persisted.value) || Boolean(pending.value))
const selectedProfile = computed(() => profiles.value.find(p => p.id === draft.value.image_profile_id))
const currentProfile = computed(() => props.project.creation_parameters?.image_profile_snapshot || profiles.value.find(p => p.id === persisted.value.image_profile_id))
const currentLabel = computed(() => imageRouteLabel(persisted.value, currentProfile.value))
const nextLabel = computed(() => imageRouteLabel(draft.value, selectedProfile.value))
const sizes = computed(() => ICAN_SIZES[props.project.settings?.ratio || '16:9'] || ICAN_SIZES['16:9'])
const resolutions = computed(() => draft.value.channel === 'personal' ? selectedProfile.value?.resolutions || ['1k','2k','4k'] : ['1k','2k','4k'])
watch(() => [props.project.id, imageRouteKey(persisted.value)], () => {
  draft.value = { ...persisted.value }; message.value = ''
  if (pending.value) draft.value = { ...pending.value.selection }
})
watch(pending, value => { if (value) { draft.value = { ...value.selection }; message.value = '' } }, { immediate: true })
function chooseChannel() {
  message.value = ''
  if (draft.value.channel === 'personal' && !selectedProfile.value) draft.value.image_profile_id = profiles.value.find(p => p.configured)?.id || ''
  if (draft.value.channel === 'ican' && !Object.values(sizes.value).includes(draft.value.size)) draft.value.size = Object.values(sizes.value)[0]
  chooseProfile()
}
function chooseProfile() {
  if (!resolutions.value.includes(draft.value.image_resolution)) draft.value.image_resolution = resolutions.value[0] || '2k'
}
async function ensureSaved(force = false) {
  if (!force && !changed.value) return
  if (saving.value) throw new Error('图像渠道正在保存，请稍后重试')
  saving.value = true; message.value = ''
  try { await props.saveRoute({ ...draft.value }); message.value = '已保存；下次出图使用此配置，已有图片不变。' }
  catch (e) { message.value = e.message; throw e }
  finally { saving.value = false }
}
async function apply() { try { await ensureSaved(true) } catch {} }
async function loadProfiles() { try { profiles.value = (await api.imageProfiles()).profiles || [] } catch (e) { message.value = e.message } }
onMounted(loadProfiles)
defineExpose({ ensureSaved })
</script>

<template>
 <section class="project-image-route">
  <header><strong>本项目图像模型</strong><span>当前：{{currentLabel}}</span></header>
  <div class="route-fields">
   <label>出图渠道<select v-model="draft.channel" :disabled="disabled||saving" @change="chooseChannel"><option value="ican">ICAN 号池 · GPT Image 2.5</option><option value="running">稳定版号池</option><option value="personal">个人 API</option></select></label>
   <label v-if="draft.channel==='personal'">模型配置<select v-model="draft.image_profile_id" :disabled="disabled||saving" @change="chooseProfile"><option value="" disabled>请选择已配置的接口</option><option v-for="profile in profiles" :key="profile.id" :value="profile.id" :disabled="!profile.configured">{{profile.name}} · {{profile.model_id}}{{profile.configured?'':'（缺少 Key）'}}</option></select></label>
   <label v-if="draft.channel==='ican'">出图尺寸<select v-model="draft.size" :disabled="disabled||saving"><option v-if="!Object.values(sizes).includes(draft.size)" :value="draft.size">{{draft.size}}（已保存）</option><option v-for="(pixels,quality) in sizes" :key="pixels" :value="pixels">{{quality}} · {{pixels.replace('x','×')}}</option></select></label>
   <label v-else-if="draft.channel==='personal'&&selectedProfile?.protocol==='openai_sync'">出图尺寸<input :value="selectedProfile.size||'auto'" disabled /></label>
   <label v-else>图片清晰度<select v-model="draft.image_resolution" :disabled="disabled||saving"><option v-for="value in resolutions" :key="value" :value="value">{{value.toUpperCase()}}</option></select></label>
   <button type="button" :disabled="disabled||saving||(draft.channel==='personal'&&!draft.image_profile_id)" @click="apply">{{saving?'保存中…':'保存出图配置'}}</button>
  </div>
  <p v-if="changed" class="pending" role="status">待应用：{{nextLabel}}。保存或下次点击生成／重试时生效。</p>
  <p v-else-if="message" role="status">{{message}}</p>
  <p v-else>仅影响本项目后续出图；不会切换语言模型，也不会重做已有图片。</p>
  <p v-if="changed&&message" class="pending" role="alert">{{message}}</p>
  <p v-if="disabled">有任务执行时请先停止并等待退出，再切换渠道。</p>
  <small v-if="project.last_image_route">最近一轮实际使用：{{project.last_image_route}}</small>
 </section>
</template>

<style scoped>
.project-image-route{padding:18px;margin:16px 0;border:1px solid var(--ocv-border,#35433e);border-radius:14px;background:var(--ocv-panel,#202825)}
.project-image-route button{padding:10px 14px;border:1px solid var(--ocv-border,#35433e);border-radius:9px;background:var(--ocv-panel-strong,#293630);color:var(--ocv-text,#e5eef0);font:inherit;cursor:pointer}.project-image-route button:disabled{opacity:.5;cursor:not-allowed}
header{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:12px}header span,p,small{color:var(--ocv-muted,#9aa6a1);font-size:13px;overflow-wrap:anywhere}.route-fields{display:flex;align-items:end;gap:12px;flex-wrap:wrap}.route-fields label{display:grid;gap:6px;flex:1 1 190px;min-width:0;font-size:13px}.route-fields select,.route-fields input{width:100%;min-width:0;box-sizing:border-box}.route-fields button{min-height:42px;white-space:nowrap}p{margin:10px 0 0}.pending{color:var(--ocv-accent,#7ddac5)}small{display:block;margin-top:8px}@media(max-width:640px){.route-fields{display:grid;grid-template-columns:minmax(0,1fr)}.route-fields button{width:100%}}
</style>
