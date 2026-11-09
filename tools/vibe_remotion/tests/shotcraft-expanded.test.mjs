import test from 'node:test';
import assert from 'node:assert/strict';
import {RECIPES,recipeRules,minimumFrames,validateShotcraftInput,shotcraftItemFrames} from '../lib/shotcraft-contract.mjs';
import {expandedFixture,fixtureBatches} from './shotcraft-expanded-fixture.mjs';

test('twelve runtime recipes validate at exact minimum durations',()=>{
  assert.equal(RECIPES.length,12);
  for(const dark of [true,false])for(const maximum of [true,false])for(const batch of fixtureBatches(expandedFixture({dark,maximum})))assert.doesNotThrow(()=>validateShotcraftInput(batch));
});
test('every adapter enforces its own time, count and material contract',()=>{
  for(const recipe of RECIPES){
    const input=expandedFixture(),s=input.plan.stages.find(s=>s.recipe===recipe);
    s.end=minimumFrames(recipe,s.items.length)/30;s.start=0;input.plan.stages=[s];input.duration=input.plan.duration=s.end;
    assert.doesNotThrow(()=>validateShotcraftInput(input),recipe);
    s.end-=1/30;input.duration=input.plan.duration=s.end;
    assert.throws(()=>validateShotcraftInput(input),/too short/,recipe);
  }
  const input=fixtureBatches(expandedFixture())[2];input.plan.stages.find(s=>s.recipe==='scanline-annotate-focus').items[0].asset_id=undefined;
  assert.throws(()=>validateShotcraftInput(input),/Original image/);
});
test('all sequential items and flip fronts receive evidence frames',()=>{
  const input=expandedFixture();const frames=shotcraftItemFrames(input.plan);
  assert.equal(frames.length,input.plan.stages.filter(s=>recipeRules[s.recipe].sequential||s.recipe==='card-flip-reveal').reduce((sum,s)=>sum+s.items.length,0));
  for(const row of frames)assert.ok(row.frame>=0&&row.frame<input.duration*30);
});
