import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import {sceneProgress} from '../lib/code-animation-progress.mjs';
import {compileProgram, evaluateProgram, validateProgram} from '../lib/code-program.mjs';

const context = {frame: 0, fps: 30, poster: false, width: 1280, height: 720,
  duration: 8, geometry: {}, measurements: {}};

test('poster progress is fully revealed while real video frame zero remains hidden', () => {
  assert.equal(sceneProgress({...context, poster: true}, .4, 1.05), 1);
  assert.equal(sceneProgress(context, .4, 1.05), 0);
  assert.ok(Math.abs(sceneProgress({...context, frame: 21}, .4, 1) - .5) < 1e-12);
  assert.equal(sceneProgress({...context, frame: 90}, .4, 1), 1);
  assert.equal(sceneProgress({...context, frame: 6}, .4, 1), 0);
});

test('progress uses seconds at any frame rate, supports a direct reveal and rejects invalid ranges', () => {
  assert.equal(sceneProgress({...context, fps: 60, frame: 45}, .5, 1), .5);
  assert.equal(sceneProgress({...context, frame: 14}, .5, .5), 0);
  assert.equal(sceneProgress({...context, frame: 15}, .5, .5), 1);
  assert.equal(sceneProgress(context, -1, 1), .5);
  for (const [start, end] of [[1, 0], ['0', 1], [NaN, 1], [0, Infinity], [false, 1]])
    assert.throws(() => sceneProgress(context, start, end), /OCV_CODE_CONTRACT:ctx.progress/);
  for (const invalid of [{fps: 0}, {frame: -1}, {frame: Infinity}, {fps: '30'}])
    assert.throws(() => sceneProgress({...context, ...invalid}, 0, 1), /valid frame and positive fps/);
});

test('interpreted helper supports arbitrary authored opacity, width and motion without host functions', () => {
  const program = compileProgram(`function Scene(ctx) {
    const p = ease(ctx.progress(0.5, 1.5));
    return <div style={{width:800*p,opacity:p,transform:\`translateX(\${(1-p)*-60}px)\`}}>
      <Text id="value">示例结果</Text>
    </div>;
  }`, ts);
  const poster = evaluateProgram(program, {...context, poster: true});
  assert.equal(poster.props.style.opacity, 1);
  assert.equal(poster.props.style.width, 800);
  const opening = evaluateProgram(program, context);
  assert.equal(opening.props.style.opacity, 0);
  assert.equal(opening.props.style.width, 0);
  const midway = evaluateProgram(program, {...context, frame: 30});
  assert.equal(midway.props.style.opacity, .5);
  assert.equal(midway.props.style.width, 400);
  assert.equal(midway.props.style.transform, 'translateX(-30px)');
  assert.equal(evaluateProgram(JSON.parse(JSON.stringify(program)), {...context, poster: true}).props.style.width, 800);
});

test('custom exit handling can preserve full poster without a fixed choreography', () => {
  const program = compileProgram(`function Scene(ctx) {
    const incoming=ctx.progress(0,0.5);
    const outgoing=ctx.poster ? 1 : 1-ctx.progress(ctx.duration-0.3,ctx.duration);
    return <Text id="label" style={{opacity:incoming*outgoing}}>文字</Text>;
  }`, ts);
  assert.equal(evaluateProgram(program, {...context, poster: true}).props.style.opacity, 1);
  assert.equal(evaluateProgram(program, context).props.style.opacity, 0);
  const end = evaluateProgram(program, {...context, frame: 239}).props.style.opacity;
  assert.ok(end > 0 && end < .2);
});

test('progress remains a whitelisted data instruction, not arbitrary callable context access', () => {
  for (const expression of ['ctx.progress()', 'ctx.progress(1)', 'ctx.progress(0,1,2)',
    'ctx.progress.call(null,0,1)', 'ctx["progress"](0,1)', 'ctx.other(0,1)',
    'ctx.progress.constructor("return 1")()']) {
    assert.throws(() => compileProgram(`function Scene(ctx) { return <Text>{${expression}}</Text>; }`, ts), /OCV_CODE_CONTRACT/);
  }
  assert.throws(() => evaluateProgram(compileProgram('function Scene(ctx) { return <Text>{ctx.progress}</Text>; }', ts), context), /Missing own data property/);
  const forged = compileProgram('function Scene(ctx) { return <Text>{ctx.progress(0,1)}</Text>; }', ts);
  forged.body[0].value.children[0].helper = 'ctx.arbitrary';
  assert.throws(() => validateProgram(forged), /Unknown helper/);
});
