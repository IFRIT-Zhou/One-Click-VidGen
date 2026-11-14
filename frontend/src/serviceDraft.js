// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Zhou Ruoyu and He Yun
// AGPL-3.0 Section 7 terms: ../../ADDITIONAL_TERMS.md

// Pure helpers for the service configuration editors: required-field checks
// and draft comparison. They never touch requests or persistence.

const blank = (value) => !String(value ?? '').trim()

export function snapshotDraft(draft) {
  return JSON.stringify(draft ?? null)
}

export function draftChanged(draft, baseline) {
  return baseline !== undefined && baseline !== null && snapshotDraft(draft) !== baseline
}

// Each rule: { key, label, when?(draft) => bool, missing?(draft) => bool }
export function missingFields(draft, rules) {
  return rules
    .filter((rule) => !rule.when || rule.when(draft))
    .filter((rule) => (rule.missing ? rule.missing(draft) : blank(draft?.[rule.key])))
    .map((rule) => rule.label)
}

export function languageAccountRules(provider = {}) {
  const custom = provider.source === 'custom' || provider.value === 'custom'
  const configured = !!provider.configured
  return [
    { key: 'provider', label: '接口账号' },
    // A saved custom interface hides its model; leaving it empty keeps the saved one.
    { key: 'model', label: custom ? '完整模型 ID' : 'Agent 模型', when: () => !(custom && configured) },
    { key: 'base_url', label: 'API Base URL', when: () => custom && !configured },
    { key: 'api_key', label: 'API Key', when: () => !custom && !configured },
    // The backend refuses to send an old key to a new address.
    { key: 'api_key', label: '新地址对应的 API Key', when: (d) => custom && configured && !blank(d.base_url) },
    {
      key: 'reply_timeout', label: '单次回复最长等待（10–600 秒）',
      when: (d) => custom && d.reasoning_compat,
      missing: (d) => !(Number(d.reply_timeout) >= 10 && Number(d.reply_timeout) <= 600),
    },
  ]
}

export const languagePresetRules = [
  { key: 'name', label: '预设名称' },
  { key: 'provider', label: '复用接口账号' },
  { key: 'model', label: '完整模型 ID' },
]

export function imageProfileRules({ isNew = false, previousBaseUrl = '', hadKeys = false } = {}) {
  return [
    { key: 'name', label: '配置名称' },
    { key: 'protocol', label: '接口协议' },
    { key: 'base_url', label: 'API Base URL' },
    { key: 'model_id', label: '模型 ID' },
    { key: 'api_key', label: 'API Key', when: () => isNew },
    {
      key: 'api_key', label: '新地址对应的 API Key',
      when: (d) => !isNew && hadKeys && !blank(d.base_url) && d.base_url.trim().replace(/\/+$/, '') !== String(previousBaseUrl || '').replace(/\/+$/, ''),
    },
    { key: 'resolutions', label: '至少一种支持分辨率', missing: (d) => !d.resolutions?.length },
    {
      key: 'timeout_seconds', label: '等待超时（30–1800 秒）',
      when: (d) => d.protocol === 'openai_sync',
      missing: (d) => !(Number(d.timeout_seconds) >= 30 && Number(d.timeout_seconds) <= 1800),
    },
    {
      key: 'size', label: '出图尺寸（auto 或 宽x高）',
      when: (d) => d.protocol === 'openai_sync',
      missing: (d) => !/^(auto|[1-9][0-9]{1,4}x[1-9][0-9]{1,4})$/.test(String(d.size || '').trim()),
    },
  ]
}

// scheme + host + port, the same "site" notion the video backend uses before
// deciding whether saved keys may be kept for a new address.
export function urlOrigin(value) {
  try {
    const url = new URL(String(value || '').trim())
    return `${url.protocol}//${url.hostname}:${url.port || (url.protocol === 'https:' ? '443' : '80')}`
  } catch { return '' }
}

