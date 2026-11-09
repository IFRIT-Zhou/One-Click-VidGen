import {test} from 'node:test';
import assert from 'node:assert/strict';
import {presentationForStage,visualGeometry,visualSpan,usesEditorialFocus,editorialContext} from '../layout.mjs';

test('a single semantic item uses hero, except preserved evidence',()=>{
  for(const layout of ['findings','comparison','process']) assert.equal(presentationForStage({layout,items:[{title:'重点'}]}),'hero');
  assert.equal(presentationForStage({layout:'evidence',items:[{title:'原始证据',asset_id:'bound'}]}),'evidence');
});
test('short paired findings use content-sized emphasis without changing data',()=>{
  const stage={layout:'findings',items:[{title:'观察A',value:'42',unit:'%'},{title:'观察B',body:'另一独立观察'}]};
  const before=structuredClone(stage);
  assert.equal(presentationForStage(stage),'paired');
  assert.deepEqual(stage,before);
});
test('images, long prose and real ordered processes keep their dedicated layouts',()=>{
  assert.equal(presentationForStage({layout:'findings',items:[{title:'A',asset_id:'ref'},{title:'B'}]}),'cards');
  assert.equal(presentationForStage({layout:'findings',items:[{title:'A',body:'长'.repeat(61)},{title:'B'}]}),'cards');
  assert.equal(presentationForStage({layout:'process',items:[{title:'先做A'},{title:'再做B'}]}),'process');
});
test('main visuals receive a large separate region in landscape and portrait',()=>{
  for(const portrait of [false,true])for(const placement of ['left','right']) {
    const w=portrait?900:1700,h=portrait?1100:550;
    const g=visualGeometry(w,h,1,portrait,placement);
    for(const rect of [g.visual,g.text]) {
      assert(rect.left>=0&&rect.top>=0&&rect.width>0&&rect.height>0);
      assert(rect.left+rect.width<=w+.001&&rect.top+rect.height<=h+.001);
    }
    assert(portrait?g.visual.height/h>.42:g.visual.width/w>.42);
    assert(g.visual.left+g.visual.width<=g.text.left||g.text.left+g.text.width<=g.visual.left||
      g.visual.top+g.visual.height<=g.text.top||g.text.top+g.text.height<=g.visual.top);
  }
});
test('the same visual remains continuous across neighboring semantic stages',()=>{
  const stages=[{start:0,end:3,visual:{asset_id:'person'}},{start:3,end:6,visual:{asset_id:'person',placement:'left',treatment:'contain'}},
    {start:6,end:9,visual:{asset_id:'other'}},{start:9,end:12,visual:{asset_id:'person'}}];
  assert.deepEqual(visualSpan(stages,0),visualSpan(stages,1));
  assert.equal(visualSpan(stages,1).end,6);
  assert.equal(visualSpan(stages,3).start,9);
});
test('visual placement changes form a new continuous span without leaking into text-only stages',()=>{
  const stages=[{start:0,end:3,visual:{asset_id:'person'}},{start:3,end:6,visual:{asset_id:'person',placement:'right'}},{start:6,end:9}];
  assert.equal(visualSpan(stages,1).start,3);
  assert.equal(visualSpan(stages,2),null);
});
test('editorial focus is limited to multi-stage single text-only content',()=>{
  const stage={layout:'findings',headline:'发现预后分层线索',items:[{title:'预后分层线索',body:'说明'}]};
  assert(usesEditorialFocus(stage,3));
  assert(!usesEditorialFocus(stage,1));
  assert(!usesEditorialFocus({...stage,visual:{asset_id:'person'}},3));
  assert(!usesEditorialFocus({...stage,layout:'evidence'},3));
  for(const patch of [{value:'42'},{unit:'%'},{asset_id:'evidence'}]) assert(!usesEditorialFocus({...stage,items:[{...stage.items[0],...patch}]},3));
  assert(!usesEditorialFocus({...stage,items:[stage.items[0],stage.items[0]]},3));
});
test('editorial labels remove only already-covered words and duplicate ordinal',()=>{
  const stage={headline:'发现预后分层线索',eyebrow:'贡献 01',items:[{title:'预后分层线索',body:'原始说明'}]};
  const copy=structuredClone(stage);
  assert.deepEqual(editorialContext(stage,0),{ordinal:'01',eyebrow:'贡献',context:''});
  assert.deepEqual(stage,copy);
  assert.equal(editorialContext({...stage,items:[{title:'仍需独立验证'}]},0).context,'仍需独立验证');
  assert.equal(editorialContext({...stage,eyebrow:'研究202501'},0).eyebrow,'研究202501');
});
