import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import {compileProgram,evaluateProgram,validateProgram,CODE_LIMITS} from '../lib/code-program.mjs';

const ctx={frame:15,fps:30,width:1920,height:1080,duration:12,poster:false};
const evaluate=source=>evaluateProgram(compileProgram(source,ts),ctx);
const value=expression=>evaluate(`function Scene(ctx){return <Text>{${expression}}</Text>;}`).children[0];
const rejects=expression=>assert.throws(()=>value(expression),/OCV_CODE_CONTRACT/);

test('String converts only bounded primitive values, including null without invoking object conversion',()=>{
  for(const [expression,expected] of [
    ['String(78.0)','78'],['String(-0)','0'],['String(true)','true'],
    ['String(false)','false'],['String(null)','null'],['String("中文")','中文'],
  ])assert.equal(value(expression),expected);
  const scene=evaluate(`function Scene(ctx){const ticks=[0,20,40,60,80,100];return <Group>{ticks.map(tick=><Text>{String(tick)+'%'}</Text>)}</Group>;}`);
  assert.deepEqual(scene.children.map(child=>child.children[0]),['0%','20%','40%','60%','80%','100%']);
  for(const expression of ['String()','String(1,2)','String([])','String({label:"x"})','String(<Text>x</Text>)','String(1/0)'])rejects(expression);
  assert.throws(()=>evaluate('function Scene(ctx){const helper=()=>1;return <Text>{String(helper)}</Text>;}'),/OCV_CODE_CONTRACT/);
});

test('String does not expose a constructor, namespace, prototype or shadowable function value',()=>{
  for(const expression of ['String','String.prototype','String.constructor','String.fromCharCode(65)','String.call(null,1)','new String(1)'])rejects(expression);
  assert.throws(()=>evaluate('function Scene(ctx){const String=(x)=>x;return <Text>{String(1)}</Text>;}'),/OCV_CODE_CONTRACT/);
  const program=compileProgram('function Scene(ctx){return <Text>{String(ctx.value)}</Text>;}',ts);
  let converted=false;
  const object={toString(){converted=true;return 'unsafe';}};
  assert.throws(()=>evaluateProgram(program,{...ctx,value:object}),/OCV_CODE_CONTRACT/);
  assert.equal(converted,false);
});

test('string.replace supports evaluated strings, chaining and first-match native substitution',()=>{
  assert.equal(value('"foo foo".replace("foo","bar")'),'bar foo');
  assert.equal(value('"foo".replace("missing","bar")'),'foo');
  assert.equal(value('"foo".replace("","pre-")'),'pre-foo');
  assert.equal(value('("a"+" b").replace(" ","_").replace("b","c")'),'a_c');
  assert.equal(value('"中文标题".replace("标题","标签")'),'中文标签');
  const input='abc',search='b',replacement='$$:$&:$`:$\'';
  assert.equal(value(`${JSON.stringify(input)}.replace(${JSON.stringify(search)},${JSON.stringify(replacement)})`),input.replace(search,replacement));
  assert.equal(evaluate(`function Scene(ctx){const item={id:'tag one',find:' ',replacement:'_'};return <Text>{item.id.replace(item.find,item.replacement)}</Text>;}`).children[0],'tag_one');
});

test('replace cannot dispatch arbitrary object methods, regexes, callbacks or prototype operations',()=>{
  for(const expression of [
    '"abc".replace("a")','"abc".replace("a","b","c")',
    '"abc".replace(/a/,"b")','"abc".replace(/a/g,"b")',
    '"abc".replace("a",()=>"b")','"abc".replace(1,"b")','"abc".replace("a",1)',
    '["a"].replace("a","b")','({replace:"x"}).replace("a","b")',
    '"abc"["replace"]("a","b")','"abc".replace.call(null,"a","b")',
    '"abc".replace?.("a","b")','"abc"?.replace("a","b")',
  ])rejects(expression);
  assert.throws(()=>evaluate('function Scene(ctx){const replacement=()=>"b";return <Text>{"abc".replace("a",replacement)}</Text>;}'),/OCV_CODE_CONTRACT/);
  assert.throws(()=>evaluate('function Scene(ctx){const replace="abc".replace;return <Text>{replace("a","b")}</Text>;}'),/OCV_CODE_CONTRACT/);
});

