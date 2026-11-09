// Runs the real VideoStudio + ShotVibePreview with synthetic shot data, on an
// ephemeral Vite server without the app proxy. Never connects to an OCV server.
// node frontend/tests/shot-vibe-preview.mjs [chrome executable]
import assert from 'node:assert/strict'
import {execFileSync} from 'node:child_process'
import {existsSync} from 'node:fs'
import path from 'node:path'
import {fileURLToPath} from 'node:url'
import {createServer} from 'vite'
import vue from '@vitejs/plugin-vue'
import puppeteer from 'puppeteer-core'

const root = fileURLToPath(new URL('../', import.meta.url))
const chrome = [process.argv[2], process.env.CHROME_BIN,
  'C:/Program Files/Google/Chrome/Application/chrome.exe',
  'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',
  '/usr/bin/google-chrome', '/usr/bin/chromium'].find(p => p && existsSync(p))
assert.ok(chrome, 'Specify a Chromium browser with H.264 playback support')
const bundledFFmpeg = path.resolve(root, '../tools/ffmpeg/bin/ffmpeg.exe')
const ffmpeg = process.env.FFMPEG || (existsSync(bundledFFmpeg) ? bundledFFmpeg : 'ffmpeg')
function makeClip(audio) {
  return execFileSync(ffmpeg, ['-v','error','-f','lavfi','-i','color=c=teal:s=48x32:r=10:d=0.8',
    ...(audio ? ['-f','lavfi','-i','sine=frequency=330:duration=0.8','-c:a','aac'] : ['-an']),
    '-c:v','libx264','-pix_fmt','yuv420p','-movflags','empty_moov+frag_keyframe','-f','mp4','pipe:1'], {windowsHide:true})
}
const clips = {preview:makeClip(true), silent:makeClip(false)}
const mediaRequests = []
const apiPrefix = '/api/video-studio/result-fixture'
const mediaPath = /^\/api\/video-studio\/result-fixture\/vibe-previews\/[a-f0-9]{32}\/(preview|silent)$/
const server = await createServer({root, configFile:false, cacheDir:'node_modules/.cache/shot-vibe-preview',
  logLevel:'error', optimizeDeps:{entries:['tests/shot-result-overview.html']},
  server:{host:'127.0.0.1',port:0,hmr:false}, plugins:[
  vue(), {name:'isolated-vibe-test-backend',transformIndexHtml:{order:'pre',handler(html,ctx){
    if(ctx.path!=='/tests/shot-result-overview.html')return
    // Reuse the normal fixture, but emulate the stage immediately after storyboard planning.
    const marker="sessionStorage.setItem('ocv-video-open',record.id)"
    assert.ok(html.includes(marker))
    return html.replace(marker,`if(new URLSearchParams(location.search).has('early-vibe')){
      record.status='storyboard_review'
      Object.assign(record.shots[0],{image:'',image_status:'pending',video:'',video_status:'pending'})
    }
    `+marker)
  }},configureServer(s) { s.middlewares.use((req,res,next)=>{
    const url = req.url.split('?')[0]
    if (!url.startsWith('/api/')) return next()
    const kind = url.match(mediaPath)?.[1]
    if (!kind) { res.writeHead(404);return res.end() }
    mediaRequests.push(url)
    res.writeHead(200,{'Content-Type':'video/mp4','Content-Length':clips[kind].length});res.end(clips[kind])
  }) }}
]})

