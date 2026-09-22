// Isolated browser smoke test. Every API/media request is intercepted; no paid call is possible.
// Start Vite on port 5199, then run: node frontend/tests/video-generation.cjs "<browser executable>"
const puppeteer = require('../../node_modules/puppeteer-core');
const assert = require('node:assert/strict');
const image = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII=', 'base64');
const shot = (id, extra = {}) => ({id,kind:'video',start:0,end:5,duration:5,generation_duration:5,slide_ids:['1'],intent:'提问与回应',image_status:'completed',image_prompt:'核心画面',video_prompt:'图1为核心分镜，观众举手提问。',...extra});
const project = {id:'ui-test',revision:1,status:'video_generation_ready',audio:true,settings:{name:'动态阶段测试',ratio:'16:9'},scenes:[{slide_id:'1',start:0,end:5,text:'测试字幕'}],logs:['核心图已确认'],shots:[shot('fresh'),shot('static',{kind:'static'}),shot('done',{video_status:'completed',video_version:'v1'}),shot('failed',{video_status:'failed',video_terminal:true}),shot('query',{video_status:'unknown',video_resume_available:true,video_task_id:'cloud-123'}),shot('unknown',{video_status:'unknown',video_resume_available:false}),shot('paused',{video_status:'stopped',video_resume_available:true,video_not_submitted:true})]};
let model={base_url:'https://api.example.test',submit_path:'/openapi/v2/example/multimodal-video',query_path:'/openapi/v2/query',upload_path:'/openapi/v2/media/upload/binary',resolution:'720p',has_api_key:true,key_count:2,effective_concurrency:2,source:'dedicated',model_label:'多模态视频'};
const posts=[],dialogs=[],errors=[];
async function main(){
 console.log('Starting isolated browser…');
 const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true,pipe:true});
 try{
  console.log('Browser ready; all API requests will be mocked.');
  const page=await browser.newPage();await page.setViewport({width:1440,height:1100});
  page.on('pageerror',e=>errors.push(e.message));
  page.on('dialog',async dialog=>{dialogs.push(dialog.message());await dialog.accept()});
  await page.setRequestInterception(true);
  page.on('request',async request=>{
   const url=new URL(request.url()),pathname=url.pathname;
   const json=value=>request.respond({status:200,contentType:'application/json',body:JSON.stringify(value)});
   if(pathname==='/api/video-model'){
    if(request.method()==='PUT'){posts.push({path:pathname,body:JSON.parse(request.postData())});model={...model,source:'dedicated',has_api_key:true}}
    return json(model);
   }
   if(pathname.startsWith('/api/video-studio')){
    if(/\/images\//.test(pathname))return request.respond({status:200,contentType:'image/png',body:image});
    if(/\/videos\/done$/.test(pathname))return request.respond({status:200,contentType:'video/mp4',body:Buffer.alloc(0)});
    if(/\/audio$/.test(pathname))return request.respond({status:200,contentType:'audio/wav',body:Buffer.alloc(44)});
    if(request.method()==='POST'){posts.push({path:pathname,body:JSON.parse(request.postData()||'{}')});project.revision++;project.status='video_review'}
    return json(pathname==='/api/video-studio'?{items:[project]}:project);
   }
   if(url.hostname!=='127.0.0.1')return request.abort();
   return request.continue();
  });
  const click=async text=>{await page.waitForFunction(value=>[...document.querySelectorAll('button')].some(el=>el.textContent.trim()===value&&!el.disabled),{},text);await page.evaluate(value=>[...document.querySelectorAll('button')].find(el=>el.textContent.trim()===value).click(),text);await page.waitForNetworkIdle({idleTime:80,timeout:5000})};
  const select=async index=>page.$$eval('.motion-editor aside button',(els,i)=>els[i].click(),index);
  await page.goto('http://127.0.0.1:5199/tests/video-generation.html');
  console.log('Mounted page, checking stage and actions…');
  await page.waitForSelector('.motion-workspace');
  assert.equal(posts.length,0,'Opening a confirmed project must never submit generation or save credentials');
  assert.equal(await page.$eval('.video-stage-nav .active b',el=>el.textContent),'动态镜头');
  assert.equal(await page.$eval('.motion-generation-bar button',el=>el.disabled),true);
  await click('试生成当前镜头');
  await page.waitForFunction(()=>document.querySelector('.motion-workspace'));
  assert.deepEqual(posts.at(-1).body.shot_ids,['fresh']);assert.equal(posts.at(-1).body.retry_failed,false);
  assert.ok(dialogs.at(-1).includes('新的付费生成'));
  await click('生成全部未完成（3）');
  assert.deepEqual(posts.at(-1).body.shot_ids,['fresh','query','paused']);
  await select(4);await click('继续查询原任务');
  assert.ok(dialogs.at(-1).includes('查询原任务，不重新提交'));
  await select(6);await click('继续生成（尚未提交）');
  assert.ok(dialogs.at(-1).includes('继续首次付费生成'));
  await select(3);await click('重新付费生成本镜');
  assert.equal(posts.at(-1).body.retry_failed,true);assert.ok(dialogs.at(-1).includes('再次扣费'));
  await select(5);assert.equal(await page.$('.motion-shot-actions button'),null);
  await select(1);assert.equal(await page.$('.motion-shot-actions'),null);
  await select(2);await page.waitForSelector('.motion-preview video');
  assert.equal(await page.$eval('.motion-preview video',el=>el.muted),true);
  assert.ok(await page.$('.motion-narration-audio'));
  assert.equal(await page.$eval('.motion-audio-toggle input',el=>el.checked),true);
  assert.equal(await page.$eval('.motion-heading-actions button',el=>el.textContent.trim()),'重新编辑本镜');
  assert.ok(await page.$('.motion-download'));
  await click('查看已确认分镜');
  assert.equal(await page.$$eval('.video-header-actions button',els=>els.some(el=>/规划/.test(el.textContent))),false);
  assert.equal(await page.$eval('.video-detail input',el=>el.disabled),true);
  await page.setViewport({width:680,height:1000});
  await click('查看动态镜头');
  assert.equal(await page.$eval('.motion-workspace',el=>el.scrollWidth>el.clientWidth+2),false);
  assert.deepEqual(errors,[]);
  console.log('Video UI: global credentials summary, no auto-generation, safe batch, resume/retry, static/media review and narrow layout passed.');
 }finally{await browser.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1});
