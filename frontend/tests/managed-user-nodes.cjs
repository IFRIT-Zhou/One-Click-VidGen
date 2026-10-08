const puppeteer=require('../../node_modules/puppeteer-core');
const assert=require('node:assert/strict');
(async()=>{const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true,args:['--disable-gpu']});try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5198/tests/managed-user-nodes.html');
 await page.waitForSelector('.user-nodes');await page.click('.user-nodes summary');
 await page.waitForFunction(()=>document.body.innerText.includes('SelfLiftH3Sampler'));
 assert.match(await page.$eval('.user-nodes',e=>e.innerText),/高速采样：所需节点已加载/);
 await page.evaluate(()=>[...document.querySelectorAll('.user-nodes button')].find(e=>e.textContent==='打开节点目录').click());
 await page.waitForFunction(()=>window.calls.some(c=>c.path.endsWith('/nodes/open-folder')&&c.method==='POST'));
 assert.equal(await page.evaluate(()=>window.calls.some(c=>c.path.endsWith('/start'))),false);
 assert.deepEqual(errors,[]);console.log('PASS: user node directory, per-workflow load status and open-folder action');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
