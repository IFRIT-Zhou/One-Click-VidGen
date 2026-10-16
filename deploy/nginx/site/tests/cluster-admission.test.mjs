import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';
const source = fs.readFileSync(new URL('../assets/cluster-health.js', import.meta.url), 'utf8');
const context = vm.createContext({}); vm.runInContext(source, context);
const describe = context.OCVGClusterHealth.describe;
const cold = {ok:false,control_api:true,ray:{ok:false,dispatcher:{ready:false,draining:true,redis_ready:true,consumer_alive:true}},tts_admission:{accepting:true,on_demand:true,scheduler_ready:true,queue_ready:true}};
test('cold GPU and H3 resource sharing allow TTS queue submission', () => {
  assert.equal(describe(cold).accepting,true); assert.equal(describe(cold).ready,false);
  assert.match(describe(cold).label,/按需启动/);
});
test('unhealthy scheduler or queue cannot be bypassed by model readiness', () => {
  for (const key of ['accepting','scheduler_ready','queue_ready']) {
    assert.equal(describe({...cold,ok:true,tts_admission:{...cold.tts_admission,[key]:false}}).accepting,false);
  }
  for (const key of ['redis_ready','consumer_alive']) {
    assert.equal(describe({...cold,ray:{dispatcher:{[key]:false}}}).accepting,false);
  }
  assert.equal(describe({...cold,control_api:false}).accepting,false);
});
test('legacy healthy payload works and missing or unhealthy evidence fails closed', () => {
  assert.equal(describe({ok:true,control_api:true}).ready,true);
  for (const data of [null,{}, {ok:false,control_api:true}, {...cold,tts_admission:undefined}, {...cold,tts_admission:{on_demand:true}}]) assert.equal(describe(data).accepting,false);
});
test('studio health refresh enables actual submit button during cold start', async () => {
  const studio=fs.readFileSync(new URL('../assets/studio.js',import.meta.url),'utf8');
  const block = studio.slice(studio.indexOf('  async function checkHealth()'),studio.indexOf('  async function checkModelPool()'));
  const update = studio.slice(studio.indexOf('  function updateSubmit()'),studio.indexOf('  async function submitJob()'));
  const strong={},button={},node={querySelector:()=>strong};
  const sandbox=vm.createContext({window:{OCVGClusterHealth:context.OCVGClusterHealth},state:{session:{},selectedVoice:{},storyboard:[{}],quote:{estimated_credits:1}},api:async()=>cold,element:s=>s==='#cloud-state'?node:button,updateVideoJobButton:()=>{}});
  vm.runInContext(block+update,sandbox); await sandbox.checkHealth();
  assert.equal(button.disabled,false); assert.match(strong.textContent,/按需启动/);
  sandbox.api=async()=>({...cold,tts_admission:{...cold.tts_admission,scheduler_ready:false}});await sandbox.checkHealth();assert.equal(button.disabled,true);
  sandbox.api=async()=>{throw Error('network');};await sandbox.checkHealth();assert.equal(button.disabled,true);assert.equal(strong.textContent,'无法连接服务');
});
test('all affected pages load shared contract before consumers with new cache version',()=>{
 for (const path of ['index.html','studio/index.html','services/index.html','contact/index.html','recharge/index.html']) {
  const html=fs.readFileSync(new URL('../'+path,import.meta.url),'utf8');
  const shared=html.indexOf('/assets/cluster-health.js?v=20261012-admission1');
  const consumer=html.match(/\/assets\/(site|studio)\.js\?v=(\d{8})-[A-Za-z0-9.-]+/);
  assert.ok(shared>=0 && consumer && consumer.index>shared,path);
  assert.ok(Number(consumer[2])>=20261012,`${path}: consumer must include the admission update`);
 }
});
