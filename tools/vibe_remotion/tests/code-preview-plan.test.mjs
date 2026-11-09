import test from 'node:test';
import assert from 'node:assert/strict';
import {codePreviewPlan} from '../lib/code-preview-plan.mjs';
import {validateCodeInput} from '../lib/code-render-contract.mjs';

const input = {mode:'preview',code:'function Scene(ctx) { return <Text id="a">Hello</Text>; }',
  canvas:[640,360],duration:3,fps:30,assets:{background:'/prepared.png'}};

test('old preview input remains full four-frame proof', () => {
  const plan = codePreviewPlan(validateCodeInput(input));
  assert.equal(plan.preview_level, 'full');
  assert.equal(plan.proof_complete, true);
  assert.deepEqual(plan.stage_frames.map(frame => frame.id), ['early','middle','hold','end']);
});

test('layout preview skips only temporal proof, not poster geometry', () => {
  const validated = validateCodeInput({...input,preview_level:'layout'});
  const plan = codePreviewPlan(validated);
  assert.deepEqual(validated.canvas, input.canvas);
  assert.equal(plan.preview_level, 'layout');
  assert.equal(plan.animation_check, 'not_checked');
  assert.equal(plan.proof_complete, false);
  assert.deepEqual(plan.stage_frames, []);
});

test('video ignores a layout preference and always requires full proof', () => {
  const validated = validateCodeInput({...input,mode:'video',preview_level:'layout'});
  assert.equal(validated.preview_level, 'full');
  const plan = codePreviewPlan({...input,mode:'video',preview_level:'layout'});
  assert.equal(plan.proof_complete, true);
  assert.equal(plan.stage_frames.length, 4);
});

test('bad preview levels and modes fail explicitly', () => {
  for (const preview_level of ['fast',null,true,{},[]]) {
    assert.throws(() => validateCodeInput({...input,preview_level}), /preview_level/);
    assert.throws(() => codePreviewPlan({...input,preview_level}), /preview_level/);
  }
  assert.throws(() => codePreviewPlan({...input,mode:'unsafe'}), /render mode/);
});

test('measurement-only does not claim temporal proof', () => {
  const plan = codePreviewPlan({...input,mode:'measure'});
  assert.equal(plan.proof_complete, false);
  assert.deepEqual(plan.stage_frames, []);
});
