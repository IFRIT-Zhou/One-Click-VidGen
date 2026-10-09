<script setup>
import { computed, onMounted, ref } from 'vue'
import { requestJSON } from '../api'
defineProps({ compact: Boolean })
const emit = defineEmits(['open-draft', 'open-project'])
const info = ref(null), drafts = ref([]), projects = ref([]), message = ref(''), busy = ref(false)
const selected = ref('')
const guide = ref(''), guideOpen = ref(false)
const activeDrafts = computed(() => drafts.value.filter(d => !d.archived))
const archivedDrafts = computed(() => drafts.value.filter(d => d.archived))
const canArchive = computed(() => info.value?.capabilities?.includes('draft_archive'))
async function setArchived(draft, archived) {
  busy.value = true; message.value = ''
  try {
    await requestJSON('/api/codex-bridge/drafts/' + draft.id + '/archive', {
      method: 'PUT', body: JSON.stringify({ revision: draft.revision, archived }),
      headers: { 'Content-Type': 'application/json' },
    })
    await refresh()
    message.value = archived ? '已归档，草稿、任务和素材均保留，可在历史归档中恢复。' : '已恢复到当前草稿。'
  } catch (e) { message.value = e.message }
  finally { busy.value = false }
}
async function showGuide() {
  guideOpen.value = !guideOpen.value
  if (!guideOpen.value || guide.value) return
  try { guide.value = (await requestJSON('/api/codex-bridge/guide')).content }
  catch (e) { message.value = e.message; guideOpen.value = false }
}
async function refresh() {
  busy.value = true; message.value = ''
  try {
    info.value = await requestJSON('/api/codex-bridge/info')
    const [d, p] = await Promise.all([requestJSON('/api/codex-bridge/drafts?include_archived=true'), requestJSON('/api/codex-bridge/projects')])
    drafts.value = d.items; projects.value = p.items
  } catch (e) { if (e.status === 404) info.value = null; else message.value = e.message }
  finally { busy.value = false }
}
async function copyInstructions() {
  const project = projects.value.find(p => p.id === selected.value)
  const text = `请通过 OCV 的 Codex 制作桥协助视频创作。\n先完整读取随 OCV 附带的通用 Skill：${info.value.skill_path || 'plugins/codex_bridge/skills/ocv-production-bridge/SKILL.md'}\n固定客户端：${info.value.client_path}\n当前 OCV 页面地址：${window.location.origin}（可能是前端端口，不要直接当作后端地址；按 Skill 验证连接。）\n先调用 info 和 schema；不要遍历 OCV 源码或编写一次性项目写回脚本。\n${project ? `目标：${project.name}；动态项目 ID：${project.id}。先读取 pack；配音是否已核查由我确认，不根据项目存在自行推断。` : '新项目请根据我的稿件、素材和创作要求填写服务器草稿，供我在 OCV 打开并制作配音。'}\n创作领域不预设；资料缺失或身份映射不明确时先问我。只填写草稿或校验、导入方案，不启动配音、图片、视频生成或付费调用。`
  try { await navigator.clipboard.writeText(text); message.value = '已复制，可粘贴到新的 Codex 对话。' }
  catch { message.value = text }
}
async function openDraft(draft) {
  busy.value = true
  try { emit('open-draft', await requestJSON('/api/codex-bridge/drafts/' + draft.id)) }
  catch (e) { message.value = e.message }
  finally { busy.value = false }
}
onMounted(refresh)
</script>
<template>
  <section v-if="info" class="codex-bridge" aria-label="Codex 制作桥">
    <header><div><strong>Codex 制作桥</strong><p>Codex 准备草稿和分镜，你核查配音与画面。不会自动生成或扣费。</p></div><button type="button" :disabled="busy" @click="refresh">刷新</button></header>
    <div v-if="!compact" class="bridge-steps"><span>① Codex 填入初始草稿</span><span>② 在 OCV 制作并确认配音</span><span>③ Codex 导入分镜方案</span><span>④ 审核后生成画面</span></div>
    <div class="bridge-tools"><select v-model="selected" aria-label="复制给 Codex 的目标项目"><option value="">新项目 · 填写初始草稿</option><option v-for="p in projects" :key="p.id" :value="p.id">{{p.name}} · {{p.shot_count}} 镜</option></select><button type="button" @click="copyInstructions">复制给 Codex 的说明</button><button type="button" :aria-expanded="guideOpen" @click="showGuide">使用指南</button><button v-if="selected" type="button" @click="emit('open-project',selected)">打开分镜</button></div>
    <div v-if="guideOpen" class="bridge-guide"><p>这份通用 Skill 随 OCV 更新，无须先安装到 Codex。复制上方说明，让新对话按路径读取即可。</p><code>{{info.skill_path}}</code><pre>{{guide || '正在读取指南…'}}</pre></div>
    <div v-if="activeDrafts.length" class="bridge-drafts"><div v-for="draft in activeDrafts" :key="draft.id"><span><b>{{draft.name}}</b><small>服务器参数草稿 · v{{draft.revision}}</small></span><span class="draft-actions"><button type="button" :disabled="busy" @click="openDraft(draft)">打开并核查</button><button v-if="canArchive" type="button" :disabled="busy" @click="setArchived(draft,true)">归档</button></span></div></div>
    <p v-else>Codex 保存的初始草稿会出现在这里。配音确认后，让 Codex 通过任务 ID 接手，无须先运行 OCV 的自动分镜规划。</p>
    <details v-if="archivedDrafts.length" class="bridge-archive"><summary>历史归档（{{archivedDrafts.length}}）</summary><p>仅收起服务器草稿，不删除任务、成片或素材。</p><div class="bridge-drafts"><div v-for="draft in archivedDrafts" :key="draft.id"><span><b>{{draft.name}}</b><small>已归档 · v{{draft.revision}}</small></span><span class="draft-actions"><button type="button" :disabled="busy" @click="openDraft(draft)">打开并核查</button><button type="button" :disabled="busy" @click="setArchived(draft,false)">恢复</button></span></div></div></details>
    <details v-if="!compact"><summary>接口与隐私说明</summary><p>当前版本用于动态视频的初始参数与分镜草案交接，不是自动出片工具。仅访问当前登录用户的数据；导入前校验、备份并检查版本，已有素材不会被整体覆盖。插件不捆绑私人创作规则或项目资料，也不自行调用外部模型；交给 Codex 的资料仍按你所用 Codex 服务的数据规则处理。</p><code>{{info.client_path}}</code></details>
    <p v-if="message" class="bridge-message" role="status">{{message}}</p>
  </section>
