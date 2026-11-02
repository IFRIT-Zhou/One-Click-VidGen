<script>
let nextHint = 0
</script>
<script setup>
import { ref, onMounted, onUpdated } from 'vue'
const props = defineProps({ active: Boolean })
const root = ref(null), dismissed = ref(false)
const id = `ican-channel-hint-${++nextHint}`
const message = 'ICAN 号池不稳定，如无法使用，可优先尝试稳定版。'
function describe() {
  const select = root.value?.querySelector('select')
  if (!select) return
  if (props.active) select.setAttribute('aria-describedby', id)
  else select.removeAttribute('aria-describedby')
  const option = select.querySelector('option[value="ican"]')
  if (option) option.title = message
}
onMounted(describe)
onUpdated(describe)
</script>
<template>
  <span ref="root" class="ican-channel-hint" :class="{'is-active': active, 'is-dismissed': dismissed}"
    @mouseenter="dismissed=false" @focusin="dismissed=false" @change="dismissed=false" @keydown.esc="dismissed=true">
    <slot />
    <span :id="id" role="tooltip" class="ican-channel-tooltip">{{message}}</span>
  </span>
</template>
<style scoped>
.ican-channel-hint{position:relative;display:grid;min-width:0;width:100%}
.ican-channel-tooltip{display:none;position:absolute;top:calc(100% + 7px);left:0;z-index:100;width:max-content;max-width:min(320px,75vw);padding:10px 12px;border-radius:8px;background:#183d43;color:#fff;font-size:13px;font-weight:400;line-height:1.6;white-space:normal;box-shadow:0 4px 16px #0003;pointer-events:none}
.ican-channel-hint.is-active:not(.is-dismissed):is(:hover,:focus-within)>.ican-channel-tooltip{display:block}
</style>
