<template>
  <button class="announcement-trigger" type="button" @click="openDialog">
    <span aria-hidden="true">◷</span> 公告 <span v-if="banner" class="announcement-new">新</span>
  </button>
  <Teleport to="body">
    <aside v-if="banner && !dialogOpen" class="announcement-banner" aria-label="最新公告">
      <span class="announcement-banner-title">{{ banner.pinned ? '置顶公告' : '最新公告' }} · {{ banner.title }}</span>
      <button type="button" @click="openDialog">查看</button>
      <button type="button" aria-label="关闭此版本的公告提示" @click="dismissBanner">×</button>
    </aside>
    <dialog ref="dialog" class="announcement-dialog" aria-labelledby="announcement-heading" @close="dialogOpen = false" @click="closeFromBackdrop">
      <header class="announcement-header">
        <div><h2 id="announcement-heading">公告</h2><p>功能调整与服务更新</p></div>
        <button type="button" aria-label="关闭公告" @click="dialog.close()">×</button>
      </header>
      <div class="announcement-content" :aria-busy="loading">
        <p v-if="!enabled" class="announcement-state">登录后即可查看公告。</p>
        <div v-else-if="error" class="announcement-state" role="status">
          <p>{{ error }}</p><button type="button" :disabled="loading" @click="fetchPage(offset)">重试</button>
        </div>
        <p v-else-if="loading && !items.length" class="announcement-state">正在加载公告…</p>
        <p v-else-if="!items.length" class="announcement-state">暂无公告。</p>
        <article v-for="item in items" :key="item.id" class="announcement-item">
          <h3><span v-if="item.pinned" class="announcement-pin">置顶</span>{{ item.title }}</h3>
          <p class="announcement-date">{{ formatDate(item.updated_at || item.published_at) }}</p>
          <div class="announcement-body">{{ item.body }}</div>
        </article>
      </div>
      <footer class="announcement-footer">
        <span>{{ total }} 条公告<span v-if="loading"> · 正在更新…</span></span>
        <div>
          <button type="button" :disabled="loading || offset === 0 || !enabled" @click="fetchPage(Math.max(0, offset - pageSize))">上一页</button>
          <button type="button" :disabled="loading || offset + pageSize >= total || !enabled" @click="fetchPage(offset + pageSize)">下一页</button>
        </div>
      </footer>
    </dialog>
  </Teleport>
</template>

<script setup>
import { computed, nextTick, onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { requestJSON } from '../api'

const props = defineProps({ enabled: { type: Boolean, default: true } })
const pageSize = 20
const storageKey = 'ocv:announcements:dismissed:v1'
const dialog = ref(null)
const dialogOpen = ref(false)
const items = ref([])
const total = ref(0)
const offset = ref(0)
const latest = ref(null)
const dismissed = ref({})
const loading = ref(false)
const error = ref('')
let lastAttempt = 0
let controller = null
let disposed = false
const banner = computed(() => {
  const item = latest.value
  return props.enabled && item && dismissed.value[String(item.id)] !== String(item.revision) ? item : null
})

function readDismissed() {
  try {
    const value = JSON.parse(localStorage.getItem(storageKey) || '{}')
    dismissed.value = value && typeof value === 'object' && !Array.isArray(value) ? value : {}
  } catch { dismissed.value = {} }
}

function dismissBanner() {
  if (!banner.value) return
  const value = { ...dismissed.value, [String(banner.value.id)]: String(banner.value.revision) }
  dismissed.value = Object.fromEntries(Object.entries(value).slice(-200))
  try { localStorage.setItem(storageKey, JSON.stringify(dismissed.value)) } catch { /* Storage may be disabled. */ }
}

async function fetchPage(nextOffset = 0, force = false) {
  if (!props.enabled || disposed || (loading.value && !force)) return
  if (force) controller?.abort()
  loading.value = true
  error.value = ''
  lastAttempt = Date.now()
  controller = new AbortController()
  const current = controller
  const timeout = setTimeout(() => current.abort(), 18000)
  try {
    const page = await requestJSON(`/api/announcements?limit=${pageSize}&offset=${nextOffset}`, { signal: current.signal, cache: 'no-store' })
    if (disposed || current !== controller || current.signal.aborted || !props.enabled) return
    if (!Array.isArray(page.items) || !Number.isInteger(page.total) || page.total < 0) throw new Error('Invalid announcement page')
    items.value = page.items
    total.value = page.total
    offset.value = nextOffset
    if (nextOffset === 0) latest.value = page.items[0] || null
  } catch {
    if (!disposed && current === controller && props.enabled) error.value = '公告暂时无法加载，请稍后重试。其他功能不受影响。'
  } finally {
    clearTimeout(timeout)
    if (controller === current) { loading.value = false; controller = null }
  }
}

async function openDialog() {
  dialogOpen.value = true
  await nextTick()
  dialog.value?.showModal()
  // An explicit click always reads the current Cloud API feed, even during a background refresh.
  void fetchPage(0, true)
}

function closeFromBackdrop(event) {
  if (event.target !== dialog.value) return
  const box = dialog.value.getBoundingClientRect()
  if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) dialog.value.close()
}

