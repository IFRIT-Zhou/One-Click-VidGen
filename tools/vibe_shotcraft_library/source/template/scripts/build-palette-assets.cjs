// Compile trusted fixture markup once. Runtime recoloring uses only validated hex tokens.
const fs=require('node:fs'),path=require('node:path');
const {render}=require('../themes/ui.cjs');
const L=require('../src/aifl/live-layout.json');
const {chromium}=require(process.env.SHOTCRAFT_PLAYWRIGHT || 'playwright');
(async()=>{
 const browser=await chromium.launch({executablePath:process.env.SHOTCRAFT_BROWSER,headless:true});
 try {
  const page=await browser.newPage(); const pages={};
  for(const kind of ['projects','detail','papers','wbr','experiments']){
   await page.setContent(render('modern-light',kind,L));
   pages[kind]=await page.evaluate(()=>{
    const wrapper=document.createElement('div');
    wrapper.setAttribute('xmlns','http://www.w3.org/1999/xhtml');
    wrapper.className='palette-page';
    const style=document.querySelector('style').cloneNode(true);
    style.textContent=style.textContent.replace(/:root\{[^}]*\}/,'').replace(/\bbody\b/g,'.palette-page');
    wrapper.append(style,document.querySelector('main').cloneNode(true));
    return new XMLSerializer().serializeToString(wrapper);
   });
  }
  const clips={};
  const add=(name,kind,b,empty=false)=>{clips[name]={kind,x:Math.round(b.x),y:Math.round(b.y),w:Math.round(b.x+b.w)-Math.round(b.x),h:Math.round(b.y+b.h)-Math.round(b.y),empty}};
  add('projects-full.png','projects',{x:0,y:0,w:L.pageW,h:L.projects.pageH});
  add('projects-empty.png','projects',{x:0,y:0,w:L.pageW,h:L.projects.pageH},true);
  for(const b of [...L.projects.cards,...L.float])add(b.file,'projects',b);
  add('nav.png','projects',L.projects.header);add('card4-hires.png','projects',L.projects.cards[3]);
  for(const [name,kind,h] of [['detail-full.png','detail',L.detail.pageH],['detail-experiments.png','experiments',L.detail.expPageH],['papers-full.png','papers',L.papers.pageH],['wbr-full.png','wbr',L.wbr.pageH]])add(name,kind,{x:0,y:0,w:L.pageW,h});
  L.papers.cards.forEach((b,i)=>add(`paper${i+1}.png`,'papers',b));
  fs.writeFileSync(path.join(__dirname,'../src/themes/palette-assets.json'),JSON.stringify({pages,clips})+'\n');
  console.log(`Compiled ${Object.keys(clips).length} recolorable assets from ${Object.keys(pages).length} pages`);
 }finally{await browser.close()}
})().catch(e=>{console.error(e);process.exitCode=1});
