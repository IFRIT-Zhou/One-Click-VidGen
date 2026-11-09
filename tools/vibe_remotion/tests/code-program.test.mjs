import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import {compileProgram,evaluateProgram,validateProgram,CODE_PROGRAM_VERSION} from '../lib/code-program.mjs';

const ctx={frame:15,fps:30,width:1920,height:1080,duration:12,poster:false,
  measurements:{title:{width:320,height:80}},geometry:{photo:{box:[100,200,400,500],prepared_size:[400,500],anchor_available:true}}};
const evaluate=source=>evaluateProgram(compileProgram(source,ts),ctx);
const rejects=source=>assert.throws(()=>evaluate(source),/OCV_CODE_CONTRACT/);

test('component controls flex layout, mixed text styles and bounded time math',()=>{
  const scene=evaluate(`export default function Scene(ctx) {
    const progress = ctx.poster ? 1 : ease(clamp(ctx.frame / ctx.fps));
    const base = {display:'flex',alignItems:'baseline',gap:10};
    return <div style={{...base,width:ctx.width,opacity:progress}}>
      <Text id="value" style={{fontSize:180,color:'#55eeff'}}>49</Text>
      <Text id="unit" style={{fontSize:80}}>对患者</Text>
    </div>;
  }`);
  assert.equal(scene.tag,'div');assert.equal(scene.props.style.opacity,.5);
  assert.equal(scene.children[0].children[0],'49');assert.equal(scene.children[1].props.style.fontSize,80);
  assert.equal(Object.getPrototypeOf(scene.props),null);
});

test('map supports local calculations, svg paths and bound asset identifiers',()=>{
  const scene=evaluate(`function Scene(ctx) {
    const cards=[{id:'one',label:'医院'},{id:'two',label:'研究'}];
    return <Group id="cards">{cards.map((card,i)=>{
      const at=i*.3;
      return <Group key={card.id} at={at}><Asset id={card.id} box={ctx.geometry.photo.box}/><Text>{card.label}</Text><svg><path d="M 0 0 L 20 10" stroke="#ffffff"/></svg></Group>;
    })}</Group>;
  }`);
  assert.equal(scene.children.length,2);assert.equal(scene.children[1].props.at,.3);
  assert.equal(scene.children[0].children[0].props.id,'one');
});

test('literal/template/conditional/expression primitives work without JS execution',()=>{
  const scene=evaluate('function Scene(ctx){ const a=[3,4]; return <Text>{`宽 ${ctx.width}，值 ${Math.sqrt(a[0]**2+a[1]**2)}`}{false && <div/>}{null}</Text>; }');
  assert.deepEqual(scene.children,['宽 1920，值 5']);
  assert.equal(evaluate('function Scene(ctx){return <Text>A &amp; B &#x4E2D;</Text>;}').children[0],'A & B 中');
  assert.equal(evaluate('function Scene(ctx){return <Text>export</Text>;} export default Scene;').children[0],'export');
});

test('imports, host access, mutation, loops, recursion and unsafe functions are rejected',()=>{
  for(const source of [
    'import fs from "fs"; function Scene(ctx){return <div/>;}',
    'function Scene(ctx){const x=fetch("https://evil.invalid");return <div/>;}',
    'function Scene(ctx){const x=process.env;return <div/>;}',
    'function Scene(ctx){const x=globalThis;return <div/>;}',
    'function Scene(ctx){const x=new Function("return 1");return <div/>;}',
    'function Scene(ctx){ctx.frame=0;return <div/>;}',
    'function Scene(ctx){let x=0;return <div/>;}',
    'function Scene(ctx){while(true){}return <div/>;}',
    'function Scene(ctx){for(const x of [1]){}return <div/>;}',
    'function Scene(ctx){return Scene(ctx);}',
    'async function Scene(ctx){return <div/>;}',
    'function Scene(ctx){const x=()=>fetch("https://evil.invalid");return <Text>{x()}</Text>;}',
    'function Scene(ctx){const x={get value(){return 1;}};return <div/>;}',
    'function Scene(ctx){return <script>alert(1)</script>;}',
  ])rejects(source);
});

test('prototype traversal and computed dangerous keys are blocked',()=>{
  for(const source of [
    'function Scene(ctx){return <Text>{ctx.constructor}</Text>;}',
    'function Scene(ctx){const k="con"+"structor";return <Text>{ctx[k]}</Text>;}',
    'function Scene(ctx){return <Text>{[].__proto__}</Text>;}',
    'function Scene(ctx){const v={"__proto__":{x:1}};return <div/>;}',
    'function Scene(ctx){return <Text>{"x".toString()}</Text>;}',
    'function Scene(ctx){return <Text>{[1].map.constructor}</Text>;}',
    'function Scene(ctx){return <Text>{ctx["toString"]}</Text>;}',
  ])rejects(source);
});

