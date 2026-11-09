import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {validateShotcraftInput,shotcraftProofFrames,shotcraftItemFrames} from '../lib/shotcraft-contract.mjs';
import {fixture} from './shotcraft-fixture.mjs';

test('Shotcraft accepts the 132/167/105-frame source structure without mutating data',()=>{
  const input=fixture(),copy=structuredClone(input),result=validateShotcraftInput(input);
  assert.deepEqual(input,copy);assert.deepEqual(result,copy);assert.notEqual(result,input);
});
test('palette and typography are required frozen data',()=>{
  for(const change of [p=>delete p.palette,p=>delete p.palette.accent,p=>p.palette.ink='#EAF3F8',p=>p.palette.accent='url(http://x)',p=>p.typography='code']){
    const input=fixture();change(input.plan);assert.throws(()=>validateShotcraftInput(input));
  }
});

test('an image-only card needs no placeholder caption but must bind an asset',()=>{
  const input=fixture(),item=input.plan.stages[0].items[0];
  item.title='';item.body='';item.asset_id='photo';item.motif='none';input.assets.photo='original.png';
  assert.doesNotThrow(()=>validateShotcraftInput(input));
  delete input.assets.photo;
  assert.throws(()=>validateShotcraftInput(input),/Unbound asset/);
  delete item.asset_id;
  assert.throws(()=>validateShotcraftInput(input),/item title/);
});
test('rejects arbitrary code/style fields, unbound assets, short stages and portrait',()=>{
  for(const change of [i=>i.plan.stages[0].js='alert(1)',i=>i.plan.stages[0].items[0].asset_id='unknown',i=>i.plan.canvas=[540,960],i=>i.plan.duration=9,i=>i.plan.stages[0].end=3.9,i=>i.plan.stages[1].recipe='custom-code',i=>i.plan.stages[1].items[0].motif='fake-plot']){
    const input=fixture();change(input);assert.throws(()=>validateShotcraftInput(input));
  }
});
test('outro maximum matches the backend 6-second boundary with 1ms tolerance',()=>{
  for(const [duration,accepted] of [[6,true],[6.001,true],[6.0011,false],[6+1/30,false]]){
    const input=fixture(),stage=input.plan.stages.at(-1);
    stage.start=0;stage.end=duration;input.plan.stages=[stage];
    input.duration=duration;input.plan.duration=duration;
    if(accepted)assert.doesNotThrow(()=>validateShotcraftInput(input));
    else assert.throws(()=>validateShotcraftInput(input),/outro must be at most 6 seconds/);
  }
});
test('proof metadata retains existing filenames and audits every relay item',()=>{
  const input=fixture(),proof=shotcraftProofFrames(input.plan,input.duration,30),items=shotcraftItemFrames(input.plan,30);
  assert.deepEqual(Object.keys(proof.details),['early','middle','end']);
  assert.deepEqual(proof.stages.map(s=>s.file),['stage_00.png','stage_01.png','stage_02.png']);
  assert.equal(items.length,3);
  for(const sample of [...proof.stages,...items])assert.ok(sample.frame>=0&&sample.frame<404);
  assert.equal(proof.stages[2].frame,388);
});
test('trusted scene contains no sample medical copy or theme colors, assets use contain',()=>{
  const source=fs.readFileSync(new URL('../src/ShotcraftScene.tsx',import.meta.url),'utf8');
  assert.doesNotMatch(source,/#[a-fA-F0-9]{6}\b|Math\.random|Date\.now|dangerouslySetInnerHTML|eval\(/);
  assert.match(source,/objectFit:'contain'/);assert.match(source,/OCV_LAYOUT_OVERFLOW/);
  for(const token of ['SpeedBlur','landEase','translateZ','strokeDashoffset','stepAt'])assert.ok(source.includes(token));
});
test('runtime attribution retains upstream license and changed-file notices',()=>{
  const license=fs.readFileSync(new URL('../lib/video-shotcraft/LICENSE',import.meta.url),'utf8');
  assert.match(license,/Apache License/);assert.match(license,/Copyright 2026 Wei Yihao/);
  assert.match(fs.readFileSync(new URL('../lib/video-shotcraft/NOTICE.txt',import.meta.url),'utf8'),/Modified files/);
});
