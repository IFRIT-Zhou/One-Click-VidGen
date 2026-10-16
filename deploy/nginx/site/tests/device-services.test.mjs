import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
import test from 'node:test';

const source = fs.readFileSync(new URL('../assets/admin-subpage.js', import.meta.url), 'utf8');
function fixture(payload, page = 'devices') {
  const nodes = new Map(), actions = new Map(), calls = [], listeners = new Map(), intervals = [];
  const element = () => ({innerHTML:'',textContent:'',className:'',setAttribute(){},removeAttribute(){},addEventListener(){}});
  const node = id => { if (!nodes.has(id)) nodes.set(id, element()); return nodes.get(id); };
  const context = vm.createContext({
    document:{body:{dataset:{adminPage:page}},hidden:false,getElementById:node,
      querySelector:s=>{if (s === '.admin-nav') return null;if (!actions.has(s)) actions.set(s,element());return actions.get(s);},
      querySelectorAll:()=>[],addEventListener:(name,fn)=>listeners.set(name,fn)},
    location:{search:'?id=node-3',assign(){}},
    sessionStorage:{getItem:()=>JSON.stringify({access_token:'test',user:{role:'admin'}}),setItem(){},removeItem(){}},
    fetch:async(path,options)=>{calls.push({path,options});return {ok:true,status:200,headers:{get:()=> 'application/json'},json:async()=>payload};},
    URLSearchParams,AbortController,setTimeout,clearTimeout,
    setInterval:(fn,ms)=>intervals.push({fn,ms}),prompt:()=> 'test reason',alert(){},
  });
  // Run the real renderer and action handlers without installing unrelated
  // list toolbar DOM. Helpers remain closure-private in production.
  const marker = '  if (wireShell()) {';
  assert.ok(source.includes(marker));
  vm.runInContext(source.replace(marker,'  globalThis.deviceTest = {agentState, connectivity, serviceState, serviceStatus, devices, deviceDetail, renderCurrent};\n  if (false) {'),context);
  return {context,api:context.deviceTest,node,actions,calls,listeners,intervals};
}
const service = (kind, overrides={}) => ({container_name:kind === 'tts' ? 'ray-worker-managed' : 'ocv-h3-comfyui',container_running:true,ray_connected:kind === 'tts' ? true : null,ready:true,status:'ready',reason:'探测正常',source:'node_agent',checked_at:'2026-10-15T09:00:00Z',...overrides});
const device = (overrides={}) => ({device_id:'node-3',lifecycle_state:'ACTIVE',ray_node_ip:'10.250.0.3/32',profile_id:'tts-gpu-worker-v1',generation:4,applied_revision:5,desired_revision:5,peer_status:'applied',last_seen_at:'2026-10-15T09:00:00Z',heartbeat_age_seconds:12,services:{tts:service('tts'),h3:service('h3')},...overrides});

test('H3 ready with TTS stopped stays independent from Agent heartbeat and Ray', async()=>{
  const item=device({heartbeat_age_seconds:1000,ray_connected:false,services:{tts:service('tts',{container_running:false,ray_connected:false,ready:false,status:'stopped'}),h3:service('h3')}});
  const f=fixture({total:1,items:[item]});await f.api.devices();
  const html=f.node('page-content').innerHTML;
  assert.match(html,/心跳超时/);assert.match(html,/Ray 离线/);
  assert.match(html,/TTS/);assert.match(html,/H3/);assert.doesNotMatch(html,/Ray 在线/);
  assert.match(html,/<article class="device-card">[\s\S]*?<code>10\.250\.0\.3\/32<\/code>[\s\S]*?<\/article>/);
  assert.match(html,/device-service-compact[\s\S]*?TTS[\s\S]*?device-service-compact[\s\S]*?H3/);
  assert.doesNotMatch(html,/admin-table-wrap|device-table|device-scroll-hint|Generation|Peer /);
  assert.match(html,/设备心跳与容器状态分别显示/);
  assert.match(f.node('page-summary').textContent,/TTS 就绪 0 台 · H3 就绪 1 台/);
});

test('legacy payload cannot manufacture independent service health from top-level Ray',async()=>{
  const item=device({services:undefined,ray_connected:true,connectivity_status:'online',heartbeat_age_seconds:null,heartbeat_status:undefined});
  const f=fixture({total:1,items:[item]});await f.api.devices();
  const html=f.node('page-content').innerHTML;
  assert.equal(f.api.agentState(item),'unknown');assert.equal(f.api.serviceState(item,'tts'),'unknown');
  assert.match(html,/容器未知/);assert.match(html,/Ray 未知/);assert.match(html,/模型 未知/);
  assert.match(f.node('page-summary').textContent,/TTS 就绪 0 台 · H3 就绪 0 台/);
});

