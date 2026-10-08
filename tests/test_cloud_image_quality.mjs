import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { parse, compileScript } from '../frontend/node_modules/@vue/compiler-sfc/dist/compiler-sfc.esm-browser.js'
import { computed, watch, reactive, nextTick } from '../frontend/node_modules/vue/dist/vue.runtime.esm-browser.js'
import { ICAN_SIZES, imageChoice, imageSize } from '../frontend/src/icanImageSizes.js'

const source = readFileSync(new URL('../frontend/src/components/CloudImageQuality.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'quality-test' }).content
  .replace(/^import .*$/gm, '').replace('export default', 'return')
const component = new Function('computed', 'watch', 'ICAN_SIZES', 'imageChoice', 'imageSize', script)(computed, watch, ICAN_SIZES, imageChoice, imageSize)
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
assert.equal(imageForm.size, '1024x1024')
assert.deepEqual(imageState.qualities.value, ['1K'])
console.log('Shared selector: both channels, orientation, resolution, switching and standalone image settings passed')
