import test from 'node:test';
import assert from 'node:assert/strict';
import {createRenderProgress,RENDER_STAGE_PREFIX} from '../lib/render-progress.mjs';

test('records stage start before work and duration after completion',async()=>{
  let now=1000;const lines=[];
  const run=createRenderProgress({clock:()=>now,write:line=>lines.push(line)});
  const result=await run('frame_end',async()=>{
    assert.equal(lines.length,1);now=3500;return {ready:true};
  });
  assert.deepEqual(result,{ready:true});
  assert.deepEqual(lines.map(line=>JSON.parse(line.slice(RENDER_STAGE_PREFIX.length))),[
    {stage:'frame_end',status:'started',elapsed_seconds:0},
    {stage:'frame_end',status:'completed',elapsed_seconds:2.5,stage_seconds:2.5},
  ]);
});

test('preserves failure identity without logging private exception details',async()=>{
  const lines=[];const error=new Error('PRIVATE_ASSET_PATH_AND_CODE');
  const run=createRenderProgress({clock:()=>10,write:line=>lines.push(line)});
  await assert.rejects(run('browser_close',()=>{throw error}),actual=>actual===error);
  assert.equal(lines.length,2);
  assert.equal(JSON.parse(lines[1].slice(RENDER_STAGE_PREFIX.length)).status,'failed');
  assert.ok(!lines.join('').includes(error.message));
});
