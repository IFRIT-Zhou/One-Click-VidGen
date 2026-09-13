<script setup>
import { ref } from 'vue'
defineProps({ form: Object, assets: Array, names: Array, uploading: Boolean, error: String, autoAnalyze: Boolean })
defineEmits(['upload', 'remove'])
const preview = ref(null)
const url = (id) => `/api/editor/uploads/${encodeURIComponent(id)}`
</script>
<template>
  <section class="reference-material-panel">
    <header><div><small>按镜头使用 · 两种导演模式通用</small><h3>参考素材 <em>{{ form.reference_image_ids.length }}/6</em></h3></div>
      <label class="script-file-picker"><input type="file" multiple accept=".jpg,.jpeg,.png,.webp" :disabled="uploading || form.reference_image_ids.length >= 6" @change="$emit('upload', $event)"/><span>{{ uploading ? (autoAnalyze ? '上传并分析中…' : '上传中…') : '＋ 添加参考图' }}</span></label>
    </header>
    <p class="muted">人物、产品、界面或场景均可。{{ autoAnalyze ? '上传后会自动识别用途，你可以直接修改' : '请填写素材用途；需要自动识别时可在“插件与设置”中开启' }}；每个镜头只使用相关素材。</p>
    <article v-for="(id,index) in form.reference_image_ids" :key="id" class="reference-material-row">
      <button type="button" class="reference-material-thumb" @click="preview={url:url(id),name:form.reference_image_labels[id]||`图${index+1}`}"><img :src="url(id)" :alt="names[index]||'参考图'"/><b>{{ form.reference_image_labels[id] || `图${index+1}` }}</b></button>
      <label><span>{{ names[index] || assets.find(a=>a.id===id)?.name || '参考素材' }}</span><textarea v-model="form.reference_image_notes[id]" rows="2" maxlength="500" placeholder="例如：这是女主角；这是配音编辑界面，仅介绍该功能时使用。"/><small>用途说明 · 可选{{ autoAnalyze&&uploading&&!form.reference_image_notes[id] ? ' · 正在分析，填写内容不会被覆盖' : '' }}</small></label>
      <button type="button" class="reference-material-remove" :aria-label="`移除${form.reference_image_labels[id]||`图${index+1}`}`" @click="$emit('remove',index)">×</button>
    </article>
    <p v-if="error" class="board-error">{{ error }}</p>
    <small class="muted">用途说明不改变剧情，也不会要求每张画面都出现该素材。{{ autoAnalyze ? '自动分析使用当前语言接口，并可能产生少量 API 消耗。' : '当前未启用自动识图，不会为上传素材调用语言模型。' }}</small>
    <div v-if="preview" class="visual-preview-modal" role="dialog" aria-modal="true" @click.self="preview=null"><div class="visual-preview-content"><div class="visual-preview-head"><strong>{{ preview.name }}</strong><button type="button" @click="preview=null">关闭</button></div><img :src="preview.url" :alt="preview.name"/></div></div>
  </section>
</template>
<style scoped>
.reference-material-panel{padding:18px;border:1px solid var(--border,#ffffff20);border-radius:14px;display:grid;gap:14px;min-width:0}.reference-material-panel header{display:flex;align-items:center;justify-content:space-between;gap:12px;flex-wrap:wrap}.reference-material-panel h3{margin:4px 0}.reference-material-panel em{font-size:12px;font-style:normal;color:var(--muted);margin-left:8px}.reference-material-panel p{margin:0;font-size:12px}.reference-material-row{display:grid;grid-template-columns:84px minmax(0,1fr) 28px;gap:12px;align-items:start;padding-top:14px;border-top:1px solid #ffffff12}.reference-material-thumb{padding:0;position:relative;height:76px;overflow:hidden;border-radius:8px}.reference-material-thumb img{width:100%;height:100%;object-fit:contain}.reference-material-thumb b{position:absolute;bottom:0;left:0;padding:2px 7px;background:#101819dd;color:white;font-size:11px}.reference-material-row label{display:grid;gap:6px;min-width:0}.reference-material-row label>span{font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.reference-material-row textarea{width:100%;min-height:62px;padding:9px;font-size:12px;resize:vertical}.reference-material-row small{font-size:11px;color:var(--muted)}.reference-material-remove{padding:0;width:28px;height:28px}.reference-material-panel>small{line-height:1.6;font-size:11px}
.reference-material-thumb{border:1px solid var(--border,#ffffff20);background:var(--surface,#18211f);color:var(--text,#edf3f0);cursor:pointer}.reference-material-remove{display:grid;place-items:center;border:1px solid var(--border,#ffffff20);border-radius:7px;background:var(--surface,#18211f);color:var(--text,#edf3f0);font-size:18px;line-height:1;cursor:pointer}.reference-material-remove:hover{color:#ffaaaa;border-color:#ffaaaa80;background:#ffaaaa12}
</style>