test('regex-based Text IDs explain the unsupported operation and preserve visible wording in the repair',()=>{
  const source=`function Scene(ctx){
    const rows=[{panel:'左侧',name:'阿伐曲泊帕'},{panel:'右侧',name:'rh-TPO'}];
    return <Group>{rows.map((b,index)=><Text id={(b.panel + '_' + b.name).replace(/[^A-Za-z0-9_:-]/g, '')}>{b.name}</Text>)}</Group>;
  }`;
  assert.throws(()=>evaluate(source),error=>{
    assert.match(error.message,/string searches only, not regular expressions/);
    assert.match(error.message,/Text\.id.*ASCII prefix.*map callback index/);
    assert.match(error.message,/label_.*panelIndex/);
    assert.match(error.message,/Do not build IDs from display wording/);
    assert.match(error.message,/do not delete visible text/);
    return true;
  });
  const repaired=source.replace("(b.panel + '_' + b.name).replace(/[^A-Za-z0-9_:-]/g, '')","'label_'+index");
  const scene=evaluate(repaired);
  assert.deepEqual(scene.children.map(child=>child.props.id),['label_0','label_1']);
  assert.deepEqual(scene.children.map(child=>child.children[0]),['阿伐曲泊帕','rh-TPO']);
  assert.throws(()=>value('"abc".replace((/a/g),"b")'),/string searches only, not regular expressions/);
});

test('serialized helper AST independently validates arity, fields and operand data types',()=>{
  const program=compileProgram('function Scene(ctx){return <Text>{String(12).replace("1","2")}</Text>;}',ts);
  assert.equal(evaluateProgram(JSON.parse(JSON.stringify(program)),ctx).children[0],'22');
  // Traverse via the actual serialized JSX child shape rather than assuming it
  // has already been trusted by compilation.
  const findReplace=node=>node&&typeof node==='object'?(node.kind==='string_replace'?node:Object.values(node).map(findReplace).find(Boolean)):undefined;
  assert.ok(findReplace(program));
  for(const mutate of [
    node=>{node.extra='execute';},
    node=>{node.value.helper='String.constructor';},
    node=>{node.value.args=[];},
    node=>{node.value.args.push({kind:'literal',value:2});},
    node=>{node.search={kind:'literal',value:1};},
    node=>{node.replacement={kind:'literal',value:{toString:'x'}};},
  ]){
    const copy=JSON.parse(JSON.stringify(program));mutate(findReplace(copy));
    assert.throws(()=>evaluateProgram(copy,ctx),/OCV_CODE_CONTRACT/);
  }
  assert.deepEqual(validateProgram(program),program);
});

test('replace output size and character-weighted operation budget are bounded',()=>{
  const long='a'.repeat(CODE_LIMITS.string);
  assert.equal(value(`${JSON.stringify(long)}.replace("a","")`).length,CODE_LIMITS.string-1);
  assert.throws(()=>value(`${JSON.stringify(long)}.replace("a","aa")`),/String exceeds limit/);
  const source=`function Scene(ctx){const input=${JSON.stringify(long)};const rows=[${Array(128).fill('1').join(',')}];return <Group>{rows.map(i=><Text>{input.replace('a','b').replace('b','a').replace('x','y')}</Text>)}</Group>;}`;
  assert.throws(()=>evaluate(source),/Operation budget exceeded/);
});

test('replacement length preflight preserves native token semantics and no-match behavior',()=>{
  for(const input of ['abc','中😀文',''])for(const search of ['',input,'b','missing']){
    for(const replacement of ['','$$','$&','$`',"$'",'$1/$01/$99/$<name>','$','$$&','$$`',"$$'",'$$$&','$&$`$\'','x$$y$&z']){
      assert.equal(value(`${JSON.stringify(input)}.replace(${JSON.stringify(search)},${JSON.stringify(replacement)})`),input.replace(search,replacement));
    }
  }
  const long='a'.repeat(CODE_LIMITS.string),tokens="$'".repeat(CODE_LIMITS.string/2);
  assert.equal(value(`${JSON.stringify(long)}.replace("absent",${JSON.stringify(tokens)})`),long);
  // Replacing the whole source has empty prefix/suffix; a token-heavy
  // replacement is safe when its actual expansion fits, even at input limits.
  assert.equal(value(`${JSON.stringify(long)}.replace(${JSON.stringify(long)},${JSON.stringify(tokens)})`),'');
  assert.equal(value(`${JSON.stringify(long)}.replace(${JSON.stringify(long)},"$&")`),long);
});

test('expanding replacements are rejected before native replace can allocate their output',()=>{
  const input='a'.repeat(CODE_LIMITS.string/2)+'b'+'c'.repeat(CODE_LIMITS.string/2-1);
  for(const replacement of ['$`'.repeat(CODE_LIMITS.string/2),"$'".repeat(CODE_LIMITS.string/2),'$&'.repeat(CODE_LIMITS.string/2)]){
    const program=compileProgram(`function Scene(ctx){return <Text>{${JSON.stringify(input)}.replace('b',${JSON.stringify(replacement)})}</Text>;}`,ts);
    const nativeReplace=String.prototype.replace;
    let allocated=false;
    String.prototype.replace=function(search,substitution){
      if(this===input&&search==='b'&&substitution===replacement){allocated=true;throw new Error('native expansion must not execute');}
      return nativeReplace.call(this,search,substitution);
    };
    try{
      assert.throws(()=>evaluateProgram(program,ctx),/String exceeds limit before replacement expansion/);
      assert.equal(allocated,false);
    }finally{String.prototype.replace=nativeReplace;}
  }
});
