import test from 'node:test';
import assert from 'node:assert/strict';
import {fittedSize,isTextOnlyCard,textOnlyCardGeometry} from '../lib/shotcraft-card-layout.mjs';

test('text-only layout applies only to no-asset + none and does not mutate content',()=>{
  const item={id:'focus',title:'预后分层',body:'一项关键发现',motif:'none'};
  const copy=structuredClone(item);
  assert.equal(isTextOnlyCard(item,undefined),true);
  assert.equal(isTextOnlyCard(item,'data:image/png;base64,abc'),false);
  for(const motif of ['document','book','inspection','refinement'])assert.equal(isTextOnlyCard({...item,motif},undefined),false);
  textOnlyCardGeometry(item,650,548);
  assert.deepEqual(item,copy);
});
test('all three card sizes center the text group and promote the title',()=>{
  const item={title:'预后分层',body:'一项关键发现'};
  for(const [w,h,compact] of [[650,548,false],[840,480,false],[410,314,true]]){
    const layout=textOnlyCardGeometry(item,w,h,compact);
    const bottom=layout.body.top+layout.body.height;
    assert.ok(Math.abs((layout.title.top+bottom)/2-h/2)<.001);
    assert.ok(layout.title.top>=layout.pad);
    assert.ok(bottom<=h-layout.pad+.001);
    assert.ok(layout.body.top>layout.title.top+layout.title.height);
    assert.ok(layout.title.size>(compact?42:52));
    assert.ok(layout.title.size>layout.body.size);
  }
});
test('title without body is centered independently with no phantom visual block',()=>{
  const layout=textOnlyCardGeometry({title:'只有标题'},650,548);
  assert.equal(layout.body.height,0);
  assert.ok(Math.abs(layout.title.top+layout.title.height/2-274)<.001);
});
test('content fitting preserves minimum readability and explicit newlines',()=>{
  assert.equal(fittedSize('完整文字'.repeat(100),200,50,100),32);
  const layout=textOnlyCardGeometry({title:'第一行\n第二行',body:'保留原文。'},650,548);
  assert.ok(layout.title.height>layout.title.size*2);
  assert.ok(layout.title.size>=32);
});