test('compact device cards keep supported container start and stop controls',async()=>{
  const controllable=kind=>service(kind,{control:{supported:true,status:'idle',desired_running:true}});
  const f=fixture({total:1,items:[device({services:{tts:controllable('tts'),h3:controllable('h3')}})]});
  await f.api.devices();
  const html=f.node('page-content').innerHTML;
  assert.match(html,/data-service-action="start" data-service-kind="tts"/);
  assert.match(html,/data-service-action="stop" data-service-kind="tts"/);
  assert.match(html,/data-service-action="start" data-service-kind="h3"/);
  assert.match(html,/data-service-action="stop" data-service-kind="h3"/);
  assert.doesNotMatch(html,/admin-table-wrap|device-scroll-hint/);
});

test('Agent without reports remains unreported even with online Ray and ready services',()=>{
  const f=fixture({});
  assert.equal(f.api.agentState(device({last_seen_at:null,heartbeat_age_seconds:0,ray_connected:true})),'unreported');
  assert.equal(f.api.agentState(device({heartbeat_age_seconds:91,heartbeat_status:'online'})),'stale');
  assert.equal(f.api.agentState(device({heartbeat_age_seconds:90})),'online');
});

test('contradictory ready service is unknown, while stopped and not installed remain distinct',()=>{
  const f=fixture({});
  for (const change of [{ready:null},{container_running:null},{ray_connected:false}]) {
    assert.equal(f.api.serviceState(device({services:{tts:service('tts',change)}}),'tts'),'unknown');
  }
  assert.equal(f.api.serviceState(device(),'h3'),'ready');
  assert.equal(f.api.serviceState(device({services:{h3:service('h3',{status:'not_installed',ready:null,container_running:null})}}),'h3'),'not_installed');
});

test('device details show both services and preserve all management actions',async()=>{
  const f=fixture(device(),'device');await f.api.deviceDetail();
  const html=f.node('page-content').innerHTML;
  assert.match(html,/独立服务状态/);assert.match(html,/ray-worker-managed/);assert.match(html,/ocv-h3-comfyui/);
  assert.match(html,/H3 使用独立服务，Ray 不适用/);assert.match(html,/来源：node_agent/);assert.match(html,/最后 Agent 心跳/);
  assert.match(html,/PAUSED 仅表示 TTS Agent 暂停，不代表 H3 或整机暂停/);
  for (const kind of ['disable','rekey','refresh']) assert.equal(typeof f.actions.get(`[data-action="${kind}"]`).onclick,'function');
  await f.actions.get('[data-action="disable"]').onclick();
  const disable=f.calls.find(c=>c.path.endsWith('/disable'));
  assert.equal(disable.options.method,'POST');assert.equal(JSON.parse(disable.options.body).reason,'test reason');
  await f.actions.get('[data-action="rekey"]').onclick();
  assert.ok(f.calls.some(c=>c.path.endsWith('/rekey-token')));
});

test('untrusted service reason, container name and device ID are escaped',async()=>{
  const attack='<img src=x onerror="alert(1)">';
  const item=device({device_id:attack,lifecycle_state:'bad" onclick="x',services:{tts:service('tts',{reason:attack,container_name:attack,source:attack}),h3:service('h3')}});
  const f=fixture(item,'device');await f.api.deviceDetail();
  const html=f.node('page-content').innerHTML;
  assert.doesNotMatch(html,/<img src=x/);assert.match(html,/&lt;img src=x/);
  const list=fixture({items:[item],total:1});await list.api.devices();
  assert.doesNotMatch(list.node('page-content').innerHTML,/<img src=x|status-bad" onclick/);
});

test('failed refresh clears previous service states rather than showing stale healthy data',async()=>{
  const f=fixture({items:[device()],total:1});await f.api.renderCurrent();
  f.context.fetch=async()=>{throw new Error('network');};await f.api.renderCurrent();
  assert.match(f.node('page-content').innerHTML,/无法获取当前设备状态/);
  assert.doesNotMatch(f.node('page-content').innerHTML,/已就绪/);
  assert.equal(f.node('page-updated').textContent,'刷新失败，当前设备状态未知');
});

test('preserves live polling and ships compact no-scroll device cards',()=>{
  assert.match(source,/setInterval\(\(\) => \{ if \(!document.hidden\) renderCurrent\(\); \}, 15000\)/);
  assert.match(source,/addEventListener\("visibilitychange"/);
  for (const file of ['devices.html','device.html']) {
    const html=fs.readFileSync(new URL('../admin/'+file,import.meta.url),'utf8');
    assert.match(html,/admin-subpage\.js\?v=20261016.devicescompact2/);
    assert.match(html,/admin-pages\.css\?v=20261016.devicescompact2/);
  }
  const css=fs.readFileSync(new URL('../assets/admin-pages.css',import.meta.url),'utf8');
  assert.match(css,/\.device-list\{display:grid/);
  assert.match(css,/@media\(max-width:560px\)/);
  assert.match(css,/\.device-card-services\{grid-template-columns:1fr\}/);
  assert.match(css,/device-services-detail\{grid-template-columns:1fr\}/);
});
