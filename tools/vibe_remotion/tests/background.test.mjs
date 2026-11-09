import test from 'node:test';
import assert from 'node:assert/strict';
import {BACKGROUND_KINDS,backgroundLayers,validateBackground,ambientMotion} from '../background.mjs';
import {validateInput} from '../contract.mjs';

const dark={background:'#081b30',ink:'#edf4fa',muted:'#a2bed5',surface:'#102941',line:'#4a718f',accent:'#e6ac52'};
test('ambient motion is frame deterministic, slow and optional',()=>{
  assert.deepEqual(ambientMotion(5,'soft_gradient'),ambientMotion(5,'soft_gradient'));
  assert.notDeepEqual(ambientMotion(1,'technical_grid'),ambientMotion(8,'technical_grid'));
  for(const kind of [undefined,'solid','paper'])assert.equal(ambientMotion(5,kind).enabled,false);
  for(let t=0;t<60;t+=.1){const m=ambientMotion(t,'soft_gradient');assert(Math.abs(m.x)<=2.4&&Math.abs(m.y)<=1.8);}
  assert.throws(()=>ambientMotion(NaN,'soft_gradient'));
});
const fixture=()=>({duration:4,fps:30,assets:{},plan:{version:'ocv-motion-v1',canvas:[1920,1080],theme:'paper',palette:dark,
  title:'背景继承',stages:[{id:'a',layout:'findings',headline:'深色背景',start:0,end:4,items:[{id:'a',title:'保持原配色'}]}]}});

test('all four native backgrounds are optional and strictly enumerated',()=>{
  assert.doesNotThrow(()=>validateInput(fixture()));
  for(const kind of BACKGROUND_KINDS) {const f=fixture();f.plan.background={kind};assert.equal(validateInput(f).plan.background.kind,kind);}
  for(const invalid of [null,[],{},'paper',{kind:'url'},{kind:'solid',url:'https://example.test'},{kind:'solid',css:'color:red'}]) assert.throws(()=>validateBackground(invalid),/Invalid background/);
});
test('source dark colors override paper theme in every new background style',()=>{
  for(const kind of BACKGROUND_KINDS) {
    const plan={...fixture().plan,background:{kind}};
    const layers=JSON.stringify(backgroundLayers(plan));
    assert(!/#F6F3ED|#E7EEE6|255,255,255/i.test(layers));
    const colors=[...layers.matchAll(/rgba\((\d+,\d+,\d+),[\d.]+\)/g)].map(v=>v[1]);
    const allowed=Object.values(dark).map(hex=>[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(','));
    assert(colors.every(rgb=>allowed.includes(rgb)));
  }
});
test('native backgrounds are deterministic local CSS without external assets or random state',()=>{
  for(const kind of BACKGROUND_KINDS) {
    const plan={...fixture().plan,background:{kind}};
    assert.deepEqual(backgroundLayers(plan,.6),backgroundLayers(plan,.6));
    assert(!/url\(|https?:|data:|random|animation/i.test(JSON.stringify(backgroundLayers(plan))));
  }
});
test('solid is clean and omitted background retains legacy palette behavior',()=>{
  assert.deepEqual(backgroundLayers({...fixture().plan,background:{kind:'solid'}}),[]);
  assert.deepEqual(backgroundLayers(fixture().plan),[]);
  assert.match(backgroundLayers({theme:'paper'})[0].background,/#E7EEE6/);
});
