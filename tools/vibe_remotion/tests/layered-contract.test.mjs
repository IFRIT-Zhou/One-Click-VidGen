import test from 'node:test';
import assert from 'node:assert/strict';
import {
  LAYERED_VERSION,LAYERED_RENDERER,validateLayeredInput,layeredProofFrames,layerMotion,visibleImageFadeStops,
} from '../lib/layered-contract.mjs';

// Deliberately nonexistent local files: contract validation is offline and must
// not read assets, touch project files, or contact a model/image provider.
const fixture=()=>({
  plan:{
    version:'ocv-layered-v1',canvas:[1920,1080],background:'#F3EEE4',fade_in:0,fade_out:0,
    elements:[
      {id:'background',type:'image',role:'background',box:[0,0,1920,1080],at:0,end:13.8,
        enter:0,exit:0,motion:'none',asset_id:'background',fit:'cover'},
      {id:'professor',type:'image',box:[60,60,900,1020],at:.1,enter:.6,
        motion:'fade',asset_id:'professor',fit:'contain',shadow:'soft'},
      {id:'gold_patch',type:'image',box:[130,790,710,160],at:.6,enter:.45,
        motion:'slide_up',asset_id:'gold_patch',fit:'contain',rotation:-2},
      {id:'institution',type:'text',box:[1040,210,760,580],at:1.2,
        text:'机构甲\n机构乙',font_size:56,bold:true,align:'left',fill:'#172F3A'},
    ],
  },
  assets:{
    background:'G:/isolated-layered-fixture/background.png',
    professor:'G:/isolated-layered-fixture/professor.png',
    gold_patch:'G:/isolated-layered-fixture/gold-patch.png',
  },
  duration:13.8,fps:30,
});

test('bounded typography and original gradient card decoration remain allowlisted',()=>{
  const input=fixture(),label=input.plan.elements[3];
  Object.assign(label,{valign:'middle',min_font_size:34,gradient:{color:'#A0EEFF',angle:180}});
  input.plan.elements.splice(3,0,{id:'card',type:'rect',box:[1040,210,760,580],
    fill:'#06253A',gradient:{color:'#10456A',angle:180},stroke:'#39DAEE',stroke_width:2,radius:18,glow:6});
  const out=validateLayeredInput(input);
  assert.equal(out.plan.elements[4].min_font_size,34);
  assert.equal(out.plan.elements[3].glow,6);
  label.min_font_size=100;
  assert.throws(()=>validateLayeredInput(input),/minimum font size/);
  label.min_font_size=34;label.gradient.color='url(secret)';
  assert.throws(()=>validateLayeredInput(input),/gradient color/);
});

test('layered versions are distinct from the two existing render paths',()=>{
  assert.equal(LAYERED_VERSION,'ocv-layered-v1');
  assert.equal(LAYERED_RENDERER,'ocv-remotion-layered-v1');
});

test('three independent image assets and native text validate without mutating input',()=>{
  const input=fixture(),copy=structuredClone(input),result=validateLayeredInput(input);
  assert.deepEqual(input,copy);
  assert.notEqual(result,input);
  assert.notEqual(result.plan,input.plan);
  assert.deepEqual(result.assets,input.assets);
  assert.deepEqual(result.plan.elements.map(e=>[e.id,e.type,e.asset_id]),[
    ['background','image','background'],['professor','image','professor'],
    ['gold_patch','image','gold_patch'],['institution','text',undefined],
  ]);
  assert.equal(result.plan.elements[3].text,'机构甲\n机构乙');
});

test('missing optional layer values are normalized independently',()=>{
  const result=validateLayeredInput(fixture()),professor=result.plan.elements[1],text=result.plan.elements[3];
  assert.equal(professor.end,13.8);
  assert.equal(professor.exit,0);
  assert.equal(professor.role,'content');
  assert.equal(professor.opacity,1);
  assert.equal(professor.rotation,0);
  assert.equal(text.enter,.6);
  assert.equal(text.exit,0);
  assert.equal(text.end,13.8);
  assert.equal(text.font_family,'sans');
  assert.equal(text.line_height,1.2);
  assert.equal(text.letter_spacing,0);
  assert.equal(text.shadow,'none');
});