function formatDate(value) {
  if (!value) return ''
  const date = new Date(value)
  return Number.isNaN(date.getTime()) ? '' : date.toLocaleString('zh-CN')
}

function refreshOnFocus() {
  readDismissed()
  if (Date.now() - lastAttempt >= 60000 && !(dialogOpen.value && offset.value > 0)) void fetchPage(0)
}

watch(() => props.enabled, (enabled) => {
  if (enabled) { lastAttempt = 0; void fetchPage(0) }
  else {
    controller?.abort()
    controller = null
    loading.value = false
    items.value = []; latest.value = null; total.value = 0; offset.value = 0; error.value = ''
  }
})
onMounted(() => { readDismissed(); void fetchPage(0); window.addEventListener('focus', refreshOnFocus) })
onBeforeUnmount(() => { disposed = true; controller?.abort(); window.removeEventListener('focus', refreshOnFocus) })
</script>

<style scoped>
.announcement-trigger { display: flex; align-items: center; gap: .6rem; width: 100%; padding: .75rem 1rem; color: inherit; background: transparent; border: 1px solid #6c838755; border-radius: 10px; text-align: left; cursor: pointer; }
.announcement-new, .announcement-pin { color: #087561; background: #d8f5ec; border-radius: 5px; padding: .15rem .4rem; font-size: .75rem; }
.announcement-banner { position: fixed; z-index: 990; bottom: 18px; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: 12px; max-width: min(720px, calc(100vw - 32px)); padding: 12px 16px; border: 1px solid #8bdac7; border-radius: 12px; background: #18352f; color: #effff9; box-shadow: 0 6px 24px #0003; }
.announcement-banner-title { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.announcement-banner button { flex-shrink: 0; color: #d0fff0; background: transparent; border: 0; padding: 4px; cursor: pointer; }
.announcement-dialog { width: min(720px, calc(100vw - 32px)); max-height: 85vh; padding: 0; border: 1px solid #a7c9c0; border-radius: 16px; background: #f6fbf9; color: #193c34; box-shadow: 0 16px 80px #0005; }
.announcement-dialog::backdrop { background: #07181399; }
.announcement-header, .announcement-footer { display: flex; justify-content: space-between; align-items: center; gap: 16px; padding: 18px 24px; }
.announcement-header { border-bottom: 1px solid #d7e6df; }
.announcement-header h2, .announcement-header p { margin: 0; }
.announcement-header p { margin-top: 5px; font-size: .85rem; color: #56736a; }
.announcement-dialog button { padding: 8px 12px; background: #e5f3ee; color: #214e40; border: 1px solid #c5dcd2; border-radius: 8px; cursor: pointer; }
.announcement-dialog button:disabled { opacity: .45; cursor: default; }
.announcement-content { max-height: 60vh; overflow: auto; padding: 0 24px; }
.announcement-item { padding: 24px 0; border-bottom: 1px solid #d7e6df; }
.announcement-item:last-child { border-bottom: 0; }
.announcement-item h3 { margin: 0; font-size: 1.1rem; overflow-wrap: anywhere; }
.announcement-pin { margin-right: 8px; vertical-align: middle; }
.announcement-date { color: #56736a; font-size: .8rem; margin: 8px 0 16px; }
.announcement-body { white-space: pre-wrap; overflow-wrap: anywhere; line-height: 1.8; }
.announcement-state { padding: 24px 0; color: #56736a; }
.announcement-footer { border-top: 1px solid #d7e6df; font-size: .85rem; }
.announcement-footer > div { display: flex; gap: 8px; }
@media (max-width: 560px) { .announcement-header, .announcement-footer { padding: 16px; } .announcement-content { padding: 0 16px; } .announcement-banner { width: calc(100vw - 32px); box-sizing: border-box; } }
</style>
