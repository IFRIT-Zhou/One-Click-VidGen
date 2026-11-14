// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Zhou Ruoyu and He Yun
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { draftChanged, imageConcurrencyRules, imageProfileRules, languageAccountRules, languagePresetRules, missingFields, parseExtraParameters, snapshotDraft, urlOrigin, vibeRules, videoRules } from '../src/serviceDraft.js'

// Draft comparison
const draft = { name: 'A', model: 'm' }
const base = snapshotDraft(draft)
assert.equal(draftChanged(draft, base), false)
assert.equal(draftChanged({ ...draft, model: 'n' }, base), true)
assert.equal(draftChanged(draft, null), false, 'closed editor is never dirty')

// Language accounts
const officialNew = { value: 'a_official', source: 'official', configured: false }
assert.deepEqual(missingFields({ provider: 'a_official', model: 'x', api_key: '' }, languageAccountRules(officialNew)), ['API Key'])
assert.deepEqual(missingFields({ provider: 'a_official', model: 'x', api_key: '' }, languageAccountRules({ ...officialNew, configured: true })), [], 'saved key may be kept')
const customNew = { value: 'custom', source: 'custom', configured: false }
assert.deepEqual(missingFields({ provider: 'custom', model: '', base_url: '', api_key: '', reasoning_compat: true, reply_timeout: 120 }, languageAccountRules(customNew)), ['完整模型 ID', 'API Base URL'], 'custom key is optional for local servers')
const customSaved = { value: 'custom', source: 'custom', configured: true }
assert.deepEqual(missingFields({ provider: 'custom', model: '', base_url: '', api_key: '', reasoning_compat: true, reply_timeout: 120 }, languageAccountRules(customSaved)), [], 'saved custom model/base may be kept')
assert.deepEqual(missingFields({ provider: 'custom', model: '', base_url: 'https://new.example/v1', api_key: '', reasoning_compat: false }, languageAccountRules(customSaved)), ['新地址对应的 API Key'])
assert.deepEqual(missingFields({ provider: 'custom', model: 'm', base_url: '', api_key: '', reasoning_compat: true, reply_timeout: 5 }, languageAccountRules(customSaved)), ['单次回复最长等待（10–600 秒）'])

// Presets
assert.deepEqual(missingFields({ name: ' ', provider: 'custom', model: '' }, languagePresetRules), ['预设名称', '完整模型 ID'])

// Image profiles
const image = { name: 'n', protocol: 'async_task', base_url: 'https://a.example', model_id: 'm', api_key: '', resolutions: ['1k'] }
assert.deepEqual(missingFields(image, imageProfileRules({ isNew: true })), ['API Key'])
assert.deepEqual(missingFields(image, imageProfileRules({ isNew: false, previousBaseUrl: 'https://a.example/', hadKeys: true })), [], 'trailing slash is not a new address')
assert.deepEqual(missingFields({ ...image, base_url: 'https://b.example' }, imageProfileRules({ previousBaseUrl: 'https://a.example', hadKeys: true })), ['新地址对应的 API Key'])
assert.deepEqual(missingFields({ ...image, resolutions: [] }, imageProfileRules({})), ['至少一种支持分辨率'])
assert.deepEqual(missingFields({ ...image, protocol: 'openai_sync', size: '1536x1024', timeout_seconds: 10 }, imageProfileRules({})), ['等待超时（30–1800 秒）'])
assert.deepEqual(missingFields({ ...image, protocol: 'openai_sync', size: 'large', timeout_seconds: 600 }, imageProfileRules({})), ['出图尺寸（auto 或 宽x高）'])

// Extra JSON
assert.deepEqual(parseExtraParameters(''), { ok: true, value: {} })
assert.equal(parseExtraParameters('[1]').ok, false)
assert.equal(parseExtraParameters('{bad').ok, false)
assert.deepEqual(parseExtraParameters('{"output_format":"png"}').value, { output_format: 'png' })

// Concurrency
assert.deepEqual(missingFields({ mode: 'auto', per_key: 1, total: 0 }, imageConcurrencyRules()), [], 'total only matters in manual mode')
assert.deepEqual(missingFields({ mode: 'manual', per_key: 17, total: 0 }, imageConcurrencyRules()), ['单 Key 并发（1–16）', '总并发上限（1–64）'])