const intIn = (value, min, max) => Number.isInteger(Number(value)) && Number(value) >= min && Number(value) <= max

export function imageConcurrencyRules() {
  return [
    { key: 'per_key', label: '单 Key 并发（1–16）', missing: (d) => !intIn(d.per_key, 1, 16) },
    { key: 'total', label: '总并发上限（1–64）', when: (d) => d.mode === 'manual', missing: (d) => !intIn(d.total, 1, 64) },
  ]
}

// Video keys are appended on the same site; a new site needs fresh keys.
export function videoRules({ savedKeyCount = 0, previousBaseUrl = '', reuseImageKeys = false } = {}) {
  const newKeys = (d) => (d.api_keys || []).some((k) => !blank(k))
  const siteChanged = (d) => savedKeyCount > 0 && !!urlOrigin(d.base_url) && urlOrigin(d.base_url) !== urlOrigin(previousBaseUrl)
  const rh = (d) => d.protocol === 'runninghub_workflow'
  return [
    { key: 'protocol', label: '接入方式' },
    { key: 'model', label: '模型调用名称', when: (d) => d.protocol === 'ark' },
    { key: 'base_url', label: 'API Base URL' },
    { key: 'base_url', label: '有效的 API Base URL（http/https）', when: (d) => !blank(d.base_url), missing: (d) => !urlOrigin(d.base_url) },
    { key: 'submit_path', label: '视频提交路径', when: (d) => !rh(d) },
    { key: 'workflow_id', label: '工作流 ID', when: rh, missing: (d) => blank(d.workflow?.workflow_id) },
    { key: 'image_nodes', label: '图片槽位', when: rh, missing: (d) => blank(d.image_nodes_text) },
    { key: 'prompt_node', label: '提示词节点编号', when: rh, missing: (d) => blank(d.workflow?.prompt_node?.node_id) },
    { key: 'duration_node', label: '时长节点编号', when: rh, missing: (d) => blank(d.workflow?.duration_node?.node_id) },
    { key: 'output_nodes', label: '视频保存节点', when: rh, missing: (d) => blank(d.output_nodes_text) },
    { key: 'preferred_output', label: '成品输出节点', when: rh, missing: (d) => blank(d.workflow?.preferred_output) },
    { key: 'api_keys', label: '视频 API Key', when: () => !reuseImageKeys && savedKeyCount === 0, missing: (d) => !newKeys(d) },
    { key: 'api_keys', label: '新站点的视频 API Key', when: (d) => !reuseImageKeys && siteChanged(d), missing: (d) => !newKeys(d) },
    { key: 'per_key_concurrency', label: '单 Key 并发（1–8）', missing: (d) => !intIn(d.per_key_concurrency, 1, 8) },
    { key: 'total_concurrency', label: '总并发上限（1–32）', when: (d) => d.concurrency_mode === 'manual', missing: (d) => !intIn(d.total_concurrency, 1, 32) },
  ]
}

export function vibeRules({ configured = false, previousBaseUrl = '' } = {}) {
  return [
    { key: 'base_url', label: '服务地址' },
    { key: 'model', label: '多模态模型' },
    { key: 'api_key', label: 'API Key', when: () => !configured },
    { key: 'api_key', label: '新地址对应的 API Key', when: (d) => configured && !blank(d.base_url) && d.base_url.trim() !== String(previousBaseUrl || '').trim() },
  ]
}

// Returns { ok, value, error } for the optional extra-parameters JSON.
export function parseExtraParameters(text) {
  if (blank(text)) return { ok: true, value: {} }
  try {
    const value = JSON.parse(text)
    if (!value || Array.isArray(value) || typeof value !== 'object') return { ok: false, error: '额外参数须为 JSON 对象，例如 {"output_format":"png"}' }
    return { ok: true, value }
  } catch (error) {
    return { ok: false, error: '额外参数不是有效的 JSON：' + error.message }
  }
}
