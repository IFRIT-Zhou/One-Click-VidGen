import assert from 'node:assert/strict'
import { createSerialTaskQueue } from '../frontend/src/serialTaskQueue.js'

let release
const gate=new Promise(resolve=>{release=resolve})
const calls=[],states=[],waiters=[]
let active=0,maxActive=0
const queue=createSerialTaskQueue(async task=>{
 active++;maxActive=Math.max(maxActive,active);calls.push(task)
 try{if(task==='first')await gate;if(task==='bad')throw new Error('expected')}
 finally{active--}
},(key,state)=>{states.push([key,state]);if(key==='four'&&state==='completed')waiters.splice(0).forEach(resolve=>resolve())})
queue.enqueue('one','first')
assert.equal(queue.enqueue('one','duplicate'),false)
queue.enqueue('two','old')
queue.enqueue('two','latest')
queue.enqueue('three','bad')
queue.enqueue('four','last')
assert.deepEqual(calls,['first'])
const complete=new Promise(resolve=>waiters.push(resolve))
release();await complete
assert.deepEqual(calls,['first','latest','bad','last'])
assert.equal(maxActive,1)
assert.ok(states.some(([key,state])=>key==='three'&&state==='failed'))
assert.ok(states.some(([key,state])=>key==='two'&&state==='queued'))
console.log('Serial queue: ordering, coalescing, duplicate prevention and failure continuation passed')
