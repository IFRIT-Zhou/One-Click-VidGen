const puppeteer=require('../../node_modules/puppeteer-core');
const assert=require('node:assert/strict');
(async()=>{const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true,args:['--disable-gpu']});try{
 for(const stage of ['storyboard','motions']){
  const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.evaluateOnNewDocument(()=>{
   window.audioPlays=[];window.audioPauses=0;
   HTMLMediaElement.prototype.play=function(){window.audioPlays.push({src:this.src,time:this.currentTime});return Promise.resolve()};
   HTMLMediaElement.prototype.pause=function(){window.audioPauses++};
  });
  await page.goto('http://127.0.0.1:5173/tests/static-to-motion.html?structure&'+stage);
  await page.waitForSelector('.structure-toolbar');
  await page.evaluate(()=>[...document.querySelectorAll('.structure-toolbar button')].find(b=>b.textContent.includes('拆分')).click());
  await page.waitForSelector('[role="dialog"]');
  assert.match(await page.$eval('[role="dialog"]',e=>e.textContent),/前半句/);
  assert.match(await page.$eval('[role="dialog"]',e=>e.textContent),/后半句/);
  assert.equal(await page.$eval('[role="dialog"] input[type="range"]',e=>e.max),'1');
  await page.evaluate(()=>[...document.querySelectorAll('[role="dialog"] button')].find(b=>b.textContent.includes('试听后段开头')).click());
  await page.waitForFunction(()=>window.audioPlays.length===1);
  const playback=await page.evaluate(()=>window.audioPlays[0]);
  assert.equal(playback.time,4);assert.match(playback.src,/audio/);
  assert.doesNotMatch(await page.$eval('[role="dialog"]',e=>e.textContent),/没有可试听的配音/);
  await page.evaluate(()=>[...document.querySelectorAll('[role="dialog"] button')].find(b=>b.textContent==='关闭').click());
  await page.waitForFunction(()=>!document.querySelector('[role="dialog"]'));
  assert.ok(await page.evaluate(()=>window.audioPauses>0));
  assert.equal(await page.evaluate(()=>window.requests.length),0);
  assert.deepEqual(errors,[]);await page.close();
 }
 console.log('PASS: shared split dialog opens from storyboard and motion pages without submitting generation');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