test('images must bind existing asset IDs rather than arbitrary URLs or missing slots',()=>{
  for(const change of [
    i=>delete i.assets.professor,
    i=>delete i.plan.elements[1].asset_id,
    i=>i.plan.elements[1].asset_id='https://example.test/person.png',
    i=>i.plan.elements[1].src='https://example.test/person.png',
    i=>i.plan.elements[1].fit='fill',
  ]){
    const input=fixture();change(input);assert.throws(()=>validateLayeredInput(input));
  }
});

test('allowlisted layers reject code, HTML, CSS and unknown runtime fields',()=>{
  for(const change of [
    i=>i.js='alert(1)',
    i=>i.plan.component='CustomComponent',
    i=>i.plan.elements[1].style={position:'fixed'},
    i=>i.plan.elements[3].html='<script>alert(1)</script>',
    i=>i.plan.elements[3].dangerouslySetInnerHTML={__html:'hello'},
    i=>i.plan.elements[3].font_family='url(https://example.test/font.woff)',
    i=>i.plan.elements[1].shadow='0 0 999px red',
    i=>i.plan.elements[3].fill='url(https://example.test/pattern.svg)',
  ]){
    const input=fixture();change(input);assert.throws(()=>validateLayeredInput(input));
  }
});

test('right-edge feather is a bounded numeric foreground-image option only',()=>{
  for(const v of [0,.12,.25]){
    const i=fixture();i.plan.elements[1].edge_fade_right=v;
    assert.equal(validateLayeredInput(i).plan.elements[1].edge_fade_right,v);
  }
  assert.equal(validateLayeredInput(fixture()).plan.elements[1].edge_fade_right,undefined);
  for(const v of [null,false,-.01,.251,Infinity,NaN,'16%','url(example)']){
    const i=fixture();i.plan.elements[1].edge_fade_right=v;assert.throws(()=>validateLayeredInput(i));
  }
  for(const index of [0,3]){
    const i=fixture();i.plan.elements[index].edge_fade_right=.1;assert.throws(()=>validateLayeredInput(i));
  }
});

test('feather follows visible content in contain boxes, and the visible crop in cover boxes',()=>{
  assert.deepEqual(visibleImageFadeStops(240,128,80,160,'contain',.25),{start:136,end:152});
  assert.deepEqual(visibleImageFadeStops(240,128,480,80,'contain',.25),{start:180,end:240});
  assert.deepEqual(visibleImageFadeStops(240,128,80,160,'cover',.25),{start:180,end:240});
  assert.deepEqual(visibleImageFadeStops(240,128,480,80,'cover',.25),{start:180,end:240});
  assert.throws(()=>visibleImageFadeStops(240,128,0,160,'contain',.25));
});

test('scene envelope rejects wrong versions, frame rates and absent content',()=>{
  for(const change of [
    i=>i.plan.version='ocv-motion-v1',
    i=>i.fps=24,
    i=>i.duration=0,
    i=>i.duration=61,
    i=>i.duration=Infinity,
    i=>i.plan.canvas=[1921,1080],
    i=>i.plan.canvas=[1920,1920],
    i=>i.plan.elements=[],
    i=>i.plan.elements[1].id='background',
  ]){
    const input=fixture();change(input);assert.throws(()=>validateLayeredInput(input));
  }
});

test('layer boxes and timing cannot overflow the canvas or the narration',()=>{
  for(const change of [
    e=>e.box=[-1,60,900,1020],
    e=>e.box=[60,60,1900,1020],
    e=>e.box=[60,60,900,1021],
    e=>e.box=[60,60,0,1020],
    e=>e.box=[60,60,NaN,1020],
    e=>e.at=-.01,
    e=>e.at=13.8,
    e=>e.end=13.81,
    e=>e.end=.1,
    e=>e.enter=14,
    e=>e.exit=14,
    e=>e.opacity=1.01,
    e=>e.rotation=16,
    e=>Object.assign(e,{at:13.79,end:13.8,enter:0}),
    e=>Object.assign(e,{box:[0,0,900,1000],rotation:10}),
  ]){
    const input=fixture();change(input.plan.elements[1]);assert.throws(()=>validateLayeredInput(input));
  }
});

