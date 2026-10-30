import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { guidedServiceRoutes } from '../src/guidedServiceRoutes.js'
const source = readFileSync(new URL('../src/useWorkspace.js', import.meta.url), 'utf8')
const start = source.indexOf('async function saveCurrentParameterPreset() {')
const end = source.indexOf('\nasync function ', start + 10)
const body = source.slice(start, end)
for (const [global, local, language, expected] of [
  [true, null, null, ['cloud', true]],
  [false, null, null, ['personal', false]],
  [true, false, null, ['cloud', false]],
  [true, true, 'personal', ['personal', true]],
  [false, false, 'cloud', ['cloud', false]],
  [true, false, 'project', ['personal', false]],
]) {
  const form = { project_name:'test', script:'keep', use_cloud_image_pool:global, task_image_pool:local, language_route:language }
  const before = JSON.stringify(form)
  let saved
  const context = { form, guidedServiceRoutes, ttsEngine:{value:'cluster'}, savingParameterPreset:{value:false},
    parameterPresetMessage:{value:''}, selectedParameterPreset:{value:''},
    api:{saveParameterPreset:async p => { saved=p; return {name:'test'} }},
    refreshParameterPresets:async()=>{} }
  await new Function('ctx', `with(ctx){${body};return saveCurrentParameterPreset()}`)(context)
  assert.ok(saved, context.parameterPresetMessage.value)
  assert.equal(saved.parameters.language_route, expected[0])
  assert.equal(saved.parameters.use_cloud_image_pool, expected[1])
  assert.equal(saved.parameters.manual_script, 'keep')
  assert.equal(JSON.stringify(form), before)
}
console.log('PASS preset save: unset, explicit and mixed channels; original form preserved')