test('events, HTML injection, external resources and CSS capabilities are blocked',()=>{
  for(const source of [
    'function Scene(ctx){return <div onClick="run"/>;}',
    'function Scene(ctx){return <div dangerouslySetInnerHTML={{__html:"x"}}/>;}',
    'function Scene(ctx){return <svg><path href="https://evil.invalid"/></svg>;}',
    'function Scene(ctx){return <Asset src="file:///private"/>;}',
    'function Scene(ctx){return <div style={{background:"url(https://evil.invalid)"}}/>;}',
    'function Scene(ctx){return <div style={{background:"u"+"rl(data:text/plain,x)"}}/>;}',
    'function Scene(ctx){return <div style={{backgroundImage:\'image-set("//evil.invalid/p.png" 1x)\'}}/>;}',
    'function Scene(ctx){return <div style={{backgroundImage:\'-webkit-image-set("/private" 1x)\'}}/>;}',
    'function Scene(ctx){return <div style={{behavior:"none"}}/>;}',
    'function Scene(ctx){return <div {...{onClick:"x"}}/>;}',
  ])rejects(source);
});

test('serialized AST is independently verified, not treated as trusted bytecode',()=>{
  const program=compileProgram('function Scene(ctx){return <Text>safe</Text>;}',ts);
  const variants=[
    {...program,evil:'code'},
    {...program,version:'arbitrary-js'},
    {...program,body:[{kind:'eval',source:'evil'}]},
    {...program,body:[{kind:'return',value:{kind:'read',name:'globalThis'}}]},
    {...program,body:[{kind:'return',value:{kind:'call',helper:'Math.constructor',args:[]}}]},
  ];
  for(const value of variants)assert.throws(()=>evaluateProgram(value,ctx),/OCV_CODE_CONTRACT/);
  assert.equal(validateProgram(JSON.parse(JSON.stringify(program))).version,CODE_PROGRAM_VERSION);
});

test('ctx never exposes prototypes, functions, getters or circular objects',()=>{
  const program=compileProgram('function Scene(ctx){return <Text>{ctx.width}</Text>;}',ts);
  const cyclic={width:10};cyclic.self=cyclic;
  const getter={};Object.defineProperty(getter,'width',{get(){throw new Error('must not execute getter');},enumerable:true});
  for(const value of [cyclic,getter,{width:()=>1},new Date(),JSON.parse('{"__proto__":{"x":1}}')]){
    assert.throws(()=>evaluateProgram(program,value),/OCV_CODE_CONTRACT/);
  }
  const safe=Object.assign(Object.create(null),{width:123});
  assert.equal(evaluateProgram(program,safe).children[0],123);
});

test('finite arithmetic, valid indexes and helper arity are enforced',()=>{
  for(const source of [
    'function Scene(ctx){return <Text>{1/0}</Text>;}',
    'function Scene(ctx){return <Text>{Math.sqrt(-1)}</Text>;}',
    'function Scene(ctx){return <Text>{[1][2]}</Text>;}',
    'function Scene(ctx){return <Text>{clamp(1,2,0)}</Text>;}',
    'function Scene(ctx){return <Text>{lerp(0,1)}</Text>;}',
    'function Scene(ctx){return <Text>{ctx.missing}</Text>;}',
    'function Scene(ctx){return <Text>{[1]+"x"}</Text>;}',
  ])rejects(source);
});

test('large/recursive workloads are bounded',()=>{
  rejects('function Scene(ctx){const a=['+Array(129).fill('1').join(',')+'];return <div/>;}');
  const source='function Scene(ctx){const a=['+Array(64).fill('1').join(',')+'];return <div>{a.map(i=>a.map(j=><div>{i+j}</div>))}</div>;}';
  rejects(source);
  rejects('function Scene(ctx){return <Text>'+('a'.repeat(13000))+'</Text>;}');
  const program={version:CODE_PROGRAM_VERSION,body:[{kind:'return',value:null}]};
  let node={kind:'literal',value:1};for(let i=0;i<70;i++)node={kind:'unary',operator:'!',value:node};
  program.body[0].value=node;assert.throws(()=>validateProgram(program),/complexity/);
});

test('only the named component and lexically bound variables are allowed',()=>{
  for(const source of [
    'function Other(ctx){return <div/>;}',
    'function Scene(data){return <div/>;}',
    'function Scene(ctx){const x=y;return <div/>;}',
    'function Scene(ctx){const x=1;const x=2;return <div/>;}',
    'function Scene(ctx){const ctx={};return <div/>;}',
    'function Scene(ctx){return <div/>;} function helper(){}',
    'function Scene(ctx){return <div/>;const x=1;}',
  ])rejects(source);
});

