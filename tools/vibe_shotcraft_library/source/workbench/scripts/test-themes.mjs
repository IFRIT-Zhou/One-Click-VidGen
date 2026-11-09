import { createRequire } from 'node:module';
import fs from 'node:fs';
import assert from 'node:assert/strict';
import test from 'node:test';
import { runInNewContext } from 'node:vm';
import ts from 'typescript';

// Run the small pure state/props adapter using the project's existing compiler.
const require = createRequire(import.meta.url);
require.extensions['.ts'] = (mod, filename) => mod._compile(ts.transpileModule(fs.readFileSync(filename, 'utf8'), {
  compilerOptions: {module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020, esModuleInterop: true, jsx: ts.JsxEmit.ReactJSX},
}).outputText, filename);
require.extensions['.tsx'] = require.extensions['.ts'];
const {switchTheme, themedProps, inheritedProps, selectedTheme, themedBackground, upgradeLegacyTheme} = require('../src/theme.ts');
const card = {themeKey: 'title', schema: [{key:'ink',default:'#111'},{key:'text',default:'Title'}]};
const manifest = {themeProp:'__theme', defaultTheme:'paper', themes:[
  {id:'paper',background:'#fff',unitDefaults:{title:{ink:'#111'}}},
  {id:'dark',background:'#111',unitDefaults:{title:{ink:'#eee'}}},
]};
test('palette edits reach props and background, preserve clip overrides, and reset on preset switch', () => {
  const m={...manifest,paletteProp:'__palette',themes:[{id:'dark',palette:{page:'#111111',text:'#eeeeee',accent:'#999999'},unitDefaults:{title:{ink:'#eeeeee',amber:'#999999'}}}]};
  const colors={page:'#fefefe',text:'#222222',accent:'#2255aa',font:'evil',border:'url(x)'};
  const props=themedProps(m,card,'dark',{ink:'#123456'},colors);
  assert.equal(props.ink,'#123456');
  assert.equal(props.amber,'#2255aa');
  assert.deepEqual(props.__palette,{page:'#fefefe',text:'#222222',accent:'#2255aa'});
  assert.equal(themedBackground({themeId:'dark',themeColors:colors},m),'#fefefe');
  const project={themeId:'dark',themeColors:colors,tracks:[]};
  const reset=switchTheme(project,m,'dark');
  assert.equal(reset.themeColors,undefined);
  assert.equal(reset.tracks,project.tracks);
  assert.equal(JSON.parse(JSON.stringify(project)).themeColors.accent,'#2255aa');
  assert.equal(themedProps(m,{schema:[]},'dark',{},colors).__palette,undefined);
});
test('a theme change preserves the complete edit tree and round-trips in JSON', () => {
  const project = {name:'Edited',tracks:[{id:'t',clips:[{start:17,duration:90,props:{text:'My copy',ink:'#c0ffee'}}]}]};
  const changed = switchTheme(project, manifest, 'dark');
  assert.equal(changed.tracks,project.tracks);
  assert.equal(project.themeId,undefined);
  assert.equal(JSON.parse(JSON.stringify(changed)).themeId,'dark');
  assert.equal(themedBackground(changed,manifest),'#111');
});
test('explicit edits take precedence even when equal to another preset default', () => {
  assert.deepEqual(themedProps(manifest,card,'dark',{ink:'#111',text:'Edited'}),{ink:'#111',text:'Edited',__theme:'dark'});
  assert.equal(themedProps(manifest,card,'dark',{ink:'#c0ffee'}).ink,'#c0ffee');
  assert.deepEqual(inheritedProps(manifest,'title',{ink:'#111',text:'Edited'}),{text:'Edited'});
});
test('old JSON is normalized once, subsequent explicit default-colored edits survive', () => {
  const old={tracks:[{clips:[{cardId:'title',props:{ink:'#111',text:'Edited'}}]}]};
  const upgraded=upgradeLegacyTheme(old,manifest,{title:card});
  assert.equal(upgraded.themeId,'paper');
  assert.deepEqual(upgraded.tracks[0].clips[0].props,{text:'Edited'});
  upgraded.tracks[0].clips[0].props.ink='#111';
  assert.equal(upgradeLegacyTheme(upgraded,manifest,{title:card}),upgraded);
  assert.equal(old.tracks[0].clips[0].props.ink,'#111');
});
test('missing or unknown IDs use the default and invalid switches are no-ops', () => {
  const project={tracks:[]};
  assert.equal(selectedTheme(manifest,'unknown').id,'paper');
  assert.equal(switchTheme(project,manifest,'unknown'),project);
  assert.equal(themedProps(manifest,card).ink,'#111');
});
test('ordinary manifests and non-theme cards retain their original behavior', () => {
  assert.deepEqual(themedProps(null,card,undefined,{text:'Edited'}),{ink:'#111',text:'Edited'});
  assert.deepEqual(themedProps(manifest,{schema:card.schema},'dark'),{ink:'#111',text:'Title'});
  assert.deepEqual(inheritedProps({},'title',{ink:'#111'}),{ink:'#111'});
});
const {parsePalette,paletteFromProject,applyPalette}=require('../src/palettePresets.ts');
test('portable palettes validate schema and round-trip without changing clips',()=>{
 const m={themes:[{id:'dark',label:'Dark',palette:{page:'#111111',text:'#eeeeee'}}]};
 const p={themeId:'dark',themeColors:{text:'#ffffff'},tracks:[]};
 const saved=paletteFromProject(m,p,'My theme');
 assert.deepEqual(parsePalette(JSON.stringify(saved),m),saved);
 assert.equal(applyPalette(p,saved).tracks,p.tracks);
 for(const bad of [{...saved,version:2},{...saved,baseThemeId:'missing'},{...saved,colors:{page:'url(x)',text:'#eeeeee'}},{...saved,colors:{page:'#111111'}}])assert.throws(()=>parsePalette(JSON.stringify(bad),m));
 assert.throws(()=>parsePalette('not json',m));
 assert.throws(()=>parsePalette(' '.repeat(65537),m));
});

