const puppeteer=require('../../node_modules/puppeteer-core');
const assert=require('node:assert/strict');
(async()=>{const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true,args:['--disable-gpu'],timeout:15000});try{
const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:5198/tests/scene-editor.html');await page.waitForSelector('.navigator-list button');
assert.match(await page.$eval('.navigator-list button',e=>e.innerText),/场景 1/);await page.click('.navigator-list button');
assert.equal(await page.$$eval('.scene-reference-assets',es=>es.length),0);
assert.equal(await page.$$eval('.video-detail',es=>es.filter(e=>getComputedStyle(e).display!=='none').length),1);
assert.match(await page.$$eval('.video-detail',es=>es.find(e=>getComputedStyle(e).display!=='none').innerText),/场景图提示词/);
await page.click('.navigator-list button:nth-child(2)');assert.match(await page.$$eval('.video-detail',es=>es.find(e=>getComputedStyle(e).display!=='none').innerText),/测试字幕/);
assert.deepEqual(errors,[]);console.log('PASS: scene and shot share editor; no standalone panel; selection renders without errors');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