test('plain objects cannot forge JSX nodes or inject unchecked properties',()=>{
  for(const source of [
    'function Scene(ctx){return {tag:"div",props:{dangerouslySetInnerHTML:{__html:"bad"}},children:[]};}',
    'function Scene(ctx){const x={tag:"div",props:{onClick:"bad"},children:[]};return <Group>{x}</Group>;}',
    'function Scene(ctx){return <Group>{ctx.fake}</Group>;}',
  ])rejects(source);
  const program=compileProgram('function Scene(ctx){return <Group>{ctx.fake}</Group>;}',ts);
  assert.throws(()=>evaluateProgram(program,{...ctx,fake:{tag:'div',props:{},children:[]}}),/OCV_CODE_CONTRACT/);
});

test('serialized instructions and context reject accessors, symbols and custom array methods without calling them',()=>{
  const program=compileProgram('function Scene(ctx){return <Text>safe</Text>;}',ts);
  const getter={};Object.defineProperty(getter,'version',{get(){assert.fail('AST getter was invoked');},enumerable:true});
  assert.throws(()=>validateProgram(getter),/getters/);
  const array=[1];Object.defineProperty(array,'0',{get(){assert.fail('array getter was invoked');},enumerable:true});
  const method=[1];method.map=()=>assert.fail('custom map was invoked');
  const symbol={width:1};symbol[Symbol('unsafe')]=1;
  for(const value of [{values:array},{values:method},symbol])assert.throws(()=>evaluateProgram(program,value),/OCV_CODE_CONTRACT/);
  const forged={...program,body:[...program.body]};
  forged.body.forEach=()=>assert.fail('AST array method was invoked');
  assert.throws(()=>validateProgram(forged),/Custom array/);
  const detached=validateProgram(program);assert.notEqual(detached,program);assert.notEqual(detached.body,program.body);
});

test('reusing rendered nodes cannot expand a small program into an unbounded DOM',()=>{
  const values='['+Array(64).fill('1').join(',')+']';
  rejects('function Scene(ctx){const a='+values+';const leaf=<Text>same</Text>;const row=<Group>{a.map(i=>leaf)}</Group>;return <Group>{a.map(i=>row)}</Group>;}');
  const safe=evaluate('function Scene(ctx){const child=<Text>same</Text>;return <Group>{[1,2].map(i=>child)}</Group>;}');
  assert.equal(safe.children.length,2);
});

test('CSS escape/comment tricks and excessive source nesting are rejected',()=>{
  rejects('function Scene(ctx){return <div style={{background:"u\\\\72l(https://evil.invalid)"}}/>;}');
  rejects('function Scene(ctx){return <div style={{background:"u/**/rl(test)"}}/>;}');
  rejects('function Scene(ctx){return <Text>{'+'('.repeat(1000)+'1'+')'.repeat(1000)+'}</Text>;}');
});

test('pure arrow helpers can construct SVG, computed styles and reusable groups',()=>{
  const result=evaluate(`function Scene(ctx){
    const groupFrame=(at)=>{
      const amount=ease(clamp((ctx.frame/ctx.fps-at)/.7));
      return {opacity:amount,transform:'translateY('+lerp(12,0,amount)+'px)'};
    };
    const arrow=(dir,x,y)=><svg width="80" height="30"><g transform={'translate('+x+','+y+')'}><path d={dir==='left'?'M 30 0 L 0 15 L 30 30':'M 0 0 L 30 15 L 0 30'} stroke="#55ccff"/></g></svg>;
    const label=(id,value)=><Text id={id} style={{fontSize:40}}>{value}</Text>;
    return <Group style={groupFrame(0)}>{arrow('left',1,0)}{label('title','比较结果')}</Group>;
  }`);
  assert.equal(result.children[0].tag,'svg');
  assert.equal(result.children[0].children[0].children[0].props.d,'M 30 0 L 0 15 L 30 30');
  assert.equal(result.children[1].children[0],'比较结果');
  assert.ok(result.props.style.opacity>.5&&result.props.style.opacity<1);
});

test('local function declarations and anonymous function-expression map callbacks remain interpreted',()=>{
  const result=evaluate(`function Scene(ctx){
    function format(value){ return '医院 '+value; }
    const label=function(value,index){const text=format(value);return <Text id={'hospital_'+index}>{text}</Text>;};
    return <Group>{['甲','乙'].map(function(value,index){return label(value,index);})}</Group>;
  }`);
  assert.deepEqual(result.children.map(x=>x.children[0]),['医院 甲','医院 乙']);
});