const {paletteAsset}=require('../../template/src/themes/palette-assets.ts');
const paletteClips=require('../../template/src/themes/palette-assets.json').clips;
const palettes=require('../../template/src/themes/palettes.json');
test('every editable texture is CSS-url-safe, including procedural kraft grain',()=>{
 const originals=fs.readdirSync(new URL('../../template/public/textures/live/',import.meta.url)).filter(f=>f.endsWith('.png'));
 assert.deepEqual(Object.keys(paletteClips).sort(),originals.sort());
 for(const [id,colors] of Object.entries(palettes))for(const name of originals){
  const uri=paletteAsset({...colors,id},name);
  assert.ok(uri.startsWith('data:image/svg+xml;'));
  assert.doesNotMatch(uri,/[()']/);
  const decoded=decodeURIComponent(uri.slice(uri.indexOf(',')+1));
  assert.ok(decoded.includes('var(--'));
  if(id==='vintage-kraft')assert.ok(decoded.includes('url(#kraft-grain)'));
 }
});

const {resolveTheme,themePaint}=require('../../template/src/themes/visual-theme.tsx');
const {WORKBENCH}=require('../../template/src/workbench.ts');

test('illumination, accents and shadows have distinct roles and preserve opacity',()=>{
 const warmLights=['255,190,120','255,214,150','255,240,210','255,240,214','255,241,214','255,244,224','255,246,228','255,248,232','255,248,235','255,255,255'];
 for(const id of Object.keys(palettes)){
  const theme=resolveTheme(id,{surface:'#eeeeee',accent:'#ff00ff',text:'#123456',page:'#abcdef'});
  for(const rgb of warmLights)assert.equal(themePaint(theme,`rgba(${rgb},0.42)`),'rgba(238,238,238,0.42)');
  assert.equal(themePaint(theme,'rgba(180,120,50,.35)'),'rgba(255,0,255,.35)');
  assert.equal(themePaint(theme,'rgba(40,30,20,0.18)'),'rgba(18,52,86,0.18)');
  assert.equal(themePaint(theme,'rgb(250,247,242)'),'rgb(171,205,239)');
  assert.equal(themePaint(theme,'rgba(220, 130, 50, .7)'),'rgba(220, 130, 50, .7)');
 }
 const original='radial-gradient(rgba(255,241,214,0.42), rgba(180,120,50,.35), rgba(40,30,20,0.18))';
 assert.equal(themePaint(resolveTheme('ink-press'),original),original);
});

test('preset switches preserve weekly copy and share compact caption defaults',()=>{
 const wbr=WORKBENCH.shots.find(u=>u.id==='wbr');
 const weeklyCard={themeKey:'wbr',schema:wbr.schema};
 const imported=inheritedProps(WORKBENCH,'wbr',wbr.props);
 assert.equal(imported.kicker,'Weekly Brief · 2026-W28');
 const caption=WORKBENCH.captions[0];
 const captionCard={themeKey:'caption',schema:caption.schema};
 for(const theme of WORKBENCH.themes){
  assert.equal(theme.unitDefaults.wbr.kicker,undefined);
  assert.equal(themedProps(WORKBENCH,weeklyCard,theme.id,imported).kicker,imported.kicker);
  assert.equal(themedProps(WORKBENCH,weeklyCard,theme.id,{kicker:'Our weekly report'}).kicker,'Our weekly report');
  assert.equal(themedProps(WORKBENCH,weeklyCard,theme.id,{kicker:''}).kicker,'');
  const props=themedProps(WORKBENCH,captionCard,theme.id,inheritedProps(WORKBENCH,'caption',caption.props));
  assert.equal(props.fontSize,theme.id==='ink-press'?22:36);
  assert.equal(props.bottom,theme.id==='ink-press'?72:32);
  assert.equal(themedProps(WORKBENCH,captionCard,theme.id,{fontSize:44,bottom:90}).bottom,90);
 }
});

test('live palette updates preserve selection and use the gesture checkpoint for undo/redo',()=>{
 const project={themeId:'dark',tracks:[{id:'track',clips:[{id:'clip',cardId:'title',props:{text:'Keep me'}}]}]};
 const mod={exports:{}};
 const mockModules={
  './cards/registry':{CARDS:{}}, './demoProject':{demoProject:()=>project},
  './cards/projectCards':{MANIFEST:null}, './cards/manifest':{manifestKey:()=>''},
  './projectImport':{buildProjectFromManifest:()=>project},
 };
 const code=ts.transpileModule(fs.readFileSync(new URL('../src/store.ts',import.meta.url),'utf8'),{
  compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2020},
 }).outputText;
 runInNewContext(code,{
  module:mod,exports:mod.exports,URLSearchParams,
  require:id=>mockModules[id]??(id.startsWith('./')?require(`../src/${id.slice(2)}.ts`):require(id)),
  localStorage:{getItem:()=>null,setItem:()=>{}},
  window:{location:{search:'',pathname:'/'},addEventListener:()=>{}},
  document:{addEventListener:()=>{}},setTimeout:()=>0,clearTimeout:()=>{},
 });
 const store=mod.exports.useStore;
 store.getState().select('clip');
 store.getState().commit();
 for(let i=1;i<=80;i++)store.getState().setThemeColors({accent:`#${i.toString(16).padStart(6,'0')}`});
 assert.equal(store.getState().past.length,1);
 assert.equal(store.getState().selectedClipId,'clip');
 assert.equal(store.getState().project.tracks,project.tracks);
 assert.equal(store.getState().project.themeColors.accent,'#000050');
 store.getState().undo();
 assert.equal(store.getState().project.themeColors,undefined);
 store.getState().redo();
 assert.equal(store.getState().project.themeColors.accent,'#000050');
});
