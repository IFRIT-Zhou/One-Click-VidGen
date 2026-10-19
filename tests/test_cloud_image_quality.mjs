import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '../frontend/node_modules/@vue/compiler-sfc/dist/compiler-sfc.esm-browser.js'
import { computed, watch, reactive, nextTick } from '../frontend/node_modules/vue/dist/vue.runtime.esm-browser.js'
import { ICAN_SIZES, imageChoice, imageSize, imageQualityLabel, imageQualityHint } from '../frontend/src/icanImageSizes.js'

const source = readFileSync(new URL('../frontend/src/components/CloudImageQuality.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'quality-test' }).content
  .replace(/^import .*$/gm, '').replace('export default', 'return')
const component = new Function('computed', 'watch', 'ICAN_SIZES', 'imageChoice', 'imageSize', 'imageQualityLabel', 'imageQualityHint', script)(computed, watch, ICAN_SIZES, imageChoice, imageSize, imageQualityLabel, imageQualityHint)
const form = reactive({ method: 'running', video_orientation: 'landscape', image_resolution: '1k', size: '2560x1440' })
const state = component.setup({ form, standalone: false }, { expose() {} })
assert.deepEqual(state.qualities.value, ['1K','2K','4K'])
state.quality.value = '4K'
state.ratio.value = '9:16'
await nextTick()
assert.equal(form.image_resolution, '4k')
assert.equal(form.video_orientation, 'portrait')
form.method = 'ican'
await nextTick()
assert.deepEqual(state.qualities.value, ['2K','2.5K'])
assert.equal(form.size, '1440x2560')
state.quality.value = '2K'
assert.equal(form.size, '1152x2048')
const defaultForm = reactive({method:'running', video_orientation:'landscape'})
const defaultState = component.setup({form:defaultForm,standalone:false},{expose(){}})
assert.equal(defaultForm.image_resolution, '2k')
assert.equal(defaultState.quality.value, '2K')
form.method = 'running'
await nextTick()
assert.equal(state.quality.value, '4K')
assert.equal(form.size, '1152x2048')
const imageForm = reactive({ method: 'running', ratio: '3:2', image_resolution: '2k', size: '2560x1440' })
const imageState = component.setup({ form: imageForm, standalone: true }, { expose() {} })
imageState.ratio.value = '21:9'
assert.equal(imageForm.ratio, '21:9')
imageForm.method = 'ican'
await nextTick()
imageState.ratio.value = '1:1'
assert.equal(imageForm.size, '2048x2048')
assert.deepEqual(imageState.qualities.value, ['2K', '2.8K'])
imageState.quality.value = '2.8K'
assert.equal(imageForm.size, '2880x2880')
imageState.ratio.value = '3:4'
assert.equal(imageForm.size, '1920x2560')
assert.deepEqual(imageState.qualities.value, ['2K', '2.5K', '3K'])
imageState.quality.value = '3K'
assert.equal(imageForm.size, '2304x3072')
imageState.ratio.value = '16:9'
assert.equal(imageForm.size, '3072x1728')
imageState.quality.value = '4K'
assert.equal(imageForm.size, '3840x2160')
assert.match(imageState.hint.value, /4096×2304/)
assert.equal(imageQualityLabel('ican','3:4','3K'), '3K · 2304×3072')
assert.equal(imageQualityLabel('running','3:4','4K'), '4K 档')
assert.match(imageQualityHint('running','3:4','4K'), /2448×3264.*2480×3312/)
assert.ok(!Object.values(ICAN_SIZES['1:1']).includes('2560x2560'))
assert.deepEqual(imageChoice('1024x1024'), {ratio:'1:1', quality:'2K'})
for (const [ratio, choices] of Object.entries(ICAN_SIZES)) {
 for (const [quality, size] of Object.entries(choices)) {
  assert.deepEqual(imageChoice(size), {ratio, quality})
  assert.equal(imageSize(ratio, quality), size)
 }
}
const oldSquare = reactive({method:'ican', size:'1024x1024'})
component.setup({form:oldSquare, standalone:true},{expose(){}})
assert.equal(oldSquare.size, '2048x2048')
console.log('Shared selector: both channels, orientation, resolution, switching and standalone image settings passed')