</template>
<style scoped>
.draft-actions{display:flex;gap:8px;flex-wrap:wrap}.bridge-archive .bridge-drafts{margin-top:12px}
.bridge-guide{min-width:0}.bridge-guide pre{max-height:440px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px;line-height:1.65;border:1px solid var(--border,#35423f);border-radius:8px;padding:14px}
.codex-bridge{border:1px solid var(--border,#35423f);border-radius:14px;background:var(--surface,#1d2624);padding:20px;margin:18px 0;display:grid;gap:16px}.codex-bridge header{display:flex;justify-content:space-between;align-items:flex-start;gap:16px}.codex-bridge p{color:var(--muted,#9daba7);font-size:13px;margin:6px 0 0;line-height:1.6}.bridge-tools{display:flex;gap:10px;flex-wrap:wrap;align-items:center}.bridge-tools select{flex:1;min-width:200px;max-width:100%}.bridge-steps{display:flex;gap:12px;flex-wrap:wrap;font-size:13px;color:var(--muted,#9daba7)}.bridge-drafts{display:grid;gap:8px}.bridge-drafts>div{display:flex;align-items:center;justify-content:space-between;gap:14px;border-top:1px solid var(--border,#35423f);padding-top:12px}.bridge-drafts small{display:block;color:var(--muted,#9daba7);margin-top:5px}.bridge-message{white-space:pre-wrap;overflow-wrap:anywhere}code{display:block;overflow-wrap:anywhere;margin-top:10px;font-size:12px}summary{cursor:pointer;font-size:13px}button{white-space:nowrap}@media(max-width:650px){.bridge-tools>*{width:100%}.bridge-drafts>div{align-items:flex-start;flex-direction:column}}
.codex-bridge button{padding:9px 13px;border:1px solid var(--border,#43534e);border-radius:8px;background:var(--surface-raised,#28332f);color:var(--text,#e8f0ed);cursor:pointer}.codex-bridge button:hover{border-color:var(--accent,#81d9bd)}.codex-bridge button:disabled{opacity:.5;cursor:wait}.codex-bridge button:focus-visible,.codex-bridge select:focus-visible{outline:2px solid var(--accent,#81d9bd);outline-offset:2px}
</style>
