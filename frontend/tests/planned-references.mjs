import test from 'node:test'
import assert from 'node:assert/strict'
import {resolvePlannedReferences} from '../src/plannedReferences.js'
const url=asset=>'/references/'+asset.id
test('planning preview resolves later uploads and preserves submission order',()=>{
 const rows=resolvePlannedReferences({reference_ids:['later','initial']},[{id:'initial',description:'人物',kind:'person'}],[{id:'later',name:'用户核对图.png'}],url)
 assert.deepEqual(rows.map(row=>row.name),['用户核对图.png','人物'])
 assert.equal(rows[0].url,'/references/later')
 assert.equal(rows[0].state,'')
 assert.equal(rows[1].purpose,'人物参考')
})
test('unknown bindings remain visible instead of silently disappearing',()=>{
 const rows=resolvePlannedReferences({reference_image_ids:['missing']},[],[],url)
 assert.equal(rows.length,1)
 assert.equal(rows[0].url,'')
 assert.match(rows[0].state,/未找到素材记录/)
 assert.deepEqual(resolvePlannedReferences({reference_ids:[],reference_image_ids:['old']},[],[],url),[])
})
