<script setup>
// Three explicit states for a stored secret:
//  keep    — saved key stays; nothing is sent (default when a key exists)
//  replace — a new key is typed; empty input still keeps the saved key
//  delete  — a separate action emitted to the parent, never mixed with saving
import { computed, ref, watch } from 'vue'

const props = defineProps({
  modelValue: { type: String, default: '' },
  label: { type: String, default: 'API Key' },
  saved: Boolean,
  hints: { type: Array, default: () => [] },
  required: Boolean,
  optionalNote: { type: String, default: '' },
  placeholder: { type: String, default: '填写服务商提供的 API Key' },
  replaceNote: { type: String, default: '' },
  disabled: Boolean,
  deletable: Boolean,
  deleting: Boolean,
  inputName: { type: String, default: 'ocv-service-secret' },
})
const emit = defineEmits(['update:modelValue', 'delete'])
const replacing = ref(!props.saved)
watch(() => props.saved, (value) => { if (!value) replacing.value = true })
const fieldId = `secret-${Math.random().toString(36).slice(2, 9)}`
const value = computed({ get: () => props.modelValue, set: (v) => emit('update:modelValue', v) })
function startReplace() { replacing.value = true }
function cancelReplace() { emit('update:modelValue', ''); replacing.value = false }
// Keep browsers and password managers from auto-filling a stored login here.
function unlock(event) { event?.currentTarget?.removeAttribute('readonly') }
</script>

<template>
  <div class="svc-field svc-secret">
    <div class="svc-label-row">
      <label :for="replacing ? fieldId : undefined" class="svc-label">{{ label }}</label>
      <span v-if="required && (!saved || replacing)" class="svc-req">必填</span>
      <span v-else-if="optionalNote" class="svc-opt">{{ optionalNote }}</span>
    </div>
    <div v-if="saved" class="svc-secret-saved">
      <span class="svc-secret-state">
        <span v-if="hints.length">已保存 <code v-for="(hint, index) in hints" :key="hint + index">{{ hint }}</code></span>
        <span v-else>已保存</span>
        <small>{{ replacing ? (replaceNote || '填写后替换已保存的 Key；留空仍保留原 Key。') : '保留原 Key，不会重新发送。' }}</small>
      </span>
      <button v-if="!replacing" type="button" :disabled="disabled" @click="startReplace">替换</button>
      <button v-else type="button" :disabled="disabled" @click="cancelReplace">撤销替换</button>
      <template v-if="deletable">
        <button v-for="(hint, index) in (hints.length ? hints : [''])" :key="'del' + index" type="button" class="svc-danger-text"
          :disabled="disabled || deleting" :aria-label="`删除本机保存的 ${label}${hint ? ' ' + hint : ''}`" @click="emit('delete', index)">
          {{ deleting ? '删除中…' : (hints.length > 1 ? `删除 ${hint}` : '删除已保存 Key') }}
        </button>
      </template>
    </div>
    <input v-if="replacing" :id="fieldId" v-model="value" :name="inputName" type="password" autocomplete="new-password"
      autocapitalize="off" spellcheck="false" data-lpignore="true" data-1p-ignore readonly :disabled="disabled"
      :placeholder="saved ? '填写新的 Key' : placeholder" :aria-required="required && !saved" @focus="unlock" />
  </div>
</template>
