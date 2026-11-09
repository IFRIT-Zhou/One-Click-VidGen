import test from 'node:test';
import assert from 'node:assert/strict';
import {geometryVisibility, sourceAnchorFeedback} from '../lib/code-geometry-visibility.mjs';

const canvas=[640,360];
test('nonzero DOM rectangles outside each canvas edge are not visible',()=>{
  for(const box of [[660,30,50,50],[-60,30,50,50],[30,380,50,50],[30,-60,50,50]]){
    const result=geometryVisibility(box,canvas);
    assert.equal(result.visible,false);
    assert.equal(result.state,'outside_canvas');
    assert.equal(result.visible_box,null);
  }
  assert.equal(geometryVisibility([640,20,50,50],canvas).visible,false);
});

test('partial clipping remains distinct from complete invisibility',()=>{
  const result=geometryVisibility([620,20,40,50],canvas);
  assert.equal(result.visible,true);
  assert.equal(result.state,'partial');
  assert.deepEqual(result.visible_box,[620,20,20,50]);
  assert.equal(result.visible_fraction,.5);
  assert.equal(geometryVisibility([0,0,640.1,360],canvas).state,'inside');
});

test('ancestor overflow clips only its specified axes and composes with canvas',()=>{
  const parent={target:'panel',box:[100,50,80,60],clip_x:true,clip_y:false,overflow_x:'hidden',overflow_y:'visible'};
  assert.equal(geometryVisibility([190,60,40,40],canvas,[parent]).state,'clipped');
  const result=geometryVisibility([150,140,60,40],canvas,[parent]);
  assert.deepEqual(result.visible_box,[150,140,30,40]);
  assert.equal(result.clipped_by[0].target,'panel');
  const nested={target:'inner',box:[140,100,60,50],clip_x:false,clip_y:true,overflow_y:'clip'};
  assert.deepEqual(geometryVisibility([150,140,60,40],canvas,[parent,nested]).visible_box,[150,140,30,10]);
  assert.equal(geometryVisibility([150,170,60,40],canvas,[parent,nested]).visible,false);
});

test('visible overflow and arbitrary nested placement are not prohibited',()=>{
  const result=geometryVisibility([20,20,100,100],canvas,[{box:[50,50,10,10],clip_x:false,clip_y:false}]);
  assert.equal(result.state,'inside');
  assert.equal(result.visible_fraction,1);
});

test('source anchors explain actual parent offsets without rewriting the design',()=>{
  const result=sourceAnchorFeedback([200,40,80,80],[500,60,80,80],[{target:'wrapper',box:[300,20,100,100]}]);
  assert.deepEqual(result.delta,[300,20,0,0]);
  assert.equal(result.displaced,true);
  assert.match(result.hint,/anchor="manual"/);
  assert.match(result.hint,/canvas coordinates/);
  assert.equal(sourceAnchorFeedback([200,40,80,80],[200,40,80,80]).displaced,false);
});

test('empty/zero dimensions cannot establish visibility',()=>{
  for(const box of [null,[20,20,0,40],[20,20,40,0],[20,20,NaN,40]]){
    assert.equal(geometryVisibility(box,canvas).visible,false);
    assert.equal(geometryVisibility(box,canvas).state,'empty');
  }
});
