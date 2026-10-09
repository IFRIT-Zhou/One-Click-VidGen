import test from 'node:test'
import assert from 'node:assert/strict'
import fs from 'node:fs'
import { createPasswordRecoveryClient } from '../frontend/src/cloudPasswordRecovery.js'
test('send uses local proxy and respects 60 second cooldown',async()=>{
  let now=0;const calls=[];const client=createPasswordRecoveryClient(async(...args)=>{calls.push(args);return {retry_after:60}},()=>now)
  await client.send(' a@example.com ');assert.equal(calls[0][0],'/api/cloud/auth/password-reset/request')
  assert.deepEqual(JSON.parse(calls[0][1].body),{email:'a@example.com'})
  await assert.rejects(client.send('a@example.com'),/60/);now=60000;await client.send('a@example.com');assert.equal(calls.length,2)
})
test('in flight request prevents duplicate sends',async()=>{
  let resolve;const client=createPasswordRecoveryClient(()=>new Promise(r=>resolve=r))
  const first=client.send('a@example.com');await assert.rejects(client.send('a@example.com'),/重复/);resolve({});await first
})
test('invalid code and inconsistent passwords never contact server',async()=>{
  let calls=0;const client=createPasswordRecoveryClient(async()=>calls++)
  await assert.rejects(client.confirm('a@example.com','123','long-password','long-password'),/6 位/)
  await assert.rejects(client.confirm('a@example.com','123456','short','short'),/10 至/)
  await assert.rejects(client.confirm('a@example.com','123456','long-password','other-password'),/不一致/)
  assert.equal(calls,0)
})
test('expired code allows retry and successful reset never logs in automatically',async()=>{
  const calls=[];const client=createPasswordRecoveryClient(async(url,options)=>{calls.push(url);if(calls.length===1)throw new Error('验证码已过期');return {ok:true}})
  await assert.rejects(client.confirm('a@example.com','123456','long-password','long-password'),/过期/)
  assert.equal((await client.confirm('a@example.com','654321','long-password','long-password')).ok,true)
  assert.ok(calls.every(url=>url==='/api/cloud/auth/password-reset/confirm'))
})
test('429 propagates retry time',async()=>{
  const client=createPasswordRecoveryClient(async()=>{throw Object.assign(new Error('频繁'),{status:429,retryAfter:'120'})},()=>0)
  await assert.rejects(client.send('a@example.com'),/频繁/);assert.equal(client.remaining(),120)
})
test('both client views use embedded recovery rather than website link',()=>{
  for(const name of ['App.vue','Studio.vue']){
    const text=fs.readFileSync(new URL('../frontend/src/'+name,import.meta.url),'utf8')
    assert.match(text,/<CloudPasswordRecovery v-if="cloudRecoveryOpen"/)
    assert.match(text,/@click="openCloudRecovery"/)
    assert.doesNotMatch(text,/href="https:\/\/oneclickvidgen.com\/forgot-password\//)
  }
})
