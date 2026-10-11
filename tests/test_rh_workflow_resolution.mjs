import test from 'node:test'
import assert from 'node:assert/strict'
import {readResolution,updateResolution,resolutionSize} from '../frontend/src/rhWorkflowResolution.js'
test('default and 1080P level match workflow calculation',()=>{
 assert.equal(resolutionSize(readResolution('[]')), '1376 × 768')
 assert.equal(resolutionSize(readResolution(updateResolution('[]','megapixels',2))), '1920 × 1088')
})
test('editing resolution preserves unrelated overrides and replaces target',()=>{
 const original=JSON.stringify([{node_id:'180',field:'frame_rate',value:24},{node_id:'115',field:'megapixels',value:1},{node_id:'115',field:'aspect_ratio',value:'9:16 (Portrait Widescreen)'}])
 const updated=updateResolution(original,'megapixels',2)
 assert.equal(JSON.parse(updated).length,3)
 assert.equal(JSON.parse(updated)[0].value,24)
 assert.equal(resolutionSize(readResolution(updated)),'1088 × 1920')
})
test('invalid input does not silently overwrite advanced settings',()=>{
 assert.throws(()=>updateResolution('{','megapixels',2))
 assert.throws(()=>updateResolution('{}','megapixels',2))
 assert.throws(()=>updateResolution('[]','megapixels',0))
 assert.throws(()=>updateResolution('[]','megapixels',NaN))
})

