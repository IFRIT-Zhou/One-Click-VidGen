// Latest pending selection per key; running tasks are never duplicated.
export function createSerialTaskQueue(worker, onState = () => {}) {
 const pending = [], states = new Map()
 let draining = false
 const report = (key, state) => { states.set(key, state); onState(key, state) }
 async function drain() {
  if (draining) return
  draining = true
  try {
   while (pending.length) {
    const item = pending.shift()
    report(item.key, 'running')
    try { await worker(item.task); report(item.key, 'completed') }
    catch (error) { report(item.key, 'failed') }
   }
  } finally { draining = false }
 }
 return {
  enqueue(key, task) {
   if (states.get(key) === 'running') return false
   const existing = pending.find(item => item.key === key)
   if (existing) existing.task = task
   else pending.push({key, task})
   report(key, 'queued')
   void drain()
   return true
  }
 }
}
