import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';
const root = new URL('../', import.meta.url);
const source = fs.readFileSync(new URL('assets/password-reset.js', root), 'utf8');
function setup(fetch, extra = {}) { const env = { URLSearchParams, fetch, ...extra }; vm.runInNewContext(source, env); return { env, client: env.OCVGPasswordReset.createClient(env) }; }
const response = (status, body, retry) => ({ ok: status >= 200 && status < 300, status, json: async () => body, headers: { get: () => retry } });
test('sends only email, uses generic message and enforces cooldown', async () => {
  let time = 1000; const calls = [];
  const { client } = setup(async (url, options) => { calls.push({url, options}); return response(200, { message: 'ignored user-specific detail', retry_after: 60 }); }, { now: () => time });
  assert.match(await client.send(' person@example.test '), /如果该邮箱/);
  assert.deepEqual(JSON.parse(calls[0].options.body), { email: 'person@example.test' });
  assert.equal(calls[0].url, '/api/v1/auth/password-reset/request');
  assert.equal(client.remaining(), 60); await assert.rejects(client.send('other@example.test'), /60 秒/);
  time += 60000; assert.equal(client.remaining(), 0); assert.equal(calls.length, 1);
});
test('blocks duplicate sends while network call is pending', async () => {
  let resolve; const { client } = setup(() => new Promise(r => { resolve = r; }));
  const sending = client.send('p@example.test'); await assert.rejects(client.send('p@example.test'), /正在发送/);
  resolve(response(200, { retry_after: 60 })); await sending;
});
test('rate limiting uses Retry-After and never presents request as success', async () => {
  const { client } = setup(async () => response(429, { detail: 'limit' }, '120'), { now: () => 0 });
  await assert.rejects(client.send('p@example.test'), /频繁/); assert.equal(client.remaining(), 120);
});
test('network and server errors can be retried without a fabricated successful send', async () => {
  let calls = 0; const { client } = setup(async () => { calls++; if (calls === 1) throw Error('offline'); return response(503, { detail: '邮件服务暂不可用' }); });
  await assert.rejects(client.send('p@example.test'), /网络连接/);
  await assert.rejects(client.send('p@example.test'), /邮件服务暂不可用/); assert.equal(client.remaining(), 0);
});
test('validates six digit code and password confirmation before contacting API', async () => {
  let calls = 0; const { client } = setup(async () => { calls++; return response(200, {}); });
  await assert.rejects(client.confirm('p@example.test', 'abcdef', '1234567890', '1234567890'), /6 位/);
  await assert.rejects(client.confirm('p@example.test', '123456', '123456789', '123456789'), /10 至 200/);
  await assert.rejects(client.confirm('p@example.test', '123456', 'x'.repeat(201), 'x'.repeat(201)), /10 至 200/);
  await assert.rejects(client.confirm('p@example.test', '123456', '1234567890', '123456789a'), /不一致/);
  assert.equal(calls, 0);
});
test('confirmation sends reset credentials without login or persisted password', async () => {
  const calls = []; const { client } = setup(async (url, options) => { calls.push({ url, body: JSON.parse(options.body) }); return response(200, { ok: true }); });
  await client.confirm(' p@example.test ', ' 012345 ', ' mypassword ', ' mypassword ');
  assert.deepEqual(calls, [{ url: '/api/v1/auth/password-reset/confirm', body: { email: 'p@example.test', code: '012345', password: ' mypassword ' } }]);
});
test('expired code error is shown and confirmation can be retried', async () => {
  let calls=0; const { client }=setup(async () => ++calls===1 ? response(400, { detail: '验证码错误或已过期' }) : response(200, {ok:true}));
  await assert.rejects(client.confirm('p@example.test', '123456', '1234567890', '1234567890'), /已过期/);
  assert.equal((await client.confirm('p@example.test', '654321', '1234567890', '1234567890')).ok, true);
});
test('return URL is restricted to known local login pages', () => {
  const { env } = setup(); const path = env.OCVGPasswordReset.loginPath;
  assert.equal(path('?return=%2Fstudio%2F'), '/studio/#login'); assert.equal(path('?return=%2Fadmin%2F'), '/admin/');
  for(const value of ['https://evil.test', '//evil.test', '/%2Fevil.test', '/unknown/', 'javascript:alert(1)']) assert.equal(path('?return='+encodeURIComponent(value)), '/#login');
});
test('page interaction clears session and sensitive fields only after confirmed success', async () => {
  const elements={}; let removed='';
  for(const id of ['reset-form','reset-send','reset-submit','reset-email','reset-message','reset-back','reset-login','reset-code','reset-password','reset-repeat','reset-success']) elements[id]={ value:'', hidden:false, listeners:{}, addEventListener(name, fn){this.listeners[name]=fn;}, focus(){this.focused=true;}, reportValidity(){return true;} };
  elements['reset-form'].reset=()=>{for(const id of ['reset-email','reset-code','reset-password','reset-repeat']) elements[id].value='';};
  const { env }=setup(async()=>response(200,{ok:true}),{document:{getElementById:id=>elements[id]},location:{search:'?return=/studio/'},sessionStorage:{removeItem:key=>removed=key},setInterval(){}});
  elements['reset-email'].value='p@example.test';elements['reset-code'].value='123456';elements['reset-password'].value='1234567890';elements['reset-repeat'].value='1234567890';
  await elements['reset-form'].listeners.submit({preventDefault(){}});
  assert.equal(removed,'ocvg-cloud-session');assert.equal(elements['reset-form'].hidden,true);assert.equal(elements['reset-success'].hidden,false);
  assert.equal(elements['reset-password'].value,'');assert.equal(elements['reset-login'].href,'/studio/#login');assert.equal(elements['reset-login'].focused,true);
});
test('all login surfaces have reset links and shared pages use new asset version', () => {
  for(const path of ['index.html','studio/index.html','recharge/index.html','admin/index.html']) assert.match(fs.readFileSync(new URL(path,root),'utf8'), /href="\/forgot-password\/\?return=/);
  assert.match(fs.readFileSync(new URL('assets/account-header.js',root),'utf8'), /data-forgot-password/);
  for(const path of ['index.html','studio/index.html','recharge/index.html','projects/index.html','contact/index.html']) assert.match(fs.readFileSync(new URL(path,root),'utf8'), /account-header.js\?v=20261008-recovery1/);
});