test('helper parameters may carry pure data or trusted JSX nodes but never function capabilities',()=>{
  const result=evaluate(`function Scene(ctx){
    const wrapper=(child,style)=><Group style={style}>{child}</Group>;
    const style=(x)=>({left:x,top:20});
    return wrapper(<Text id="title">安全</Text>,style(10));
  }`);
  assert.equal(result.props.style.left,10);
  assert.equal(result.children[0].children[0],'安全');
  for(const source of [
    'function Scene(ctx){const helper=()=>1;return <div style={helper}/>;}',
    'function Scene(ctx){const helper=()=>1;const object={fn:helper};return <Text>bad</Text>;}',
    'function Scene(ctx){const helper=()=>1;const a=[helper];return <Text>bad</Text>;}',
    'function Scene(ctx){const helper=()=>1;return <Text>{helper}</Text>;}',
    'function Scene(ctx){const maker=()=>()=>1;return <Text>{maker()}</Text>;}',
    'function Scene(ctx){const helper=()=>1;const alias=helper;return <Text>{alias()}</Text>;}',
    'function Scene(ctx){const apply=(fn)=>fn();const helper=()=>1;return <Text>{apply(helper)}</Text>;}',
  ])rejects(source);
});

test('direct, mutual, nested recursion and host-calling helpers are rejected before interpretation',()=>{
  for(const source of [
    'function Scene(ctx){const recurse=(x)=>recurse(x);return <Text>{recurse(1)}</Text>;}',
    'function Scene(ctx){const first=()=>second();const second=()=>first();return <Text>{first()}</Text>;}',
    'function Scene(ctx){function first(){function second(){return first();}return second();}return <Text>{first()}</Text>;}',
    'function Scene(ctx){const helper=async()=>1;return <Text>{helper()}</Text>;}',
    'function Scene(ctx){function* helper(){yield 1;}return <Text>{helper()}</Text>;}',
    'function Scene(ctx){const helper=()=>new Date();return <Text>{helper()}</Text>;}',
    'function Scene(ctx){const helper=()=>1;return <Text>{helper.call(null)}</Text>;}',
    'function Scene(ctx){const helper=()=>1;return <Text>{helper.constructor("evil")}</Text>;}',
    'function Scene(ctx){return <Text>{ctx.helper()}</Text>;}',
  ])rejects(source);
});

test('helper call depth and operation budgets prevent exponential expansion',()=>{
  let chain='const helper0=()=>1;';
  for(let i=1;i<35;i++)chain+=`const helper${i}=()=>helper${i-1}();`;
  rejects(`function Scene(ctx){${chain}return <Text>{helper34()}</Text>;}`);
  let exponential='const helper0=()=>1;';
  for(let i=1;i<20;i++)exponential+=`const helper${i}=()=>helper${i-1}()+helper${i-1}();`;
  rejects(`function Scene(ctx){${exponential}return <Text>{helper19()}</Text>;}`);
});

test('helper arity, unsupported parameters and source diagnostics are actionable',()=>{
  const cases=[
    ['function Scene(ctx){\n const helper=(a)=>a;\n return <Text>{helper()}</Text>;\n}',/expects 1 explicit arguments.*\[line 3, column/],
    ['function Scene(ctx){\n const helper=({a})=>a;\n return <Text>bad</Text>;\n}',/simple names.*\[line 2, column/],
    ['function Scene(ctx){\n const helper=(a=1)=>a;\n return <Text>bad</Text>;\n}',/default values explicitly.*\[line 2, column/],
    ['function Scene(ctx){\n const bad=()=>unknown();\n return <Text>{bad()}</Text>;\n}',/Unknown or recursive helper.*\[line 2, column/],
    ['function Scene(ctx){\n let x=1;\n return <Text>{x}</Text>;\n}',/use const value.*\[line 2, column/],
  ];
  for(const [source,message] of cases)assert.throws(()=>compileProgram(source,ts),message);
  assert.throws(()=>evaluate('function Scene(ctx){\n const f=(x)=>x/0;\n return <Text>{f(1)}</Text>;\n}'),/finite numeric value.*\[line 2, column/);
});

test('forged helper AST and source locations are independently checked',()=>{
  const program=compileProgram('function Scene(ctx){const helper=(x)=>x;return <Text>{helper(1)}</Text>;}',ts);
  assert.equal(evaluateProgram(JSON.parse(JSON.stringify(program)),ctx).children[0],1);
  const recursive=JSON.parse(JSON.stringify(program));
  recursive.body[0].value.body[0].value={kind:'invoke',name:'helper',args:[{kind:'literal',value:1}]};
  assert.throws(()=>validateProgram(recursive),/recursive helper/);
  const escaped=JSON.parse(JSON.stringify(program));
  escaped.body[1].value.props.push({key:'data',value:{kind:'read',name:'helper'}});
  assert.throws(()=>validateProgram(escaped),/cannot escape/);
  const location=JSON.parse(JSON.stringify(program));location.body[0].loc={line:1,column:1,source:'evil'};
  assert.throws(()=>validateProgram(location),/source location/);
});
