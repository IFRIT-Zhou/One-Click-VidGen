import assert from 'node:assert/strict'
import {visualPlaybackRange,previewRate} from '../src/motionTimeline.js'
const shots=[{start:.2,end:2},{start:2.6,end:4.6}]
assert.deepEqual(visualPlaybackRange(shots,0,5),{start:0,end:2.6,duration:2.6})
assert.deepEqual(visualPlaybackRange(shots,1,5),{start:2.6,end:5,duration:2.4})
assert.equal(previewRate(4,2),1)
assert.equal(previewRate(2,4),.5)
assert.equal(previewRate(NaN,4),1)
assert.equal(previewRate(0,4),1)
console.log('Final narration preview: trims, slows and retains pauses')