// Video: same site appends keys; new site needs keys; RH workflow fields follow the backend's require_ready
assert.equal(urlOrigin('https://a.example/v1'), urlOrigin('https://a.example:443/other'))
assert.notEqual(urlOrigin('https://a.example'), urlOrigin('https://b.example'))
const video = { protocol: 'async_task', base_url: 'https://v.example', submit_path: '/s', api_keys: [''], per_key_concurrency: 1, concurrency_mode: 'auto', total_concurrency: 3 }
assert.deepEqual(missingFields(video, videoRules({ savedKeyCount: 0 })), ['视频 API Key'])
assert.deepEqual(missingFields(video, videoRules({ savedKeyCount: 0, reuseImageKeys: true })), [])
assert.deepEqual(missingFields(video, videoRules({ savedKeyCount: 2, previousBaseUrl: 'https://v.example/' })), [], 'same site keeps saved keys')
assert.deepEqual(missingFields({ ...video, base_url: 'https://w.example' }, videoRules({ savedKeyCount: 2, previousBaseUrl: 'https://v.example' })), ['新站点的视频 API Key'])
const rhDraft = { ...video, protocol: 'runninghub_workflow', submit_path: '', workflow: { workflow_id: '1', prompt_node: { node_id: '' }, duration_node: { node_id: '9' }, preferred_output: '' }, image_nodes_text: '', output_nodes_text: '3' }
assert.deepEqual(missingFields(rhDraft, videoRules({ savedKeyCount: 1, previousBaseUrl: 'https://v.example' })), ['图片槽位', '提示词节点编号', '成品输出节点'])
assert.deepEqual(missingFields({ ...video, protocol: 'ark', model: '' }, videoRules({ savedKeyCount: 1, previousBaseUrl: 'https://v.example' })), ['模型调用名称'])

// Vibe
assert.deepEqual(missingFields({ base_url: 'https://x', model: 'm', api_key: '' }, vibeRules({ configured: true, previousBaseUrl: 'https://x' })), [])
assert.deepEqual(missingFields({ base_url: 'https://y', model: 'm', api_key: '' }, vibeRules({ configured: true, previousBaseUrl: 'https://x' })), ['新地址对应的 API Key'])
assert.deepEqual(missingFields({ base_url: 'https://x', model: '', api_key: '' }, vibeRules({})), ['多模态模型', 'API Key'])

// Services page: language account changes are drafts; no select change may save.
const panel = readFileSync(new URL('../src/components/services/LanguageServicePanel.vue', import.meta.url), 'utf8')
const studio = readFileSync(new URL('../src/Studio.vue', import.meta.url), 'utf8')
const servicesStart = studio.indexOf(`v-else-if="studioPage==='services'"`)
const services = studio.slice(servicesStart, studio.indexOf(`v-else-if="studioPage==='plugins'"`, servicesStart))
assert.ok(servicesStart > 0 && services.length > 1000, 'services section found')
assert.ok(!/onLanguage(Provider|Source|Model)Changed/.test(services), 'services page must not use auto-saving change handlers')
assert.ok(!/@change="[^"]*save/i.test(panel), 'no select change in the language panel may save')
assert.ok(/role="tablist"/.test(services) && /role="tab"/.test(services), 'service categories use tab semantics')
assert.ok(!/@change="saveImageConcurrencySettings"/.test(services), 'concurrency mode change must not save immediately')
assert.ok(!/id:'concurrency'/.test(studio), '出图并发 is merged into 图像接口')
for (const file of ['../src/components/services/VideoServicePanel.vue', '../src/components/VibeModelSettings.vue']) {
  assert.ok(!/@change="[^"]*save/i.test(readFileSync(new URL(file, import.meta.url), 'utf8')), file + ': no select change may save')
}
// Secrets: an untouched key field is never sent.
const imagePanel = readFileSync(new URL('../src/components/services/ImageServicePanel.vue', import.meta.url), 'utf8')
assert.ok(/if \(!String\(payload\.api_key \|\| ''\)\.trim\(\)\) delete payload\.api_key/.test(imagePanel))
console.log('PASS service drafts: language/image/video/vibe/concurrency rules, key keep/replace/append, extra JSON, no auto-save, tab semantics')
