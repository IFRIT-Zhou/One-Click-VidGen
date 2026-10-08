import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { readCloudPoolPreference, saveCloudPoolPreference, workspaceParameters } from '../src/cloudPoolPreference.js'

const values = new Map()
const storage = { getItem: key => values.get(key), setItem: (key, value) => values.set(key, value) }
assert.equal(readCloudPoolPreference(storage), false)
saveCloudPoolPreference(true, storage)
assert.equal(readCloudPoolPreference(storage), true)
const form = { use_cloud_image_pool: true, project_name: '' }
Object.assign(form, workspaceParameters({ use_cloud_image_pool: false, project_name: '旧任务' }))
assert.equal(form.use_cloud_image_pool, true)
assert.equal(form.project_name, '旧任务')
Object.assign(form, workspaceParameters({ use_cloud_image_pool: false, project_name: '旧预设' }))
assert.equal(form.use_cloud_image_pool, true)
saveCloudPoolPreference(false, storage)
assert.equal(readCloudPoolPreference(storage), false)
assert.equal(readCloudPoolPreference({ getItem() { throw Error('blocked') } }), false)
assert.doesNotThrow(() => saveCloudPoolPreference(true, { setItem() { throw Error('blocked') } }))
const source = readFileSync(new URL('../src/useWorkspace.js', import.meta.url), 'utf8')
assert.match(source, /use_cloud_image_pool: readCloudPoolPreference\(\)/)
assert.match(source, /Object\.entries\(workspaceParameters\(job\.request\)\)/)
assert.match(source, /Object\.assign\(form, workspaceParameters\(parameters\)\)/)
assert.match(source, /saveCloudPoolPreference\(enabled\)/)
assert.match(source, /function guidedVisualParameters\(\)[\s\S]*?use_cloud_image_pool: form\.use_cloud_image_pool/)
console.log('cloud pool preference regression tests passed')
