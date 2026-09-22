// Start Vite on port 5199, then: node frontend/tests/dynamic-text-mode.cjs "<browser executable>"
// All API and non-local requests are intercepted; this test cannot call paid services.
const puppeteer = require('../../node_modules/puppeteer-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const qaDirectory = path.resolve(__dirname, '../../.tmp_diagnostic_dynamic_text_mode');
const posts = [], errors = [];
let project = null;
const legacy = { id:'legacy-dynamic', revision:1, status:'draft', settings:{name:'历史动态任务',ratio:'16:9'}, creation_parameters:{script:'旧任务文案',director_strategy:'stable'}, shots:[], scenes:[], logs:[] };

async function main() {
 fs.mkdirSync(qaDirectory, { recursive:true });
 console.log('Starting isolated browser...');
 const browser = await puppeteer.launch({ executablePath:process.argv[2],headless:true,pipe:true });
 try {
  console.log('Browser ready; mocking all API requests.');
  const page = await browser.newPage(); await page.setViewport({width:1440,height:1100});
  page.on('pageerror', error=>{errors.push(error.message);console.error(error.stack)});
  page.on('dialog', dialog=>dialog.accept());
  await page.setRequestInterception(true);
  page.on('request', async request=>{
   const url = new URL(request.url()), path = url.pathname;
   const json = value=>request.respond({status:200,contentType:'application/json',body:JSON.stringify(value)});
   if(path.startsWith('/api/')) {
    const body = JSON.parse(request.postData()||'{}');
    if(request.method()!=='GET')posts.push({path,body});
    if(path==='/api/health')return json({ok:true,tts25_online:true});
    if(path==='/api/session')return json({user:null,auth_mode:'local'});
    if(path==='/api/subtitle-fonts')return json({fonts:['Microsoft YaHei','Arial']});
    if(path==='/api/video-studio/sources')return json({items:[{id:'source',name:'配音来源'}]});
    if(path==='/api/video-studio' && request.method()==='GET')return json({items:[legacy]});
    if(path==='/api/video-studio/legacy-dynamic')return json(legacy);
    if(path==='/api/video-studio/from-project' || (path==='/api/video-studio' && request.method()==='POST')) {
     project = {...legacy,id:'new-dynamic',settings:{...legacy.settings,...body},creation_parameters:{...body,dynamic_video:true}};
     return json(project);
    }
    if(path.includes('image-profiles'))return json({items:[],profiles:[]});
    if(path.includes('parameter-presets'))return json({items:[]});
    return json({items:[],jobs:[],assets:[],total:0,total_pages:1,page:1});
   }
   if(url.hostname!=='127.0.0.1')return request.abort();
   return request.continue();
  });
  await page.goto('http://127.0.0.1:5199/tests/dynamic-text-mode.html');
  console.log('Studio mounted; checking dynamic modes.');
  await page.waitForFunction(()=>window.studio?.health?.ok);
  const change = async code=>{await page.evaluate(code);await page.waitForNetworkIdle({idleTime:60,timeout:5000})};
  const click = async text=>{await page.evaluate(value=>[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===value&&!el.disabled)?.click(),text);await page.waitForNetworkIdle({idleTime:60,timeout:5000})};
  await change(()=>{studio.newProject('dynamic');studio.studioDrawer='画面编排'});
  assert.equal(await page.$eval('.dynamic-text-mode-row button.active',el=>el.textContent),'画面优先');
  assert.equal(await page.$('.director-strategy-options[aria-label="导演策略"]'),null);
  assert.equal(await page.$eval('.checkbox-row input',el=>el.disabled),false);
  await click('文字辅助');
  assert.deepEqual(await page.evaluate(()=>[studio.form.dynamic_text_mode,studio.form.director_strategy,studio.generationRequestPayload().dynamic_text_mode]),['text_assisted','stable','text_assisted']);
  assert.equal(await page.$eval('.checkbox-row input',el=>el.disabled),false);
  await page.screenshot({path:path.join(qaDirectory,'dynamic-text-mode-desktop.png'),fullPage:true});
  await change(()=>{studio.form.script='模式草稿测试';studio.goHome();const draft=studio.studioDrafts.find(item=>item.form.script==='模式草稿测试');studio.newProject('dynamic',draft)});
  assert.equal(await page.evaluate(()=>studio.form.dynamic_text_mode),'text_assisted');
  await change(()=>{studio.newProject('video');studio.studioDrawer='画面编排'});
  assert.equal(await page.$('.dynamic-text-mode-row'),null);
  assert.equal(await page.evaluate(()=>Object.hasOwn(studio.generationRequestPayload(),'dynamic_text_mode')),false);
  assert.ok(await page.$('.director-strategy-options[aria-label="导演策略"]'));
  assert.equal(await page.$eval('.checkbox-row input',el=>el.disabled),true);
  await change(()=>{studio.newProject('dynamic',{id:'old-draft',form:{dynamic_video:true,script:'草稿文案'},subtitle:{}});studio.studioDrawer='画面编排'});
  assert.equal(await page.$eval('.dynamic-text-mode-row button.active',el=>el.textContent),'文字辅助');
  assert.equal(await page.evaluate(()=>studio.form.scene_references_enabled),false);
  await change(()=>{studio.restoreDefaultsOnNewProject=true;studio.newProject('dynamic');studio.studioDrawer='画面编排'});
  assert.equal(await page.$eval('.dynamic-text-mode-row button.active',el=>el.textContent),'画面优先');
  await change(()=>{localStorage.setItem('ocv.studio.creation_preferences.v1',JSON.stringify({dynamic_text_mode:'text_assisted'}));studio.restoreDefaultsOnNewProject=false});
  await change(()=>{localStorage.setItem('ocv.studio.creation_preferences.v1',JSON.stringify({dynamic_text_mode:'text_assisted'}));studio.newProject('dynamic')});
  assert.equal(await page.evaluate(()=>studio.form.dynamic_text_mode),'text_assisted');
  await change(()=>{studio.activeJob={id:'old-task',status:'completed',request:{dynamic_video:true,script:'旧任务文案'}};studio.studioKind='dynamic'});
  await change(()=>studio.duplicateStudioProject());
  assert.equal(await page.evaluate(()=>studio.form.dynamic_text_mode),'text_assisted');
  await change(()=>{studio.studioDrawer='';studio.studioPage='videos'});
  await page.waitForSelector('.video-start');
  assert.equal(await page.$eval('.video-start .dynamic-text-mode-row button.active',el=>el.textContent),'画面优先');
  await page.select('.video-start select','source');
  await click('新建任务并导入配音字幕');
  assert.equal(posts.at(-1).body.dynamic_text_mode,'visual_first');
  assert.equal(posts.at(-1).body.scene_references_enabled,true);
  await page.evaluate(()=>[...document.querySelectorAll('.video-stage-nav button')].find(el=>el.textContent.includes('参数回顾')).click());
  assert.ok(await page.$eval('.parameter-review .setting-summaries',el=>el.textContent.includes('画面优先')));
  await change(()=>{studio.studioPage='home'}); await change(()=>{studio.studioPage='videos'});
  await page.waitForSelector('.video-record');
  await page.click('.video-record');
  await page.waitForSelector('.video-stage-nav');
  await page.evaluate(()=>[...document.querySelectorAll('.video-stage-nav button')].find(el=>el.textContent.includes('参数回顾')).click());
  await page.evaluate(()=>[...document.querySelectorAll('.parameter-review .setting-summaries button')].find(el=>el.textContent.includes('画面编排')).click());
  assert.ok(await page.$eval('.parameter-review',el=>el.textContent.includes('文字辅助')));
  await click('以此配置新建');
  assert.equal(await page.evaluate(()=>studio.form.dynamic_text_mode),'text_assisted');
  await change(()=>{studio.studioPage='videos'});
  await page.$eval('.video-srt-import',el=>el.open=true);
  await page.$eval('.video-srt-import textarea',el=>{el.value='1\n00:00:00,000 --> 00:00:02,000\n字幕测试';el.dispatchEvent(new Event('input',{bubbles:true}))});
  await click('创建草案');
  assert.equal(posts.at(-1).body.dynamic_text_mode,'visual_first');
  await change(()=>{studio.restoreDefaultsOnNewProject=true;studio.newProject('dynamic');studio.studioDrawer='画面编排'});
  await page.setViewport({width:680,height:1000});
  await page.screenshot({path:path.join(qaDirectory,'dynamic-text-mode-mobile.png'),fullPage:true});
  assert.equal(await page.$eval('.dynamic-text-mode-row',el=>el.scrollWidth>el.clientWidth+2),false);
  assert.deepEqual(errors,[]);
  assert.ok(posts.every(post=>['/api/video-studio','/api/video-studio/from-project'].includes(post.path)));
  console.log('Dynamic mode UI passed: default/toggle, graphic isolation, scenes, historical draft/task/snapshot, preferences, import/SRT payloads and narrow layout. No real API calls.');
 } finally { await browser.close(); }
}
main().catch(error=>{console.error(error);process.exitCode=1});
