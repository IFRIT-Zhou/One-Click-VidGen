import test from 'node:test';
import assert from 'node:assert/strict';
import ts from 'typescript';
import {compileProgram, evaluateProgram} from '../lib/code-program.mjs';
import {validateTree} from '../lib/code-render-contract.mjs';

const context={width:1280,height:720,duration:10,fps:30,frame:0,poster:true,geometry:{},measurements:{}};
const assets={background:'approved-background',subject_0:'approved-subject'};
const check=source=>validateTree(evaluateProgram(compileProgram(source,ts),context),assets,{},10);

test('actual JSX fractional IDs are diagnosed with their evaluated value and stable alternative',()=>{
  const source=`function Scene(ctx) {
    const rows=[{at:0.55,label:'第一项'},{at:3.5,label:'第二项'}];
    return <div>{rows.map((row,i)=><Group id={'panel_'+row.at} at={row.at}>
      <Text id={'label_'+i}>{row.label}</Text>
    </Group>)}</div>;
  }`;
  assert.throws(()=>check(source),error=>{
    assert.match(error.message,/Invalid Group\.id: received "panel_0\.55"/);
    assert.match(error.message,/node Scene\.children\[0\] <Group>/);
    assert.match(error.message,/do not build IDs from fractional times/);
    return true;
  });
  assert.doesNotThrow(()=>check(source.replace("'panel_'+row.at","'panel_'+i")));
});

test('actual mapped Text key does not substitute for id and the diagnostic repair compiles',()=>{
  const source=`function Scene(ctx) {
    const rows=['第一项','第二项'];
    return <Group id="items">{rows.map((row,i)=><Text key={i}>{row}</Text>)}</Group>;
  }`;
  assert.throws(()=>check(source),error=>{
    assert.match(error.message,/Missing required Text\.id: received undefined/);
    assert.match(error.message,/node Scene\.children\[0\] <Text>/);
    assert.match(error.message,/id=\{'label_' \+ i\}/);
    return true;
  });
  assert.doesNotThrow(()=>check(source.replace('<Text key={i}>',"<Text key={i} id={'label_' + i}>")));
});

test('actual code duplication and fixed-background misuse remain rejected with targeted feedback',()=>{
  assert.throws(()=>check('function Scene(ctx){return <div><Text id="name">甲</Text><Text id="name">乙</Text></div>;}'),error=>{
    assert.match(error.message,/Duplicate Text id "name"; first used at Scene\.children\[0\]/);
    assert.match(error.message,/node Scene\.children\[1\] <Text>/);
    return true;
  });
  assert.throws(()=>check('function Scene(ctx){return <div><Asset id="background"/><Text id="title">标题</Text></div>;}'),error=>{
    assert.match(error.message,/Remove this <Asset id="background" \/> element/);
    assert.match(error.message,/node Scene\.children\[0\] <Asset>/);
    return true;
  });
});

test('nested unsupported JSX reports the actual child tag and location, not its outer div',()=>{
  const source=`function Scene(ctx) {
  return <div>
    <svg>
      <text>标题</text>
    </svg>
  </div>;
}`;
  assert.throws(()=>check(source),error=>{
    assert.match(error.message,/Unsupported JSX tag <text>/);
    assert.match(error.message,/outside SVG/);
    assert.match(error.message,/\[line 4, column 8\]/);
    assert.doesNotMatch(error.message,/\[line 2,/);
    return true;
  });
  assert.doesNotThrow(()=>check(source.replace('<text>标题</text>','<line x1={0} y1={0} x2={20} y2={0}/>').replace('</svg>','</svg><Text id="title">标题</Text>')));
});

test('SVG resource definitions identify the precise tag and a supported gradient alternative',()=>{
  const source='function Scene(ctx) {\n  return <div>\n    <svg>\n      <defs><linearGradient id="shine"><stop/></linearGradient></defs>\n    </svg>\n  </div>;\n}';
  assert.throws(()=>check(source),error=>{
    assert.match(error.message,/Unsupported JSX tag <defs>/);
    assert.match(error.message,/CSS gradients/);
    assert.match(error.message,/\[line 4, column 8\]/);
    return true;
  });
  assert.doesNotThrow(()=>check('function Scene(ctx) { return <div style={{position:"absolute",left:100,top:80,width:600,height:2,backgroundImage:"linear-gradient(90deg, transparent, #70eaff, transparent)"}}/>; }'));
});

test('fragments are diagnosed at their own position with a non-animating layout alternative',()=>{
  for(const source of [
    'function Scene(ctx) {\n  return <> <Text id="x">原文</Text> </>;\n}',
    'function Scene(ctx) {\n  return <div>\n    <><Text id="x">原文</Text></>\n  </div>;\n}',
  ]){
    assert.throws(()=>check(source),error=>{
      assert.match(error.message,/Unsupported JSX fragment/);
      assert.match(error.message,/div for HTML layout, g inside SVG, or span inside Text/);
      assert.match(error.message,/use Group only when/);
      assert.match(error.message,source.includes('return <div>')?/\[line 3, column 5\]/:/\[line 2, column 10\]/);
      return true;
    });
    assert.doesNotThrow(()=>check(source.replace('<>','<div>').replace('</>','</div>')));
  }
});

test('nested unsafe attributes report the actual attribute position without relaxing validation',()=>{
  const source='function Scene(ctx) {\n  return <div>\n    <div onClick="run"/>\n  </div>;\n}';
  assert.throws(()=>check(source),error=>{
    assert.match(error.message,/Unsafe JSX attribute: onClick/);
    assert.match(error.message,/\[line 3, column 10\]/);
    return true;
  });
});

test('unsupported methods provide safe supported repairs that preserve the layout',()=>{
  const merged='function Scene(ctx) { const base={fontSize:64,color:"#ffffff"}; return <Text id="title" style={Object.assign({},base,{fontWeight:700})}>标题</Text>; }';
  assert.throws(()=>check(merged),error=>{
    assert.match(error.message,/Unsupported call Object.assign/);
    assert.match(error.message,/object spread/);
    return true;
  });
  assert.doesNotThrow(()=>check(merged.replace('Object.assign({},base,{fontWeight:700})','{...base,fontWeight:700}')));
  const flags='function Scene(ctx) { const sides="tb"; return <div style={{borderTop:sides.indexOf("t")>=0?"2px solid #70eaff":"none"}}><Text id="label">原文</Text></div>; }';
  assert.throws(()=>check(flags),error=>{
    assert.match(error.message,/Unsupported method .indexOf/);
    assert.match(error.message,/explicit booleans/);
    assert.match(error.message,/Do not remove the intended decoration or content/);
    return true;
  });
  assert.doesNotThrow(()=>check(flags.replace('const sides="tb"','const sides={top:true,bottom:true}').replace('sides.indexOf("t")>=0','sides.top')));
});

test('guide repair examples compile and validate as written',()=>{
  assert.doesNotThrow(()=>check("function Scene(ctx) { const base={fontSize:64,color:'#ffffff'}; return <Text id=\"title\" style={{...base,fontWeight:700}}>标题</Text>; }"));
  assert.doesNotThrow(()=>check("function Scene(ctx) { const sides={top:true,bottom:true}; return <div style={{borderTop:sides.top?'2px solid #70eaff':'none',borderBottom:sides.bottom?'2px solid #70eaff':'none'}}><Text id=\"label\">原文</Text></div>; }"));
});