test('full-scene background is present continuously without a fade or white flash',()=>{
  for(const change of [
    i=>i.plan.elements[0].at=.1,
    i=>i.plan.elements[0].end=13,
    i=>i.plan.elements[0].enter=.1,
    i=>i.plan.elements[0].exit=.1,
    i=>i.plan.elements[0].motion='fade',
    i=>i.plan.elements[0].fit='contain',
    i=>i.plan.elements[0].box=[0,0,960,540],
    i=>i.plan.elements.shift(),
    i=>i.plan.fade_in=.1,
    i=>i.plan.fade_out=.1,
  ]){
    const input=fixture();change(input);assert.throws(()=>validateLayeredInput(input));
  }
});

test('text must be real copy with bounded typographic parameters',()=>{
  for(const change of [
    e=>e.text='',
    e=>e.text=' \n ',
    e=>e.font_size=11,
    e=>e.font_size=201,
    e=>e.bold='yes',
    e=>e.align='justify',
    e=>e.line_height=.9,
    e=>e.line_height=1.9,
    e=>e.letter_spacing=-4,
    e=>e.letter_spacing=13,
    e=>e.fill=['#172F3A'],
  ]){
    const input=fixture();change(input.plan.elements[3]);assert.throws(()=>validateLayeredInput(input));
  }
});

test('a legibility wash is optional, bounded and native-text-only',()=>{
  for(const opacity of [0,.86,1]){
    const i=fixture(),halo={color:'#e9F1FC',opacity};i.plan.elements[3].readability_halo=halo;
    assert.deepEqual(validateLayeredInput(i).plan.elements[3].readability_halo,halo);
  }
  assert.equal(validateLayeredInput(fixture()).plan.elements[3].readability_halo,undefined);
  for(const halo of [null,[],{},false,{color:'#112233'},{color:'#fff',opacity:.5},{color:'url(x)',opacity:.9},
    ...[-.1,1.01,'1',NaN,Infinity].map(opacity=>({color:'#123456',opacity})),
    {color:'#000000',opacity:1,blur:'100vw'}]){
    const i=fixture();i.plan.elements[3].readability_halo=halo;assert.throws(()=>validateLayeredInput(i));
  }
  const i=fixture();i.plan.elements[1].readability_halo={color:'#112233',opacity:.95};assert.throws(()=>validateLayeredInput(i));
});

test('draw motion is limited to bounded native line/polyline points',()=>{
  const input=fixture();input.plan.elements[1].motion='draw';
  assert.throws(()=>validateLayeredInput(input));
  const line={id:'rule',type:'line',box:[1040,160,700,2],at:.7,enter:.6,motion:'draw',
    points:[[0,1],[700,1]],stroke:'#B08C42',stroke_width:2};
  const valid=fixture();valid.plan.elements.push(line);
  assert.doesNotThrow(()=>validateLayeredInput(valid));
  for(const points of [[[0,1]],[[0,1],[701,1]],[[0,1],[300,1],[700,1]]]){
    const invalid=fixture();invalid.plan.elements.push({...line,points});
    assert.throws(()=>validateLayeredInput(invalid));
  }
  const polyline=fixture();polyline.plan.elements.push({...line,type:'polyline',points:[[0,1],[350,0],[700,1]]});
  assert.doesNotThrow(()=>validateLayeredInput(polyline));
});

test('proof frames preserve standard output names and stay inside the composition',()=>{
  const input=validateLayeredInput(fixture()),proof=layeredProofFrames(input.plan,input.duration,input.fps);
  assert.deepEqual(Object.keys(proof.details),['early','middle','end']);
  assert.ok(proof.stages.length>0);
  const max=Math.round(input.duration*input.fps)-1;
  for(const frame of Object.values(proof.details)){
    assert.ok(Number.isInteger(frame)&&frame>=0&&frame<=max);
  }
  const filenames=new Set();
  for(const sample of proof.stages){
    assert.ok(Number.isInteger(sample.frame)&&sample.frame>=0&&sample.frame<=max);
    assert.equal(sample.time,sample.frame/input.fps);
    assert.match(sample.file,/^[A-Za-z0-9_-]+\.png$/);
    assert.ok(!filenames.has(sample.file));filenames.add(sample.file);
  }
});

