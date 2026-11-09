import test from 'node:test';
import assert from 'node:assert/strict';
import {assetPlacement, safeStyle, validateTree, validateCodeInput, codeProofFrames, CODE_RENDERER,treeManifest,validateFrameManifest,computedColorAlpha,backgroundCoverage,textOverflowEvidence} from '../lib/code-render-contract.mjs';

const geometry={person:{box:[50,80,400,600],prepared_size:[800,1200],anchor_available:true}};
const asset={person:'approved-data-url',background:'approved-data-url'};
const text=(id='title',content='研究团队')=>({tag:'Text',props:{id,style:{fontSize:48}},children:[content]});
const base={canvas:[1280,720],duration:10,fps:30,mode:'preview',code:'function Scene(ctx) { return <Text id="title">Title</Text>; }',assets:{background:'G:/background.png'},geometry:{},measurements:{}};

test('source anchoring maps crop into original position without stretching',()=>{
  assert.deepEqual(assetPlacement({id:'person',anchor:'source',crop:[.25,.1,.75,.8]},geometry),{box:[150,140,200,420.00000000000006],crop:[.25,.1,.75,.8]});
  assert.throws(()=>assetPlacement({id:'person',anchor:'source',box:[0,0,400,600]},geometry),/mutually exclusive/);
  assert.throws(()=>assetPlacement({id:'person',anchor:'source',style:{width:500}},geometry),/cannot override/);
  assert.throws(()=>assetPlacement({id:'missing',anchor:'source'},geometry),/no source anchor/);
  assert.deepEqual(assetPlacement({id:'odd',anchor:'source',crop:[.1,.1,.9,.9]}, {odd:{box:[0,0,101,101],prepared_size:[101,101],anchor_available:true}}).box,[10,10,81,81]);
});
test('style supports layout, gradients, local masks but never resources or executable CSS',()=>{
  assert.deepEqual(safeStyle({display:'flex',gap:16,maskImage:'linear-gradient(to bottom, black 70%, transparent)',transform:'translateX(12px)'}),{display:'flex',gap:16,maskImage:'linear-gradient(to bottom, black 70%, transparent)',transform:'translateX(12px)'});
  for(const value of ['url(https://evil.test/x)','uRl (//evil)','image-set("//evil/x" 1x)','image-set(url(data:image/png;evil))','linear-gradient(black,red); color:red','\\75rl(x)','expression(alert(1))'])assert.throws(()=>safeStyle({background:value}),/Unsafe/);
  for(const key of ['animation','transition','onClick','--payload','src'])assert.throws(()=>safeStyle({[key]:'x'}),/Unsupported/);
});
test('Text owns real content and inline spans; raw text, duplicate IDs and HTML are rejected',()=>{
  const title=text();title.children=[{tag:'span',props:{style:{color:'#00ffff'}},children:['研究']},'团队'];
  assert.equal(validateTree(title,asset,geometry),title);
  assert.throws(()=>validateTree({tag:'div',props:{},children:['unmeasured']},asset),/inside Text/);
  assert.throws(()=>validateTree([text(),text()],asset),/Duplicate Text/);
  assert.throws(()=>validateTree({tag:'script',props:{},children:[]},asset),/Unsupported tag/);
  assert.throws(()=>validateTree({tag:'div',props:{dangerouslySetInnerHTML:{__html:'x'}},children:[]},asset),/Unsupported/);
});
test('missing Text IDs identify the exact node and teach required IDs instead of removing them',()=>{
  const missing={tag:'Text',props:{key:'row_1'},children:['原文']};
  const tree={tag:'Group',props:{id:'intro'},children:[text('title'),{tag:'div',props:{},children:[missing]}]};
  assert.throws(()=>validateTree(tree,asset),error=>{
    assert.match(error.message,/Missing required Text\.id: received undefined/);
    assert.match(error.message,/\[node Scene\.children\[1\]\.children\[0\] <Text>\]/);
    assert.match(error.message,/<Text id="label_1">/);
    assert.match(error.message,/not key alone/);
    assert.match(error.message,/Do not remove Text\.id/);
    assert.equal((error.message.match(/\[node /g)||[]).length,1);
    return true;
  });
  assert.equal(missing.props.id,undefined,'diagnostics must not silently repair model code');
});
test('malformed IDs report the actual value and component while keeping the same accepted alphabet',()=>{
  for(const value of ['panel_0.55','中文','two words','',null,7]){
    const tree={tag:'Group',props:{id:value},children:[text()]};
    assert.throws(()=>validateTree(tree,asset),error=>{
      assert.match(error.message,/Invalid Group\.id: received/);
      assert.ok(error.message.includes(JSON.stringify(value)));
      assert.match(error.message,/\[node Scene <Group>\]/);
      assert.match(error.message,/no spaces, Chinese characters or decimal points/);
      assert.match(error.message,/id="panel_1"/);
      return true;
    });
  }
  for(const value of ['title_1','title:1','title-1','123','a'.repeat(80)])assert.doesNotThrow(()=>validateTree(text(value),asset));
  assert.throws(()=>validateTree(text('a'.repeat(81)),asset),/Invalid Text\.id/);
  assert.throws(()=>validateTree(text('x'.repeat(5000)),asset),error=>error.message.length<700);
});
test('duplicate Text IDs report both locations and preserve the unique-ID rule',()=>{
  assert.throws(()=>validateTree([text('same'),{tag:'Group',props:{id:'group'},children:[text('same')]}],asset),error=>{
    assert.match(error.message,/Duplicate Text id "same"; first used at Scene\[0\]/);
    assert.match(error.message,/\[node Scene\[1\]\.children\[0\] <Text>\]/);
    assert.match(error.message,/Every Text needs a unique id/);
    return true;
  });
});
test('background Asset feedback removes the redundant node, not the selected background',()=>{
  assert.throws(()=>validateTree({tag:'div',props:{},children:[{tag:'Asset',props:{id:'background'},children:[]}]},asset),error=>{
    assert.match(error.message,/Remove this <Asset id="background" \/> element/);
    assert.match(error.message,/already drawn underneath Scene/);
    assert.match(error.message,/\[node Scene\.children\[0\] <Asset>\]/);
    return true;
  });
  assert.throws(()=>validateTree({tag:'Asset',props:{},children:[]},asset),/Missing required Asset\.id/);
});
test('all tree validation errors retain local paths without accepting unsafe nodes',()=>{
  assert.throws(()=>validateTree({tag:'div',props:{},children:[{tag:'span',props:{style:{background:'url(file:///secret)'}},children:[]}]},asset),error=>{
    assert.match(error.message,/Unsafe style\.background/);
    assert.match(error.message,/\[node Scene\.children\[0\] <span>\]/);
    return true;
  });
  assert.throws(()=>validateTree({tag:'div',props:{},children:{}},asset),/children must be an array/);
});
test('bound assets, safe SVG and deterministic Groups',()=>{
  assert.doesNotThrow(()=>validateTree({tag:'Asset',props:{id:'person',anchor:'source'},children:[]},asset,geometry));
  for(const id of ['missing','background'])assert.throws(()=>validateTree({tag:'Asset',props:{id},children:[]},asset,geometry));
  assert.throws(()=>validateTree({tag:'Asset',props:{id:'person',src:'https://evil.test'},children:[]},asset,geometry),/Unsupported/);
  assert.throws(()=>validateTree({tag:'svg',props:{},children:[{tag:'image',props:{},children:[]}]},asset),/Unsupported/);
  assert.doesNotThrow(()=>validateTree({tag:'svg',props:{viewBox:'0 0 100 100'},children:[{tag:'path',props:{d:'M 10 10 L 90 90',stroke:'#fff'},children:[]}]},asset));
  assert.throws(()=>validateTree({tag:'Group',props:{exit:1},children:[text()]},asset,{},10),/Group.exit/);
});
test('measure does not require a program or background, actual render does',()=>{
  assert.doesNotThrow(()=>validateCodeInput({...base,mode:'measure',code:undefined,assets:{},measure_requests:[{id:'x',text:'49对患者',font_size:64,font_weight:700,font_family:'sans'}]}));
  assert.throws(()=>validateCodeInput({...base,assets:{}}),/background/);
  assert.throws(()=>validateCodeInput({...base,measure_requests:[{id:'x',text:'x',font_size:32,font_family:'https://evil.test/font'}]}),/font_family/);
  assert.throws(()=>validateCodeInput({...base,geometry:{person:{...geometry.person,arbitrary_path:'x'}}}),/Unsupported/);
  assert.doesNotThrow(()=>validateCodeInput({...base,geometry:{external:{prepared_size:[100,200],anchor_available:false,anchor_unavailable_reason:'Independent reference asset'}}}));
});
test('critical frame contract includes the final hold and honest code renderer ID',()=>{
  const frames=codeProofFrames(10);
  assert.deepEqual(frames.map(f=>f.id),['early','middle','hold','end']);
  assert.equal(frames.find(f=>f.id==='hold').time,9);
  assert.equal(CODE_RENDERER,'ocv-remotion-code-v1');
});
test('every rendered frame must retain approved wording and asset identities',()=>{
  const tree=[text('number','49'),{tag:'Asset',props:{id:'person'},children:[]}];
  const manifest=treeManifest(tree);
  assert.deepEqual(manifest,{texts:{number:'49'},assets:['person']});
  assert.doesNotThrow(()=>validateFrameManifest([],manifest));
  assert.throws(()=>validateFrameManifest(text('number','94'),manifest),/changed text/);
  assert.throws(()=>validateFrameManifest(text('other','49'),manifest),/changed text/);
  assert.throws(()=>validateFrameManifest({tag:'Asset',props:{id:'other'},children:[]},manifest),/unreviewed asset/);
});
test('measurement supports large display numbers up to 512 pixels',()=>{
  for(const font_size of [300,512])assert.doesNotThrow(()=>validateCodeInput({...base,mode:'measure',measure_requests:[{id:'number',text:'49',font_size}]}));
  assert.throws(()=>validateCodeInput({...base,mode:'measure',measure_requests:[{id:'number',text:'49',font_size:513}]}),/font_size/);
});
test('full-canvas opaque backgrounds are reported, local cards and translucent backplates are not',()=>{
  const surface={tag:'div',box:[0,0,1280,720],background_color:'rgb(0, 12, 24)',opacity:1,display:'block',visibility:'visible'};
  const evidence=backgroundCoverage(surface,[1280,720]);
  assert.equal(evidence.effective_alpha,1);assert.equal(evidence.coverage_width,1);
  assert.equal(backgroundCoverage({...surface,box:[0,0,500,300]},[1280,720]),null);
  assert.equal(backgroundCoverage({...surface,box:[500,0,1280,720]},[1280,720]),null);
  assert.equal(backgroundCoverage({...surface,background_color:'rgba(0, 12, 24, 0.7)'},[1280,720]),null);
  assert.equal(backgroundCoverage({...surface,opacity:.5},[1280,720]),null);
  assert.equal(backgroundCoverage({...surface,tag:'img'},[1280,720]),null);
  assert.equal(backgroundCoverage({...surface,display:'none'},[1280,720]),null);
  assert.equal(computedColorAlpha('rgb(1 2 3 / 97%)'),.97);
  assert.equal(computedColorAlpha('color(srgb 0 0 0 / 0.96)'),.96);
});
test('natural visible line-height ink is not overflow; constrained and clipped content is',()=>{
  const metrics={font_size:200,scroll_width:246,client_width:246,scroll_height:260,client_height:200,overflow_x:'visible',overflow_y:'visible',explicit_width:false,explicit_height:false};
  assert.equal(textOverflowEvidence(metrics),null);
  assert.equal(textOverflowEvidence({...metrics,explicit_height:true}).vertical,true);
  assert.equal(textOverflowEvidence({...metrics,overflow_y:'hidden'}).vertical,true);
  assert.equal(textOverflowEvidence({...metrics,overflow_y:'visible',scroll_width:350,explicit_width:true}).horizontal,true);
  assert.deepEqual(safeStyle({verticalAlign:'baseline'}),{verticalAlign:'baseline'});
});
