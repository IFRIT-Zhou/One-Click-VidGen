// Real Vue UI, mocked read-only catalog; no OCV project or paid API.
import assert from 'node:assert/strict'
import {existsSync,mkdirSync,readFileSync} from 'node:fs'
import {createHash} from 'node:crypto'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {createServer} from 'vite'
import vue from '@vitejs/plugin-vue'
import puppeteer from 'puppeteer-core'
const root=fileURLToPath(new URL('../',import.meta.url))
const galleryRoot=path.resolve(root,'../tools/vibe_remotion/gallery')
const manifestBytes=readFileSync(path.join(galleryRoot,'manifest.json'))
const manifest=JSON.parse(manifestBytes),versionKey=createHash('sha256').update(manifestBytes).digest('hex').slice(0,16)
const chrome=[process.argv[2],process.env.CHROME_BIN,'C:/Program Files/Google/Chrome/Application/chrome.exe','C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe','/usr/bin/chromium'].find(p=>p&&existsSync(p))
assert.ok(chrome,'Chromium required')
const server=await createServer({root,configFile:false,logLevel:'error',cacheDir:'node_modules/.cache/shotcraft-picker-ui',plugins:[vue()],optimizeDeps:{entries:['tests/shotcraft-template-picker.html']},server:{host:'127.0.0.1',port:0,hmr:false}})
let browser
try{
  await server.listen();const origin='http://127.0.0.1:'+server.httpServer.address().port
  browser=await puppeteer.launch({executablePath:chrome,headless:true,args:['--disable-gpu']})
  const page=await browser.newPage(),errors=[]
  await page.evaluateOnNewDocument(gallery=>{window.__gallery=gallery},Object.entries(manifest.styles).map(([id,row])=>({name:row.name,preview_url:`/api/vibe-templates/${id}/preview?v=${versionKey}`,poster_url:`/api/vibe-templates/${id}/poster?v=${versionKey}`})))
  await page.setViewport({width:1440,height:1100})
  page.on('pageerror',error=>errors.push(String(error)))
  await page.setRequestInterception(true)
  page.on('request',request=>{
    const url=new URL(request.url())
    if(url.origin!==origin)return request.abort()
    if(url.pathname.startsWith('/api/vibe-templates/')){
      const match=url.pathname.match(/^\/api\/vibe-templates\/([a-z0-9-]+)\/(preview|poster)$/),row=manifest.styles[match?.[1]],asset=match?.[2]
      assert.ok(row&&asset,'test may only read bundled gallery media')
      return request.respond({status:200,contentType:asset==='preview'?'image/gif':'image/jpeg',body:readFileSync(path.join(galleryRoot,row[asset]))})
    }
    request.continue()
  })
  const card=id=>`[data-template-id="style-${id}"]`
  const clickText=text=>page.$eval('.shotcraft-picker',(el,text)=>[...el.querySelectorAll('button')].find(button=>button.textContent.trim()===text).click(),text)
  await page.goto(origin+'/tests/shotcraft-template-picker.html',{waitUntil:'networkidle0'})
  await page.waitForFunction(()=>document.querySelectorAll('.shotcraft-template-card').length===12)
  assert.equal(await page.$eval('.shotcraft-template-grid',el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),4)
  assert.equal(await page.evaluate(()=>fixture.calls.filter(call=>call.method!=='GET').length),0)
  assert.equal(await page.evaluate(()=>fixture.state.meta.valid),true)
  await page.hover(card(7))
  await page.waitForSelector(card(7)+' [role=tooltip]')
  assert.match(await page.$eval(card(7)+' [role=tooltip]',el=>el.textContent),/人物／机构介绍.*单图入画.*至少 4 秒/s)
  assert.ok(await page.$eval(card(7)+' img',el=>new URL(el.src).pathname.endsWith('/preview')))
  assert.ok(await page.$$eval('.shotcraft-template-card img',els=>els.filter(el=>new URL(el.src).pathname.endsWith('/preview')).length)<=4)
  await page.click(card(0));await page.click(card(1));await page.click(card(2));await page.click(card(3))
  assert.deepEqual(await page.evaluate(()=>[...fixture.state.selected]),['style-0','style-1','style-2'])
  assert.match(await page.$eval('.shotcraft-picker-warning',el=>el.textContent),/最多选择 3 款/)
  await page.click(card(1))
  assert.deepEqual(await page.evaluate(()=>[...fixture.state.selected]),['style-0','style-2'])
  assert.equal(await page.$eval(card(0),el=>el.getAttribute('aria-pressed')),'true')
  await clickText('让 AI 自动选择')
  assert.deepEqual(await page.evaluate(()=>[...fixture.state.selected]),[])
  await page.click(card(11))
  assert.deepEqual(await page.evaluate(()=>[...fixture.state.selected]),[])
  assert.match(await page.$eval('.shotcraft-picker-warning',el=>el.textContent),/本地模板组件缺失/)
  await page.evaluate(()=>fixture.state.disabled=true)
  await page.click(card(0))
  assert.deepEqual(await page.evaluate(()=>[...fixture.state.selected]),[])
  await page.evaluate(()=>{fixture.state.disabled=false;fixture.state.duration=3})
  await page.click(card(0))
  assert.equal(await page.evaluate(()=>fixture.state.meta.valid),false)
  assert.match(await page.$eval('.shotcraft-picker-warning',el=>el.textContent),/至少需要 4 秒/)
  await clickText('让 AI 自动选择')
  await page.evaluate(()=>fixture.state.duration=13)
  await page.click(card(2))
  assert.match(await page.$eval('.shotcraft-picker-warning',el=>el.textContent),/搭配另一款/)
  await page.click(card(0));assert.equal(await page.evaluate(()=>fixture.state.meta.valid),true)
  await clickText('暂停示意')
  assert.equal(await page.$$eval('.shotcraft-template-card img',els=>els.filter(el=>new URL(el.src).pathname.endsWith('/preview')).length),0)
  await page.focus(card(4));await page.waitForSelector(card(4)+' [role=tooltip]')
  await page.keyboard.press('Escape');assert.equal(await page.$(card(4)+' [role=tooltip]'),null)
  await page.focus(card(5));await page.waitForSelector(card(5)+' [role=tooltip]')
  if(process.env.SHOTCRAFT_UI_SCREENSHOT_DIR){mkdirSync(process.env.SHOTCRAFT_UI_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:path.join(process.env.SHOTCRAFT_UI_SCREENSHOT_DIR,'picker-desktop.png'),fullPage:true})}
  await page.setViewport({width:420,height:850})
  await page.mouse.move(0,0);await page.evaluate(()=>document.activeElement?.blur())
  assert.equal(await page.$eval('.shotcraft-template-grid',el=>getComputedStyle(el).gridTemplateColumns.split(' ').length),2)
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth+1),'mobile must not overflow')
  if(process.env.SHOTCRAFT_UI_SCREENSHOT_DIR)await page.screenshot({path:path.join(process.env.SHOTCRAFT_UI_SCREENSHOT_DIR,'picker-mobile.png'),fullPage:true})
  await page.evaluate(()=>fixture.state.visible=false)
  await page.waitForFunction(()=>!document.querySelector('.shotcraft-picker'))
  await page.emulateMediaFeatures([{name:'prefers-reduced-motion',value:'reduce'}])
  await page.evaluate(()=>{fixture.state.visible=true;fixture.state.selected=[]})
  await page.waitForFunction(()=>document.querySelectorAll('.shotcraft-template-card').length===12)
  assert.equal(await page.$eval('.shotcraft-motion-toggle',el=>el.textContent),'播放示意')
  assert.equal(await page.$$eval('.shotcraft-template-card img',els=>els.filter(el=>new URL(el.src).pathname.endsWith('/preview')).length),0)
  await page.evaluate(()=>fixture.state.visible=false);await page.waitForFunction(()=>!document.querySelector('.shotcraft-picker'))
  await page.evaluate(()=>{fixture.state.fail=true;fixture.state.visible=true})
  await page.waitForSelector('.shotcraft-picker-error')
  assert.match(await page.$eval('.shotcraft-picker-error',el=>el.textContent),/仍可使用自动选择/)
  assert.equal(await page.evaluate(()=>fixture.state.meta.valid),true)
  await page.evaluate(()=>fixture.state.fail=false);await clickText('重试读取')
  await page.waitForFunction(()=>document.querySelectorAll('.shotcraft-template-card').length===12)
  assert.equal(await page.$('.shotcraft-picker-error'),null)
  assert.deepEqual(errors,[])
  console.log('Shotcraft picker browser checks passed: 12 cards, semantic tooltips, manual/auto choice, timing, unavailable states, pause, reduced motion, keyboard, mobile, retry.')
}finally{await browser?.close();await server.close()}
