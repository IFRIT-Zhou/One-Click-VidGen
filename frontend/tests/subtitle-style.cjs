const puppeteer = require('../../node_modules/puppeteer-core');
const assert = require('node:assert/strict');
const path = require('node:path');
async function main(){
 const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true});
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
  const overflow=await page.$eval('.subtitle-design',e=>e.scrollWidth>e.clientWidth+1);
  assert.equal(overflow,false);assert.deepEqual(errors,[]);
  await page.screenshot({path:path.resolve(__dirname,'../../runtime_logs/subtitle_preview/panel.png'),fullPage:true});
  console.log('UI: versions, locking, separate layouts, width and runtime errors passed');
 }finally{await browser.close()}
}
main().catch(e=>{console.error(e);process.exitCode=1});
