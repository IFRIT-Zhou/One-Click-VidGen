import assert from 'node:assert/strict'
import {sceneReferenceReadiness as check} from '../src/sceneReferenceReadiness.js'
const base=()=>({creation_parameters:{dynamic_text_mode:'visual_first',scene_references_enabled:true},shots:[{id:'one'}]})
assert.equal(check(base()).can_confirm,true)
let p=base();p.shots[0].scene_reference_id='room';assert.equal(check(p).can_confirm,false)
p=base();p.scene_reference_plan={scenes:[{scene_id:'room',members:[0]}]};assert.equal(check(p).can_confirm,false)
p=base();p.shots[0].scene_reference_id='room';p.scene_assets=[{id:'room',image_status:'failed'}];assert.equal(check(p).can_confirm,false)
p.scene_assets[0].image_status='completed';assert.equal(check(p).can_confirm,true)
p.scene_assets[0].disabled=true;p.scene_assets[0].image_status='failed';assert.equal(check(p).can_confirm,true)
p=base();p.shots[0].scene_reference_disabled=true;p.scene_reference_plan={scenes:[{scene_id:'room',members:[0]}]};assert.equal(check(p).can_confirm,true)
p=base();p.scene_references_status='generating';assert.equal(check(p).can_confirm,false)
p=base();p.scene_reference_readiness={can_confirm:false,blockers:[{code:'SCENE_FILE_MISSING'}]};assert.equal(check(p).can_confirm,false)
console.log('scene-reference-readiness: 9 cases passed')
