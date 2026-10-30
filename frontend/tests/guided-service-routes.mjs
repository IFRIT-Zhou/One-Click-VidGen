import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { guidedServiceRoutes, guidedServiceParameters } from '../src/guidedServiceRoutes.js'
import { workspaceParameters } from '../src/cloudPoolPreference.js'
const source = readFileSync(new URL('../src/useWorkspace.js', import.meta.url), 'utf8')
const hydrateBody = source.split('function hydrateGuidedForm(job) {')[1].split('\nasync function loadGuidedAudioReview')[0]
const form = { use_cloud_image_pool: true, task_image_pool: null, language_route: null,
  method: 'running', reference_image_ids: [], reference_image_notes: {}, reference_image_labels: {}, reference_image_kinds: {} }
const job = { request: { language_route: 'personal', use_cloud_image_pool: false, method: 'ican' } }
new Function('form', 'workspaceParameters', 'ttsEngine', 'job', `return function(job){${hydrateBody}(job)`)(form, workspaceParameters, { value: 'cluster' }, job)
assert.deepEqual(guidedServiceRoutes(form), { language_route: 'personal', use_cloud_image_pool: false })
assert.equal(form.use_cloud_image_pool, true)
// Explicit one-click change, not implicit migration of old projects.
Object.assign(form, { language_route: 'cloud', task_image_pool: true })
const payloadBody = source.split('function guidedVisualParameters() {')[1].split('\nfunction guidedRenderParameters')[0]
const payload = new Function('form', 'cloudSession', 'guidedServiceParameters', `return function(){${payloadBody}()`)(form, { value: { authenticated: true } }, guidedServiceParameters)
assert.equal(payload.language_route, 'cloud')
assert.equal(payload.use_cloud_image_pool, true)
assert.equal(payload.method, 'ican')
assert.deepEqual(job.request, { language_route: 'personal', use_cloud_image_pool: false, method: 'ican' })
assert.throws(() => guidedServiceParameters(form, false), /未切换为个人 API/)
assert.equal(form.task_image_pool, true)
for (const language of ['cloud', 'personal']) for (const image of [true, false]) {
  form.language_route = language
  form.task_image_pool = image
  assert.deepEqual(guidedServiceParameters(form, true), { language_route: language, use_cloud_image_pool: image })
}
form.language_route = 'project'
assert.equal(guidedServiceRoutes(form).language_route, 'personal')
assert.deepEqual(guidedServiceParameters({language_route:'personal',task_image_pool:false},false),{language_route:'personal',use_cloud_image_pool:false})
console.log('PASS old project hydration, explicit channel changes, real payload, login loss and mixed routes')