test('background stays fully visible on the first, middle and final rendered frame',()=>{
  const input=validateLayeredInput(fixture()),background=input.plan.elements[0];
  const frames=Math.round(input.duration*input.fps);
  for(const frame of [0,Math.floor(frames/2),frames-1]){
    const motion=layerMotion(background,frame/input.fps,input.plan.canvas);
    assert.deepEqual(motion,{opacity:1,x:0,y:0,scale:1,reveal:1});
  }
});

test('foreground respects absolute start, complete entrance, exit and end boundaries',()=>{
  const layer={...validateLayeredInput(fixture()).plan.elements[1],at:1,end:4,enter:.6,exit:.3};
  const motionAt=time=>layerMotion(layer,time,[1920,1080]);
  assert.equal(motionAt(1-1/30).opacity,0);
  assert.equal(motionAt(1).opacity,0);
  assert.ok(motionAt(1.3).opacity>0&&motionAt(1.3).opacity<1);
  assert.equal(motionAt(1.6).opacity,1);
  assert.equal(motionAt(3.5).opacity,1);
  assert.ok(motionAt(3.85).opacity>0&&motionAt(3.85).opacity<1);
  assert.equal(motionAt(4).opacity,0);
  assert.equal(motionAt(4+1/30).opacity,0);
});

test('all allowlisted layer motions are deterministic, finite and bounded',()=>{
  const base={...validateLayeredInput(fixture()).plan.elements[1],at:1,end:4,enter:.6,exit:.3,opacity:.7};
  const canvas=[1920,1080],times=[-10,0,1,1.15,1.3,1.6,2.5,3.85,4,20];
  for(const motion of ['none','fade','slide_left','slide_right','slide_up','scale','grow','draw']){
    const layer={...base,motion};
    for(const time of times){
      const result=layerMotion(layer,time,canvas);
      assert.deepEqual(result,layerMotion(structuredClone(layer),time,[...canvas]));
      assert.ok(Object.values(result).every(Number.isFinite));
      assert.ok(result.opacity>=0&&result.opacity<=.7);
      assert.ok(Math.abs(result.x)<=Math.min(canvas[0]*.04,100));
      assert.ok(Math.abs(result.y)<=Math.min(canvas[1]*.04,70));
      assert.ok(result.scale>=.92&&result.scale<=1);
      assert.ok(result.reveal>=0&&result.reveal<=1);
      if(motion==='slide_left')assert.ok(result.x<=0);
      if(motion==='slide_right')assert.ok(result.x>=0);
      if(motion==='slide_up')assert.ok(result.y>=0);
    }
    const settled=layerMotion(layer,2.5,canvas);
    assert.equal(settled.opacity,.7);
    assert.equal(Math.abs(settled.x),0);
    assert.equal(Math.abs(settled.y),0);
    assert.equal(settled.scale,1);
    assert.equal(settled.reveal,1);
  }
});

test('grow and draw reveal progressively while scale lands at full size',()=>{
  const base={...validateLayeredInput(fixture()).plan.elements[1],at:1,end:4,enter:.6,exit:0};
  for(const motion of ['grow','draw']){
    const layer={...base,motion};
    const start=layerMotion(layer,1,[1920,1080]),middle=layerMotion(layer,1.3,[1920,1080]),end=layerMotion(layer,1.6,[1920,1080]);
    assert.equal(start.reveal,0);
    assert.equal(start.opacity,1);
    assert.ok(middle.reveal>0&&middle.reveal<1);
    assert.equal(end.reveal,1);
  }
  const scale={...base,motion:'scale'};
  assert.equal(layerMotion(scale,1,[1920,1080]).scale,.92);
  assert.ok(layerMotion(scale,1.3,[1920,1080]).scale<1);
  assert.equal(layerMotion(scale,1.6,[1920,1080]).scale,1);
});
