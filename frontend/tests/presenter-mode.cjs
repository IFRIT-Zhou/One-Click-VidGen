const puppeteer=require('../../node_modules/puppeteer-core');
const assert=require('node:assert/strict');
(async()=>{const browser=await puppeteer.launch({executablePath:process.argv[2],headless:true,args:['--disable-gpu']});try{
 const page=await browser.newPage(),errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:5173/tests/presenter-mode.html');
 await page.waitForSelector('.presenter-settings input');
 await page.click('.presenter-settings input');
 await page.select('.presenter-settings select','person');
 assert.deepEqual(await page.evaluate(()=>[window.form.presenter_mode,window.form.presenter_reference_id]),[true,'person']);
 await page.waitForSelector('.shot-audio-settings');
 await page.evaluate(()=>{
  const label=[...document.querySelectorAll('label')].find(l=>l.textContent.includes('本镜讲解员出镜开口'));
  label.querySelector('input').click();
 });
 assert.equal(await page.evaluate(()=>document.querySelector('label:has(input)').textContent.length>0),true);
 const buttons=await page.evaluate(()=>[...document.querySelectorAll('button')].map(b=>b.textContent.trim()));
 assert.equal(await page.evaluate(()=>[...document.querySelectorAll('label')].find(l=>l.textContent.includes('本镜讲解员出镜开口')).querySelector('input').checked),true);
 await page.evaluate(()=>{
  const label=[...document.querySelectorAll('label')].find(l=>l.textContent.trim()==='人物对口型');label.querySelector('input').click();
 });
 assert.equal(await page.evaluate(()=>[...document.querySelectorAll('label')].find(l=>l.textContent.trim()==='人物对口型').querySelector('input').checked),false);
 await page.evaluate(()=>[...document.querySelectorAll('button')].find(b=>b.textContent==='保存讲解设置').click());
 await page.waitForFunction(()=>window.requests.length===1);
 const request=(await page.evaluate(()=>window.requests))[0];
 assert.equal(request.body.presenter_speaking,true);
 assert.equal(request.body.reference_audio_lipsync,false);
 assert.ok(request.path.endsWith('/motion'));
 assert.deepEqual(errors,[]);
 console.log('PASS: presenter selector and per-shot controls; no automatic generation or billing');
}finally{await browser.close()}})().catch(e=>{console.error(e);process.exitCode=1});
