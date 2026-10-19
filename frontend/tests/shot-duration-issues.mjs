import test from 'node:test'
import assert from 'node:assert/strict'
import {computed,ref} from 'vue'
import {projectDurationIssues,shotDurationIssue} from '../src/shotDurationIssues.js'

const cloud={video_generation_backend:'cloud_h3',dynamic_max_shot_duration:30}
const shot=(id,duration)=>({id,kind:'video',duration})
test('all overlimit ordinals are reported, including fractional requests',()=>{
 const rows=projectDurationIssues([6.3,3.7,5.9,4,14.53,5,14.65].map((n,i)=>shot(String(i),n)),cloud)
 assert.deepEqual(rows.map(row=>row.index),[4,6])
 assert.deepEqual(rows.map(row=>row.seconds),['14.53','14.65'])
 assert.ok(rows.every(row=>row.requested===15&&row.maximum===14))
 assert.equal(shotDurationIssue(shot('a',14),cloud),null)
 assert.equal(shotDurationIssue(shot('a',14.001),cloud).seconds,'14.001')
})
test('static, personal routes, and exact limits are not falsely marked',()=>{
 assert.equal(shotDurationIssue({...shot('a',60),kind:'static'},cloud),null)
 assert.equal(shotDurationIssue({...shot('a',20),video_generation_options:{backend:'api'}},cloud),null)
 assert.equal(shotDurationIssue(shot('a',15),{}),null)
 assert.equal(shotDurationIssue(shot('a',15.01),{}),null)
 assert.equal(shotDurationIssue(shot('a',10.01),{dynamic_max_shot_duration:10}),null)
 assert.equal(shotDurationIssue({...shot('a',14.53),video_request:{backend:'api',duration:8}},cloud).maximum,14)
 for(const invalid of [0,-1,NaN,Infinity,undefined])assert.equal(shotDurationIssue(shot('a',invalid),cloud),null)
})
test('splitting and saved route changes recompute IDs and displayed numbers',()=>{
 const shots=ref([shot('first',4),shot('long',16),shot('other',14.65)])
 const issues=computed(()=>projectDurationIssues(shots.value,cloud))
 assert.deepEqual(issues.value.map(row=>row.shot.id),['long','other'])
 shots.value.splice(1,1,shot('child-a',8),shot('child-b',8))
 assert.deepEqual(issues.value.map(row=>[row.shot.id,row.index]),[['other',3]])
 shots.value[3].video_generation_options={backend:'api'}
 assert.deepEqual(issues.value,[])
 shots.value[3].video_generation_options={backend:'cloud_h3'}
 assert.equal(issues.value[0].index,3)
 shots.value[3].duration=14
 assert.deepEqual(issues.value,[])
})
