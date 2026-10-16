const STORAGE_KEY = 'ocv.cloud_pool.enabled.v1'
const CHANNEL_KEY = 'ocv.cloud_pool.method.v1'

export function readCloudChannelPreference(storage) {
  // An explicit choice always wins; older installations have no channel key.
  try { return (storage ?? globalThis.localStorage)?.getItem(CHANNEL_KEY) === 'running' ? 'running' : 'ican' }
  catch { return 'ican' }
}

export function saveCloudChannelPreference(method, storage) {
  if (!['running', 'ican'].includes(method)) return
  try { (storage ?? globalThis.localStorage)?.setItem(CHANNEL_KEY, method) }
  catch { /* optional browser storage */ }
}

export function readCloudPoolPreference(storage) {
  try { return (storage ?? globalThis.localStorage)?.getItem(STORAGE_KEY) === '1' }
  catch { return false }
}

export function saveCloudPoolPreference(enabled, storage) {
  try { (storage ?? globalThis.localStorage)?.setItem(STORAGE_KEY, enabled ? '1' : '0') }
  catch { /* Browser storage may be unavailable. */ }
}

// The service switch is a user preference, not a historical task/preset field.
// Existing jobs retain their saved provider until explicitly advanced/submitted.
export function workspaceParameters(parameters = {}) {
  const { use_cloud_image_pool, ...rest } = parameters
  return rest
}
