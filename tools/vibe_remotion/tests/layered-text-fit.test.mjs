import test from 'node:test';
import assert from 'node:assert/strict';
import {fitLayeredText,isCompactNumericLabel} from '../lib/layered-text-fit.mjs';

test('compact values retain their units without breaking explicitly authored lines',()=>{
  for(const text of ['11.8个月','12.1个月','49对患者','29.7%','≥ 400 ng/mL'])assert.ok(isCompactNumericLabel(text),text);
  for(const text of ['研究团队','11.8\n个月','11.8个月后进行下一阶段的复查'])assert.equal(isCompactNumericLabel(text),false,text);
});

test('fitting leaves valid typography unchanged',()=>{
  assert.equal(fitLayeredText(72,43,()=>true),72);
});
test('bounded fit finds largest readable size, without changing content or geometry',()=>{
  const size=fitLayeredText(72,43,s=>s*1.2*2<=130);
  assert.ok(size>=54.1&&size<=130/2.4);
});
test('too-small frame fails rather than truncating or shrinking without a limit',()=>{
  assert.equal(fitLayeredText(72,43,s=>s<=20),null);
  assert.equal(fitLayeredText(72,undefined,s=>s<=54),null);
});
test('fractional minimum cannot be rounded below its readability floor',()=>{
  assert.equal(fitLayeredText(72,43.123,s=>s<=43.123),43.123);
});
