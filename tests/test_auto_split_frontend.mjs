import {test} from 'node:test'
import assert from 'node:assert/strict'
import {autoSplitWindow,needsAutoSplit} from '../frontend/src/autoSplitWindow.js'
test('only selected vs adjacent dynamic shots, with edge/static boundaries',()=>{
 const shots=[{kind:'static'},{kind:'video'},{kind:'video'},{kind:'video'}]
 assert.deepEqual(autoSplitWindow(shots,1).map(x=>x.index),[1])
 assert.deepEqual(autoSplitWindow(shots,1,true).map(x=>x.index),[1,2])
 assert.deepEqual(autoSplitWindow(shots,2,true).map(x=>x.index),[1,2,3])
 assert.deepEqual(autoSplitWindow(shots,3,true).map(x=>x.index),[2,3])
 assert.deepEqual(autoSplitWindow(shots,0,true),[])
})
test('14 seconds allowed; request rounds up without shortening audio',()=>{
 assert.equal(needsAutoSplit({kind:'video',duration:14}),false)
 assert.equal(needsAutoSplit({kind:'video',duration:14.001}),true)
 assert.equal(needsAutoSplit({kind:'static',duration:20}),false)
})
