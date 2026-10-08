const puppeteer = require('../../node_modules/puppeteer-core');
const assert = require('node:assert/strict');
const path = require('node:path');
async function main(){
 const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true,args:['--no-sandbox','--disable-gpu']});
 try{
  const page=await browser.newPage();await page.setViewport({width:1000,height:1600});
  await page.setRequestInterception(true);
  page.on('request',r=>r.url().endsWith('/api/subtitle-fonts')?r.respond({status:200,contentType:'application/json',body:JSON.stringify({fonts:['Microsoft YaHei','Arial']})}):r.continue());
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.goto('http://127.0.0.1:5197/tests/subtitle-style.html');
  await page.waitForSelector('.edition-options input');
  assert.deepEqual(await page.$$eval('.edition-options input',els=>els.map(e=>e.checked)),[true,true]);
  await page.click('.edition-options label:first-child input');
  assert.equal(await page.$eval('fieldset',e=>e.disabled),true);
  assert.equal(await page.$eval('.edition-options label:last-child input',e=>e.disabled),true);
  await page.click('.edition-options label:first-child input');
  assert.equal(await page.$eval('fieldset',e=>e.disabled),false);
  await page.$eval('.style-fields input[type=number]',e=>{e.value=62;e.dispatchEvent(new Event('change',{bubbles:true}))});
  await page.select('.orientation-field select','landscape');
  assert.equal(await page.$eval('.style-fields input[type=number]',e=>e.value),'36');
  await page.select('.orientation-field select','portrait');
  assert.equal(await page.$eval('.style-fields input[type=number]',e=>e.value),'62');
  await page.select('.orientation-field select','landscape');
  await page.$eval('.subtitle-preview',e=>e.scrollIntoView({block:'center',behavior:'instant'}));
  const box=await page.$eval('.subtitle-preview svg',e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}});
  await page.mouse.move(box.x+box.w*.5,box.y+box.h*.5);await page.mouse.down();await page.mouse.move(box.x+box.w*.6,box.y+box.h*.4,{steps:5});await page.mouse.up();
  const placed=await page.evaluate(()=>JSON.parse(JSON.stringify(window.testSettings.subtitle_layouts.landscape)));
  assert.ok(Math.abs(placed.frame_x-60)<1,JSON.stringify({placed,errors}));assert.ok(Math.abs(placed.frame_y-40)<1);
  await page.$eval('input[aria-label="画面缩放"]',e=>{e.value=80;e.dispatchEvent(new Event('input',{bubbles:true}))});
  assert.equal(await page.evaluate(()=>window.testSettings.subtitle_layouts.landscape.frame_scale),.8);
  await page.click('.layout-target button:last-child');
  await page.$eval('.subtitle-preview',e=>e.scrollIntoView({block:'center',behavior:'instant'}));
  const subbox=await page.$eval('.subtitle-preview svg',e=>{const r=e.getBoundingClientRect();return {x:r.x,y:r.y,w:r.width,h:r.height}});
  await page.mouse.move(subbox.x+subbox.w*.5,subbox.y+subbox.h*.7);await page.mouse.down();await page.mouse.move(subbox.x+subbox.w*.4,subbox.y+subbox.h*.6,{steps:5});await page.mouse.up();
  const sub=await page.evaluate(()=>JSON.parse(JSON.stringify(window.testSettings.subtitle_layouts.landscape)));
  assert.ok(Math.abs(sub.x_position-40)<1);assert.ok(Math.abs(sub.position-85)<1);
  await page.click('.edition-options label:first-child input');
  assert.equal(await page.$eval('input[aria-label="画面缩放"]',e=>e.disabled),false);
  await page.click('.edition-options label:first-child input');
  const overflow=await page.$eval('.subtitle-design',e=>e.scrollWidth>e.clientWidth+1);
  assert.equal(overflow,false);assert.deepEqual(errors,[]);
  await page.screenshot({path:path.resolve(__dirname,'../../runtime_logs/subtitle_preview/panel.png'),fullPage:true});
  console.log('UI: versions, locking, separate layouts, frame dragging/scaling, caption dragging, raw layout editing, width and runtime errors passed');
 }finally{await browser.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1});
