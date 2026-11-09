import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import {validateInput,proofFrames,loadAssets} from '../contract.mjs';
import {activeEvidence,entranceSeconds,exitSeconds,readingSeconds} from '../timing.mjs';
import {contrastRatio,validatePalette} from '../palette.mjs';

const fixture=()=>({duration:8,fps:30,assets:{},plan:{version:'ocv-motion-v1',canvas:[1920,1080],theme:'paper',title:'研究结果',
  stages:[{id:'a',layout:'findings',start:0,end:4,headline:'治疗前信息',items:[{id:'x',title:'临床指标'}]},
    {id:'b',layout:'comparison',start:4,end:8,headline:'两项相关因素',items:[{id:'y',title:'因素一'},{id:'z',title:'因素二',at:.5}]}]}});

test('accepts semantic stages without model coordinates',()=>assert.equal(validateInput(fixture()).plan.stages.length,2));
test('rejects gaps and wrong timeline endpoints',()=>{const f=fixture();f.plan.stages[1].start=4.2;assert.throws(()=>validateInput(f),/continuously/);});
test('rejects arbitrary code and unbound assets',()=>{let f=fixture();f.plan.stages[0].js='fetch()';assert.throws(()=>validateInput(f),/Unsupported/);
  f=fixture();f.plan.stages[0].items[0].asset_id='missing';assert.throws(()=>validateInput(f),/Unbound/);});
test('evidence requires real image binding',()=>{const f=fixture();f.plan.stages[0].layout='evidence';assert.throws(()=>validateInput(f),/bound image/);});
test('rejects items appearing after phase',()=>{const f=fixture();f.plan.stages[0].items[0].at=3.9;assert.throws(()=>validateInput(f),/too late/);});
test('proof covers every stage and includes real timestamps',()=>{const f=fixture();const frames=proofFrames(f.plan,8);assert.equal(frames.stages.length,2);
  assert(frames.stages[0].time<4);assert(frames.stages[1].time>=4);assert(frames.details.end<240);});
test('forbids URL assets',()=>assert.throws(()=>loadAssets({core:'https://example.test/a.png'}),/absolute local/));
test('bound scene IDs may contain colon without becoming paths',()=>{
  const dir=fs.mkdtempSync(path.join(os.tmpdir(),'ocv-motion-test-'));
  try {
    const file=path.join(dir,'pixel.png');
    fs.writeFileSync(file,Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a9VYAAAAASUVORK5CYII=','base64'));
    assert.match(loadAssets({'scene:scene_01':file})['scene:scene_01'],/^data:image\/png;base64,/);
  } finally {fs.rmSync(dir,{recursive:true,force:true});}
});
test('renderer limits agree with backend for canvas and unit',()=>{const f=fixture();f.plan.canvas=[160,90];f.plan.stages[0].items[0].unit='u'.repeat(32);assert.doesNotThrow(()=>validateInput(f));});
test('first evidence respects cue and follows time, not array order',()=>{
  const items=[{id:'later',asset_id:'a',at:3},{id:'earlier',asset_id:'b',at:1}];
  assert.equal(activeEvidence(items,.5),null);
  assert.equal(activeEvidence(items,2).item.id,'earlier');
  assert.equal(activeEvidence(items,4).item.id,'later');
});
test('a phase reserves entrance, reading and exit time',()=>{
  assert(entranceSeconds(1)+readingSeconds(1)+exitSeconds(1)<1);
  const f=fixture();f.plan.stages[0].items[0].at=.8;assert.throws(()=>validateInput(f),/First meaningful/);
});
test('default stagger shrinks for short phases without changing explicit cues',()=>{
  const f=fixture();f.duration=1;f.plan.stages=[{id:'short',layout:'comparison',start:0,end:1,headline:'短阶段',items:[{id:'a',title:'一'},{id:'b',title:'二'},{id:'c',title:'三'}]}];
  const normalized=validateInput(f);assert(normalized.plan.stages[0].items[2].at<=.151);assert.equal(f.plan.stages[0].items[0].at,undefined);
});
test('visual defaults use a bound asset and may carry the evidence image',()=>{
  const f=fixture();f.assets.person='/local/person.png';f.plan.stages[0].visual={asset_id:'person',caption:'原图素材'};
  f.plan.stages[0].layout='evidence';f.plan.stages[0].items[0].at=1;
  const result=validateInput(f);
  assert.equal(result.plan.stages[0].visual.placement,'left');
  assert.equal(result.plan.stages[0].visual.treatment,'contain');
  assert.equal(f.plan.stages[0].visual.placement,undefined);
});
test('visual references reject unbound images, paths and free-form layout instructions',()=>{
  const f=fixture();f.plan.stages[0].visual={asset_id:'missing'};assert.throws(()=>validateInput(f),/Unbound visual/);
  f.assets.bound='/local/person.png';f.plan.stages[0].visual={asset_id:'bound',placement:'absolute'};assert.throws(()=>validateInput(f),/placement/);
  f.plan.stages[0].visual={asset_id:'bound',css:'position:fixed'};assert.throws(()=>validateInput(f),/Unsupported/);
});
test('palette validates effective theme overrides and readable contrast',()=>{
  assert.equal(contrastRatio('#000000','#ffffff'),21);
  for(const theme of ['paper','clinical','midnight']) assert.doesNotThrow(()=>validatePalette({theme}));
  const f=fixture();f.plan.palette={background:'#ffffff',ink:'#111111',muted:'#666666',accent:'#9c3127'};
  assert.doesNotThrow(()=>validateInput(f));
  f.plan.palette.ink='#eeeeee';assert.throws(()=>validateInput(f),/ink\/background/);
  f.plan.palette.ink='#111111';f.plan.palette.muted='#bbbbbb';assert.throws(()=>validateInput(f),/muted\/background/);
  f.plan.palette={background:'url(https://example.test)'};assert.throws(()=>validateInput(f),/Invalid palette/);
});
