import test from 'node:test'
import assert from 'node:assert/strict'
import {planningMaximum,splitMaximum,planningPreferences} from '../src/shotPlanningLimit.js'
import {shotDurationIssue} from '../src/shotDurationIssues.js'
test('defaults, custom values and legacy cluster recommendation',()=>{
 assert.equal(planningMaximum({}),14)
 assert.equal(planningMaximum({dynamic_max_shot_duration:28}),28)
 assert.equal(planningMaximum({dynamic_max_shot_duration:15}),15)
 assert.equal(planningMaximum({video_generation_backend:'cloud_h3',dynamic_max_shot_duration:30}),14)
 const params={video_generation_backend:'cloud_h3',dynamic_max_shot_duration:30,dynamic_cluster_duration_recommended:false}
 assert.equal(planningMaximum(params),30)
 assert.equal(splitMaximum({},params),14)
 assert.equal(splitMaximum({video_generation_options:{backend:'api'}},params),30)
 assert.equal(params.dynamic_max_shot_duration,30)
 assert.equal(shotDurationIssue({kind:'video',duration:20},params).maximum,14)
 assert.equal(shotDurationIssue({kind:'video',duration:40,video_generation_options:{backend:'api'}},params),null)
})
test('submitted preferences keep custom value and give readable invalid-input errors',()=>{
 assert.deepEqual(planningPreferences({video_generation_backend:'cloud_h3',dynamic_max_shot_duration:28}),{dynamic_max_shot_duration:28,dynamic_cluster_duration_recommended:true})
 assert.equal(planningPreferences({video_generation_backend:'cloud_h3',dynamic_max_shot_duration:''}).dynamic_max_shot_duration,14)
 for(const value of ['',3,14.5,NaN])assert.throws(()=>planningPreferences({dynamic_max_shot_duration:value}),/至少 4 秒/)
})