let browser
try {
  await server.listen()
  const origin = 'http://127.0.0.1:'+server.httpServer.address().port
  browser = await puppeteer.launch({executablePath:chrome,headless:true,args:['--disable-gpu','--autoplay-policy=no-user-gesture-required']})
  const page = await browser.newPage()
  const errors = []
  page.on('pageerror',e=>errors.push(String(e)))
  // Also block all off-origin requests, even if a server link is malformed.
  await page.setRequestInterception(true)
  page.on('request',r=>new URL(r.url()).origin===origin?r.continue():r.abort())
  await page.goto(origin+'/tests/shot-result-overview.html?references=1',{waitUntil:'networkidle0'})
  const panel = '.vibe-preview', video = panel+' video', link = panel+' a[download]'
  const select = '.navigator-list button[data-shot-index="1"]'
  await page.waitForSelector(panel,{hidden:true})
  await page.$eval('.production-tabs',el=>[...el.querySelectorAll('button')].find(b=>b.textContent==='视频生成与结果').click())
  await page.waitForSelector(panel,{visible:true})
  assert.ok(await page.$('.motion-detail .vibe-preview'),'motion module belongs to video tab')
  assert.equal(await page.$eval(panel,e=>e.open),false)
  assert.equal(await page.evaluate(()=>window.fixture.calls.some(r=>r.url.includes('/vibe-preview'))),false,'mount is read-free')
  await page.evaluate(()=>{
    const otherFetch=window.fetch
    window.previewMock={calls:[],pending:[]}
    window.fetch=(url,options={})=>{
      if(!String(url).includes('/vibe-preview'))return otherFetch(url,options)
      window.previewMock.calls.push({method:options.method||'GET',url:String(url),body:options.body?JSON.parse(options.body):null})
      return new Promise(resolve=>window.previewMock.pending.push((data,status=200)=>resolve(new Response(JSON.stringify(data),{status}))))
    }
  })
  const count=()=>page.evaluate(()=>window.previewMock.calls.length)
  const waitCount=n=>page.waitForFunction(n=>window.previewMock.calls.length===n,{},n)
  const respond=(data,index=0,status=200)=>page.evaluate((data,i,status)=>window.previewMock.pending.splice(i,1)[0](data,status),data,index,status)
  const clickButton=(label)=>page.$eval(panel,(p,label)=>[...p.querySelectorAll('button')].find(b=>b.textContent.trim()===label).click(),label)
  const choose=text=>page.$eval(panel+' input[type=file]',(el,text)=>{
    const files=new DataTransfer();files.items.add(new File([text],'test-plan.json',{type:'application/json'}))
    el.files=files.files;el.dispatchEvent(new Event('change',{bubbles:true}))
  },text)
  const first='1'.repeat(32),second='2'.repeat(32),obsolete='3'.repeat(32)
  await page.click(panel+'>summary')
  await waitCount(1)
  await respond({available:true,preview_id:first,preview_url:'https://wrong.invalid/external.mp4'})
  await page.waitForFunction(sel=>document.querySelector(sel)?.readyState>=2,{},video)
  assert.equal(await page.$eval(video,e=>e.getAttribute('src')),apiPrefix+'/vibe-previews/'+first+'/preview')
  await page.$eval(video,async e=>{e.muted=true;await e.play()})
  await page.waitForFunction(sel=>document.querySelector(sel).currentTime>0,{},video)
  assert.equal(await page.$eval(link,e=>e.getAttribute('href')),apiPrefix+'/vibe-previews/'+first+'/silent')
  assert.equal(await page.$eval(link,e=>e.download),'镜头1-无声动效.mp4')
  const cdp=await page.createCDPSession()
  await cdp.send('Browser.setDownloadBehavior',{behavior:'deny',eventsEnabled:true})
  const began=new Promise((resolve,reject)=>{
    const t=setTimeout(()=>reject(new Error('download did not start')),5000)
    cdp.once('Browser.downloadWillBegin',e=>{clearTimeout(t);resolve(e)})
  })
  await page.click(link)
  assert.equal((await began).suggestedFilename,'镜头1-无声动效.mp4')
  assert.ok(mediaRequests.some(u=>u.endsWith('/silent')))

  await page.click(panel+' .vibe-import>summary')
  const plan={pack_id:'a'.repeat(32),source_sha256:'b'.repeat(64),plan:{sequence:[{asset_id:'layer_01',cue:'s1'}]}}
  await choose(JSON.stringify({...plan,project_id:'wrong'}))
  await page.waitForSelector(panel+' [role=alert]')
  await choose(JSON.stringify(plan))
  await page.waitForFunction(()=>[...document.querySelectorAll('.vibe-preview button')].some(b=>b.textContent==='生成本镜预览'&&!b.disabled))
  assert.equal(await count(),1,'choosing a plan does not render')
  await clickButton('生成本镜预览')
  await waitCount(2)
  assert.deepEqual(await page.evaluate(()=>window.previewMock.calls[1]),{method:'POST',url:apiPrefix+'/shots/shot1/vibe-previews',body:plan})
  await respond({detail:'模拟：方案与当前核心图不匹配'},0,409)
  await page.waitForFunction(()=>document.querySelector('.vibe-preview [role=alert]')?.textContent.includes('不匹配'))
  assert.equal(await count(),2,'failure has no automatic retry')
  await clickButton('生成本镜预览')
  await waitCount(3)
  await respond({preview_id:second,cached:true})
  await page.waitForFunction(id=>document.querySelector('.vibe-preview video')?.src.includes(id),{},second)
  assert.ok((await page.$eval(panel+' [role=status]',p=>p.textContent)).includes('复用'))

  await clickButton('刷新预览')
  await waitCount(4) // shot 1 GET pending
  await page.click(select)
  await page.waitForFunction(()=>document.querySelector('.vibe-preview')?.open===false)
  assert.equal(await count(),4,'switch does not launch rendering or reads')
  await page.click(panel+'>summary');await waitCount(5) // shot 2 resolves first
  await respond({available:false,stale:true},1)
  await page.waitForFunction(()=>document.querySelector('.vibe-preview').textContent.includes('旧预览已收起'))
  await respond({available:true,preview_id:obsolete},0) // old shot must never appear
  await page.evaluate(()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))))
  assert.equal(await page.$(video),null)
  assert.equal(await page.$(link),null)
  assert.equal(await page.$eval(panel+' .vibe-import',e=>e.textContent.includes('test-plan.json')),false)
  assert.deepEqual(await page.evaluate(()=>window.previewMock.calls.filter(c=>c.method==='GET').map(c=>c.url)),
    ['shot1','shot1','shot2'].map(id=>apiPrefix+'/shots/'+id+'/vibe-preview/latest'))
  // Exercise the ordinary user's complete workflow with a real parent VideoStudio and its
  // own checked-in mock project. Only the Vibe responses change. No project/model service.
  await page.goto(origin+'/tests/shot-result-overview.html',{waitUntil:'networkidle0'})
  await page.waitForSelector(panel,{visible:true})
  const installWorkflowMock=()=>page.evaluate(async apiPrefix=>{
    const baseFetch=window.fetch
    const current=await (await baseFetch(apiPrefix)).json()
    window.previewMock={calls:[],pending:[],confirmations:[],answer:true,original:structuredClone(current),current}
    window.confirm=text=>{window.previewMock.confirmations.push(text);return window.previewMock.answer}
    window.fetch=(url,options={})=>{
      if(String(url)==='/api/vibe-model')return Promise.resolve(new Response(JSON.stringify({configured:true,model:'deepseek-flash',max_image_requests:4})))
      if(String(url).endsWith('/vibe-cloud-job/latest'))return Promise.resolve(new Response(JSON.stringify({available:false})))
      if(String(url).split('?')[0]===apiPrefix)return Promise.resolve(new Response(JSON.stringify(window.previewMock.current)))
      if(!/\/vibe-(?:design|preview|adopt|undo|intent|cloud-jobs)/.test(String(url)))return baseFetch(url,options)
      window.previewMock.calls.push({method:options.method||'GET',url:String(url),body:options.body?JSON.parse(options.body):null})
      return new Promise(resolve=>window.previewMock.pending.push((data,status=200)=>resolve(new Response(JSON.stringify(data),{status}))))
    }
  },apiPrefix)
  await installWorkflowMock()
  await page.click(panel+'>summary');await waitCount(1);await respond({available:false})
  const btnState=label=>page.$eval(panel,(p,label)=>[...p.querySelectorAll('button')].filter(b=>b.textContent.trim()===label).map(b=>!b.disabled),label)
  const batch=()=>page.$eval('.motion-batch-actions button',b=>({text:b.textContent.trim(),enabled:!b.disabled}))
  const requestAt=i=>page.evaluate(i=>window.previewMock.calls[i],i)
  const settle=()=>page.evaluate(()=>new Promise(ok=>requestAnimationFrame(()=>requestAnimationFrame(ok))))
  const confirms=()=>page.evaluate(()=>window.previewMock.confirmations)
  const answer=value=>page.evaluate(v=>window.previewMock.answer=v,value)
  const normal={text:'全部重新生成（1）',enabled:true}
  assert.deepEqual(await batch(),normal)
  await clickButton('自动设计并预览');await waitCount(2)
  assert.deepEqual((await requestAt(1)).body,{engine:'local',confirm_model:false,pace:'normal',instruction:''})
  assert.equal((await requestAt(1)).url,apiPrefix+'/shots/shot1/vibe-design')
  assert.equal((await confirms()).length,0,'free design needs no external consent')
  await respond({engine:'local',summary:'已有图层',warnings:[],preview_request:plan})
  await waitCount(3)
  assert.deepEqual(await requestAt(2),{method:'POST',url:apiPrefix+'/shots/shot1/vibe-previews',body:plan})
  await respond({preview_id:first,cached:false});await page.waitForSelector(video)
  await settle();assert.equal(await count(),3,'preview is not adoption')
  assert.deepEqual(await batch(),normal)
  assert.deepEqual(await btnState('采用到正式镜头'),[true])
  const engine=panel+' .vibe-designer select'
  assert.deepEqual(await page.$eval(engine,el=>[...el.options].map(e=>e.value)),['local','cloud','layered'])
  await page.select(engine,'cloud');await answer(false)
  await clickButton('重新设计并预览');await settle()
  assert.match((await confirms()).at(-1),/当前分镜图、生成本图时实际使用的参考图和最终字幕时间戳/)
  assert.match((await confirms()).at(-1),/选择已验证镜头模板.*不自动抠图、不调用图像模型/s)
  assert.equal(await count(),3,'canceled external image request cannot call design or spend')
  await answer(true);await clickButton('重新设计并预览');await waitCount(4)
  assert.equal((await requestAt(3)).url,apiPrefix+'/shots/shot1/vibe-cloud-jobs')
  assert.deepEqual((await requestAt(3)).body,{confirm_cloud:true,start_mode:'fresh',template_mode:'shotcraft',pace:'normal',instruction:'',max_image_requests:0})
  await respond({detail:'模型设计暂不可用'},0,502)
  await page.waitForSelector(panel+' [role=alert]')
  await settle();assert.equal(await count(),4,'model error does not silently rerender or adopt')
  assert.deepEqual(await batch(),normal)
  // New successful preview after an error, still unadopted until separately authorized.
  await page.select(engine,'local');await clickButton('重新设计并预览');await waitCount(5)
  await respond({engine:'local',summary:'已重新设计',warnings:[],preview_request:plan});await waitCount(6)
  await respond({preview_id:second,cached:false});await page.waitForFunction(id=>document.querySelector('.vibe-preview video')?.src.includes(id),{},second)
  await answer(false);await clickButton('采用到正式镜头');await settle();assert.equal(await count(),6)
  await answer(true);await clickButton('采用到正式镜头');await waitCount(7)
  assert.deepEqual(await requestAt(6),{method:'POST',url:apiPrefix+'/shots/shot1/vibe-adopt',body:{revision:1,preview_id:second}})
  await respond({detail:'本镜画面已更新'},0,409)
  await page.waitForFunction(()=>document.querySelector('.vibe-preview [role=alert]')?.textContent.includes('画面已更新'))
  assert.deepEqual(await btnState('撤回采用'),[]);assert.deepEqual(await batch(),normal)
  await clickButton('采用到正式镜头');await waitCount(8)
  const adopted=await page.evaluate(id=>{
    const c=structuredClone(window.previewMock.original);c.revision=2
    Object.assign(c.shots[0],{kind:'video',video:'assets/videos/shot1/clip.mp4',video_status:'completed',
      video_backend:'vibe',video_version:'2',video_request:{backend:'vibe',preview_id:id}})
    return window.previewMock.current=c
  },second)
  await respond(adopted)
  await page.waitForFunction(()=>document.querySelector('.vibe-preview summary')?.textContent.includes('已采用 Vibe'))
  assert.deepEqual(await batch(),{text:'全部重新生成（0）',enabled:false})
  assert.deepEqual(await btnState('撤回采用'),[true])
  await answer(false);await clickButton('撤回采用');await settle();assert.equal(await count(),8)
  await answer(true);await clickButton('撤回采用');await waitCount(9)
  assert.deepEqual(await requestAt(8),{method:'POST',url:apiPrefix+'/shots/shot1/vibe-undo',body:{revision:2,preview_id:second}})
  const restored=await page.evaluate(()=>{
    const c=structuredClone(window.previewMock.original);c.revision=3;return window.previewMock.current=c
  })
  await respond(restored)
  await page.waitForFunction(()=>!document.querySelector('.vibe-preview summary')?.textContent.includes('已采用 Vibe'))
  assert.deepEqual(await batch(),normal);assert.deepEqual(await btnState('撤回采用'),[])
  assert.deepEqual(await btnState('采用到正式镜头'),[true])

  const setReservation=(enabled,revision,autopilot=false)=>page.evaluate((enabled,revision,autopilot)=>{
    const c=structuredClone(window.previewMock.current)
    c.revision=revision;c.shots[0].vibe_reserved=enabled;c.creation_parameters.dynamic_auto_advance=autopilot
    return window.previewMock.current=c
  },enabled,revision,autopilot)
  const finishReserve=async(enabled,rev,autopilot=false)=>{
    const n=await count();await clickButton(enabled?'预留给 Vibe':'取消预留');await waitCount(n+1)
    assert.deepEqual(await requestAt(n),{method:'POST',url:apiPrefix+'/shots/shot1/vibe-intent',body:{revision:rev,enabled}})
    await respond(await setReservation(enabled,rev+1,autopilot))
    await page.waitForFunction(on=>document.querySelector('.vibe-preview summary')?.textContent.includes('已预留')===on,{},enabled)
  }
  const noGenerations=()=>page.evaluate(()=>window.fixture.calls.filter(r=>r.method!=='GET'))
  const nextExport='.video-next-stage button'
  assert.equal(await page.$eval(nextExport,b=>b.textContent.trim()),'下一步：合成与导出')
  await finishReserve(true,3,true);await settle()
  assert.deepEqual(await batch(),{text:'全部重新生成（0）',enabled:false})
  assert.equal(await page.$(nextExport),null,'even previously completed footage does not override an unfulfilled reservation')
  assert.deepEqual(await noGenerations(),[],'auto-advance never submits video generation or export for reserved shots')
  await finishReserve(false,4);await settle()
  assert.deepEqual(await batch(),normal)
  assert.equal(await page.$eval(nextExport,b=>b.textContent.trim()),'下一步：合成与导出')

  await page.goto(origin+'/tests/shot-result-overview.html?early-vibe=1',{waitUntil:'networkidle0'})
  await page.waitForSelector('.production-tabs')
  assert.equal(await page.$eval(panel,e=>!!e.getClientRects().length),false,'early design tab does not show Vibe above core controls')
  await installWorkflowMock()
  const type='[data-design-shot="shot1"] .video-row select:has(option[value="vibe"])'
  const promptTab='[data-design-shot="shot1"] [data-panel="prompts"] .panel-toggle[aria-expanded="false"]'
  if(await page.$(promptTab))await page.click(promptTab)
  await page.waitForSelector(type,{visible:true});assert.equal(await page.$eval(type,e=>e.disabled),false)
  await page.select(type,'vibe')
  await page.waitForFunction(()=>document.querySelector('.vibe-preview')?.open)
  assert.match(await page.$eval(panel,p=>p.textContent),/还没有完成的核心图/)
  assert.deepEqual(await btnState('自动设计并预览'),[])
  assert.deepEqual(await btnState('预留给 Vibe'),[true])
  assert.equal(await count(),0,'selecting Vibe without a core image only opens the panel')
  await answer(false);await clickButton('预留给 Vibe');await settle()
  assert.equal(await count(),0);assert.match((await confirms()).at(-1),/不会提交给视频模型/)
  await answer(true);await finishReserve(true,1)
  assert.equal(await page.$eval(type,e=>e.value),'vibe')
  await answer(false);await clickButton('取消预留');await settle()
  assert.equal(await count(),1);assert.match((await confirms()).at(-1),/取消预留/)
  await answer(true);await finishReserve(false,2);await settle()
  assert.equal(await page.$eval(type,e=>e.value),'video')
  assert.deepEqual(await noGenerations(),[],'early reserve/cancel never requests images, videos, or a replan')
  assert.equal(await count(),2,'only the two explicitly confirmed local reservation writes happened')
  assert.deepEqual(errors,[])
  console.log('PASS: preview/play/download, consent, adoption/undo, early reserve/cancel without images, export/batch guards, cache and stale results.')
} finally {
  try { if(browser)await browser.close() } finally { await server.close() }
}
