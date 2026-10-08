const puppeteer=require('../../node_modules/puppeteer-core');
const assert=require('node:assert/strict');
(async()=>{const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true,args:['--disable-gpu'],timeout:15000});try{
const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:5173/tests/model-install.html');await page.waitForSelector('.model-guide.missing');
assert.equal(await page.$$eval('.model-guide',x=>x.length),2);
await page.click('.model-guide button');await page.waitForSelector('.model-item');
assert.match(await page.$eval('.model-item',x=>x.innerText),/待补齐/);
await page.click('.model-item .file-actions button');
assert.equal(await page.evaluate(()=>window.actions[0].path),'vae/a.safetensors');
await page.setViewport({width:390,height:844});
assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth),true);
assert.deepEqual(errors,[]);console.log('PASS: inline missing status, model actions, TTS guide and mobile layout');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
