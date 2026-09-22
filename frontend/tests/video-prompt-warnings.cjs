// Start Vite on 5199, then: node frontend/tests/video-prompt-warnings.cjs "<browser executable>"
// All API requests are mocked. No media generation or real task modification.
const puppeteer = require('../../node_modules/puppeteer-core');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const directory = path.resolve(__dirname, '../../.tmp_diagnostic_video_warning_levels');
const format = '提示词分节标题不完整或顺序不同，内容已保留；不影响继续生成。';
const shot = (id, notes=[])=>({id,kind:'video',start:0,end:5,duration:5,generation_duration:5,slide_ids:['1'],intent:'用动作与表情表达点餐犹豫',image_prompt:'人物与菜单组成核心画面。',video_prompt:'人物翻动菜单，犹豫后作出决定。',image_prompt_warnings:notes});
const record = {id:'ui-test',revision:1,status:'storyboard_review',settings:{name:'警告分级测试',ratio:'16:9'},scenes:[{slide_id:'1',start:0,end:5,text:'测试字幕'}],logs:[],shots:[shot('format',[format]),shot('info',['缺少既定主体名称：观众','缺少文字容器：对话气泡']),shot('warning',['缺少短文字原文：危险']),shot('mixed',[format,'缺少文字归属：观众','规划了画面短文字，却同时要求全面禁止文字'])]};
async function main(){
 fs.mkdirSync(directory,{recursive:true});
 const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true,pipe:true});
 const errors=[],writes=[];
 try{
  const page=await browser.newPage();await page.setViewport({width:1440,height:1100});
  page.on('pageerror',error=>errors.push(error.message));
  await page.setRequestInterception(true);
  page.on('request',request=>{
   const url=new URL(request.url());
   if(url.pathname.startsWith('/api/')){
    if(request.method()!=='GET')writes.push(url.pathname);
    let body=url.pathname==='/api/video-studio'?{items:[record]}:url.pathname==='/api/video-studio/ui-test'?record:{items:[]};
    return request.respond({status:200,contentType:'application/json',body:JSON.stringify(body)});
   }
   if(url.hostname!=='127.0.0.1')return request.abort();
   return request.continue();
  });
  await page.goto('http://127.0.0.1:5199/tests/video-generation.html');
  await page.waitForSelector('.video-editor');
  assert.equal(await page.$$eval('.prompt-warning-index button',els=>els.length),2);
  assert.equal(await page.$$eval('.video-editor aside button.warning',els=>els.length),2);
  assert.equal(await page.$('.current-shot-warning'),null,'Pure formatting must never ask user to review');
  await page.$$eval('.video-editor aside button',els=>els[1].click());
  await page.waitForSelector('.current-shot-warning');
  assert.equal(await page.$eval('.current-shot-warning',el=>el.open),false);
  assert.ok(await page.$eval('.current-shot-warning summary',el=>el.textContent.includes('措辞比对记录')));
  await page.$$eval('.prompt-warning-index button',els=>els[0].click());
  await page.waitForFunction(()=>document.querySelector('.current-shot-warning')?.open);
  assert.ok(await page.$eval('.current-shot-warning',el=>el.textContent.includes('缺少短文字原文')));
  await page.$$eval('.video-editor aside button',els=>els[3].click());
  assert.equal(await page.$eval('.current-shot-warning',el=>el.open),false,'Changing shot returns details to collapsed');
  assert.equal(await page.$$eval('.current-shot-warning .prompt-note-warning',els=>els.length),1);
  await page.screenshot({path:path.join(directory,'warning-tiers-desktop.png'),fullPage:true});
  await page.setViewport({width:680,height:1000});
  assert.equal(await page.$eval('.prompt-warning-index',el=>el.scrollWidth>el.clientWidth+2),false);
  await page.screenshot({path:path.join(directory,'warning-tiers-mobile.png'),fullPage:true});
  // A historical all-format task must become quiet solely by reading it.
  record.shots=Array.from({length:8},(_,i)=>shot('format-'+i,[format]));
  await page.reload();await page.waitForSelector('.video-editor');
  assert.equal(await page.$('.prompt-warning-index'),null);
  assert.equal(await page.$('.current-shot-warning'),null);
  assert.equal(await page.$$eval('.video-editor aside em',els=>els.length),0);
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
  console.log('Warning UI passed: historical 8-format task quiet, real warnings counted, lexical details collapsed, explicit jump opens details, mobile fits. No API writes.');
 }finally{await browser.close()}
}
main().catch(error=>{console.error(error);process.exitCode=1});
