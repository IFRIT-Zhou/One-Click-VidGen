/** A deliberately small, capability-free JSX language for Vibe components.
 *
 * Model source is parsed by the trusted Node caller's TypeScript parser. It is
 * NEVER evaluated, transpiled to executable JavaScript, imported, or passed to
 * Function. The browser only interprets this validated data AST. No ambient
 * JavaScript objects/functions enter its environment.
 */
import {sceneProgress} from './code-animation-progress.mjs';

export const CODE_PROGRAM_VERSION='ocv-code-program-v1';
export const CODE_LIMITS=Object.freeze({source:60000,nodes:8000,depth:64,array:128,
  keys:128,string:12000,operations:100000,outputNodes:2000,callDepth:32});
export const CODE_TAGS=Object.freeze(['div','span','svg','path','polyline','line',
  'rect','circle','ellipse','g','Asset','Text','Group']);
const TAGS=new Set(CODE_TAGS);
const BAD_KEYS=new Set(['__proto__','prototype','constructor','__defineGetter__','__defineSetter__',
  '__lookupGetter__','__lookupSetter__','toString','valueOf','then']);
const HELPERS=new Set(['clamp','lerp','ease','String']);
const MATH=new Set(['min','max','abs','round','floor','ceil','sin','cos','sqrt','pow']);
const RESERVED=new Set(['ctx','Math',...HELPERS]);
const BINARY=new Set(['+','-','*','/','%','**','<','<=','>','>=','===','!==','&&','||','??']);
const UNARY=new Set(['+','-','!']);
const fail=message=>{throw new Error('OCV_CODE_CONTRACT:'+message);};
const own=(value,key)=>Object.prototype.hasOwnProperty.call(value,key);
const plain=value=>value!==null&&typeof value==='object'&&!Array.isArray(value)&&
  (Object.getPrototypeOf(value)===Object.prototype||Object.getPrototypeOf(value)===null);
function key(value){
  if(typeof value!=='string'||!value||value.length>100||BAD_KEYS.has(value))fail('Unsafe property name');
  return value;
}
function name(value){
  key(value);
  if(!/^[A-Za-z_$][A-Za-z0-9_$]*$/.test(value)||RESERVED.has(value))fail('Invalid or reserved local name: '+value);
  return value;
}
function text(value){if(typeof value!=='string'||value.length>CODE_LIMITS.string)fail('String exceeds limit');return value;}
function literal(value){
  if(value===null||typeof value==='boolean')return value;
  if(typeof value==='string')return text(value);
  if(typeof value==='number'&&Number.isFinite(value))return value;
  fail('Only finite JSON literals are allowed');
}
function exact(node,keys){
  if(!plain(node)||Object.keys(node).some(k=>!keys.includes(k)&&!(k==='loc'&&keys.includes('kind'))))fail('Invalid AST fields');
  for(const k of Object.keys(node))key(k);
  if(own(node,'loc')){
    const loc=node.loc;
    if(!plain(loc)||Object.keys(loc).some(k=>!['line','column'].includes(k))||
      ![loc.line,loc.column].every(v=>Number.isInteger(v)&&v>=1&&v<=CODE_LIMITS.source))fail('Invalid source location');
  }
}
function locatedError(error,loc){
  if(error instanceof Error&&error.message.startsWith('OCV_CODE_CONTRACT:')&&loc&&!/\[line \d+, column \d+\]/.test(error.message)){
    error.message+=` [line ${loc.line}, column ${loc.column}]`;
  }
  throw error;
}
function jsxText(value){
  // JSX source line indentation is formatting, while single-line spaces matter.
  const lines=value.replace(/\r/g,'').split('\n');
  const cleaned=lines.map((line,index)=>{
    let result=line.replace(/\t/g,' ');
    if(index>0)result=result.replace(/^ +/,'');
    if(index<lines.length-1)result=result.replace(/ +$/,'');
    return result;
  }).filter(Boolean).join(' ');
  return cleaned.replace(/&(#x[0-9a-f]+|#\d+|amp|lt|gt|quot|apos);/gi,(_,entity)=>{
    const fixed={amp:'&',lt:'<',gt:'>',quot:'"',apos:"'"};
    if(own(fixed,entity))return fixed[entity];
    const code=entity.toLowerCase().startsWith('#x')?parseInt(entity.slice(2),16):parseInt(entity.slice(1),10);
    if(!Number.isInteger(code)||code<0||code>0x10ffff||(code>=0xd800&&code<=0xdfff))fail('Invalid JSX character entity');
    return String.fromCodePoint(code);
  });
}

/** Synchronous compiler. Inject `import ts from 'typescript'` in the Node CLI;
 * do not bundle a parser or any execution engine into the rendering page. */
export function compileProgram(source,ts){
  if(typeof source!=='string'||!source.trim()||source.length>CODE_LIMITS.source)fail('Source missing or exceeds limit');
  if(!ts?.createSourceFile||!ts?.SyntaxKind)fail('Trusted TypeScript parser is required');
  let file;
  try{file=ts.createSourceFile('scene.tsx',source,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);}
  catch{fail('JSX parser could not safely read this source');}
  function location(nodeOrPosition){
    const at=typeof nodeOrPosition==='number'?nodeOrPosition:nodeOrPosition.getStart(file);
    const {line,character}=file.getLineAndCharacterOfPosition(at);
    return {line:line+1,column:character+1};
  }
  function syntaxFail(node,message){try{fail(message);}catch(error){locatedError(error,location(node));}}
  if(file.parseDiagnostics?.length){const diagnostic=file.parseDiagnostics[0];syntaxFail(diagnostic.start??0,
    'JSX syntax error: '+ts.flattenDiagnosticMessageText(diagnostic.messageText,' ')+'; use function Scene(ctx) { const helper = (x) => ...; return <Group>...</Group>; }');}
  const exportStatement=file.statements[1];
  const trailingExport=file.statements.length===2&&ts.isExportAssignment(exportStatement)&&
    !exportStatement.isExportEquals&&ts.isIdentifier(exportStatement.expression)&&exportStatement.expression.text==='Scene';
  if((file.statements.length!==1&&!trailingExport)||!ts.isFunctionDeclaration(file.statements[0]))fail('Provide exactly function Scene(ctx) { ... }');
  const fn=file.statements[0];
  if(fn.name?.text!=='Scene'||!fn.body||fn.asteriskToken||fn.typeParameters?.length||fn.parameters.length!==1||
    !ts.isIdentifier(fn.parameters[0].name)||fn.parameters[0].name.text!=='ctx'||fn.parameters[0].initializer||
    fn.parameters[0].dotDotDotToken)fail('Expected synchronous Scene(ctx) component');
  const modifiers=fn.modifiers??[];
  if(trailingExport&&modifiers.length)fail('Only one default export is allowed');
  if(modifiers.some(m=>![ts.SyntaxKind.ExportKeyword,ts.SyntaxKind.DefaultKeyword].includes(m.kind)))fail('Async/decorated functions are prohibited');
  if(modifiers.length&&!(modifiers.length===2&&modifiers.some(m=>m.kind===ts.SyntaxKind.ExportKeyword)&&
    modifiers.some(m=>m.kind===ts.SyntaxKind.DefaultKeyword)))fail('Only optional export default is allowed');
  let count=0;
  const visit=(node,depth=0)=>{
    if(++count>CODE_LIMITS.nodes||depth>CODE_LIMITS.depth*2)fail('Source syntax complexity limit exceeded');
    ts.forEachChild(node,child=>visit(child,depth+1));
  };visit(fn);
  function property(node){
    if(ts.isIdentifier(node)||ts.isStringLiteral(node)||ts.isNumericLiteral(node))return key(node.text);
    fail('Computed or executable object keys are prohibited');
  }
  function statements(block){
    const out=[];
    for(let i=0;i<block.statements.length;i++){
      const statement=block.statements[i];
      if(ts.isVariableStatement(statement)){
        if(!(statement.declarationList.flags&ts.NodeFlags.Const)||statement.modifiers?.length)syntaxFail(statement,'Only local const bindings are supported; use const value = expression instead of let/var');
        for(const declaration of statement.declarationList.declarations){
          if(!ts.isIdentifier(declaration.name)||!declaration.initializer)syntaxFail(declaration,'const needs a simple name and value; use const value = expression (no destructuring)');
          out.push({kind:'const',name:name(declaration.name.text),value:expression(declaration.initializer),loc:location(declaration)});
        }
      }else if(ts.isFunctionDeclaration(statement)&&statement.name){
        out.push({kind:'const',name:name(statement.name.text),value:localFunction(statement),loc:location(statement)});
      }else if(ts.isReturnStatement(statement)&&statement.expression&&i===block.statements.length-1){
        out.push({kind:'return',value:expression(statement.expression),loc:location(statement)});
      }else syntaxFail(statement,'Only pure local const/helper declarations followed by one final return are allowed; use condition ? yes : no instead of if/loops');
    }
    if(!out.length||out.at(-1).kind!=='return')fail('Component or map block needs a final return');
    return out;
  }
  function localFunction(node,maxParams=16){
    if(node.modifiers?.length||node.asteriskToken||node.typeParameters?.length||!node.body||node.parameters.length>maxParams)
      syntaxFail(node,'Helpers must be synchronous pure functions with at most '+maxParams+' parameters; use const helper = (a, b) => expression');
    if(ts.isFunctionExpression(node)&&node.name)syntaxFail(node,'Use an anonymous function expression or const helper = (a) => expression; named recursive expressions are not supported');
    const params=node.parameters.map(param=>{
      if(!ts.isIdentifier(param.name)||param.initializer||param.dotDotDotToken||param.questionToken)
        syntaxFail(param,'Helper parameters must be simple names; pass default values explicitly (no rest/destructuring/default parameters)');
      return name(param.name.text);
    });
    if(new Set(params).size!==params.length)syntaxFail(node,'Duplicate helper parameter');
    return {kind:'function',params,body:ts.isBlock(node.body)?statements(node.body):[{kind:'return',value:expression(node.body)}],loc:location(node)};
  }
  function jsx(node){
    try{return jsxValue(node);}
    catch(error){locatedError(error,location(node));}
  }
  function jsxValue(node){
    const opening=ts.isJsxElement(node)?node.openingElement:node;
    if(!ts.isIdentifier(opening.tagName)||!TAGS.has(opening.tagName.text)){
      const tag=opening.tagName.getText(file).slice(0,100);
      const hint=tag==='text'?'Visible words must use <Text id="label">...</Text> outside SVG, not SVG <text>.':
        ['defs','linearGradient','radialGradient','stop','filter','mask','clipPath'].includes(tag)?
          'Use supported CSS gradients, clipPath or maskImage styles for decoration; SVG resource definitions are not supported.':
          'Use supported tags: '+CODE_TAGS.join(', ')+'.';
      syntaxFail(opening.tagName,'Unsupported JSX tag <'+tag+'>. '+hint);
    }
    const props=[];
    for(const attr of opening.attributes.properties){
      if(!ts.isJsxAttribute(attr))syntaxFail(attr,'JSX spread attributes are prohibited; pass explicit props such as style={baseStyle}, not {...props}');
      const attrName=key(attr.name.text);
      if(/^on/i.test(attrName)||['ref','dangerouslySetInnerHTML','src','srcSet','href','xlinkHref','srcDoc','formAction'].includes(attrName))syntaxFail(attr,'Unsafe JSX attribute: '+attrName);
      let value={kind:'literal',value:true};
      if(attr.initializer){
        if(ts.isStringLiteral(attr.initializer))value={kind:'literal',value:jsxText(attr.initializer.text)};
        else if(ts.isJsxExpression(attr.initializer)&&attr.initializer.expression)value=expression(attr.initializer.expression);
        else syntaxFail(attr,'Unsupported JSX attribute value');
      }
      props.push({key:attrName,value});
    }
    const children=[];
    for(const child of ts.isJsxElement(node)?node.children:[]){
      if(ts.isJsxText(child)){const value=jsxText(child.text);if(value)children.push({kind:'literal',value});}
      else if(ts.isJsxExpression(child)){if(child.expression)children.push(expression(child.expression));}
      else if(ts.isJsxElement(child)||ts.isJsxSelfClosingElement(child))children.push(jsx(child));
      else if(ts.isJsxFragment(child))syntaxFail(child,'Unsupported JSX fragment <>...</>. Replace the fragment with one supported container: div for HTML layout, g inside SVG, or span inside Text; use Group only when a timed full-canvas layer is intended');
      else syntaxFail(child,'Unsupported JSX child: '+ts.SyntaxKind[child.kind]);
    }
    return {kind:'node',tag:opening.tagName.text,props,children};
  }
  function expression(node){
    try{return {...expressionValue(node),loc:location(node)};}
    catch(error){locatedError(error,location(node));}
  }
  function expressionValue(node){
    if(ts.isParenthesizedExpression(node))return expression(node.expression);
    if(ts.isAsExpression(node)||ts.isTypeAssertionExpression(node)||ts.isNonNullExpression(node))fail('Type assertions/non-null escapes are not part of the scene language');
    if(ts.isNumericLiteral(node))return {kind:'literal',value:literal(Number(node.text))};
    if(ts.isStringLiteral(node)||ts.isNoSubstitutionTemplateLiteral(node))return {kind:'literal',value:text(node.text)};
    if(node.kind===ts.SyntaxKind.TrueKeyword||node.kind===ts.SyntaxKind.FalseKeyword)return {kind:'literal',value:node.kind===ts.SyntaxKind.TrueKeyword};
    if(node.kind===ts.SyntaxKind.NullKeyword)return {kind:'literal',value:null};
    if(ts.isIdentifier(node))return {kind:'read',name:node.text};
    if(ts.isArrowFunction(node)||ts.isFunctionExpression(node))return localFunction(node);
    if(ts.isPrefixUnaryExpression(node)){
      const operator=ts.tokenToString(node.operator);if(!UNARY.has(operator))fail('Unsupported unary operator');
      return {kind:'unary',operator,value:expression(node.operand)};
    }
    if(ts.isBinaryExpression(node)){
      const operator=ts.tokenToString(node.operatorToken.kind);if(!BINARY.has(operator))fail('Assignment, comma and unsupported binary operators are prohibited');
      return {kind:'binary',operator,left:expression(node.left),right:expression(node.right)};
    }
    if(ts.isConditionalExpression(node))return {kind:'conditional',condition:expression(node.condition),yes:expression(node.whenTrue),no:expression(node.whenFalse)};
    if(ts.isArrayLiteralExpression(node)){
      if(node.elements.length>CODE_LIMITS.array)fail('Array exceeds limit');
      return {kind:'array',items:node.elements.map(item=>{if(ts.isSpreadElement(item)||ts.isOmittedExpression(item))fail('Array spread/holes are prohibited');return expression(item);})};
    }
    if(ts.isObjectLiteralExpression(node)){
      if(node.properties.length>CODE_LIMITS.keys)fail('Object exceeds limit');
      return {kind:'object',entries:node.properties.map(item=>{
        if(ts.isSpreadAssignment(item))return {spread:expression(item.expression)};
        if(ts.isPropertyAssignment(item))return {key:property(item.name),value:expression(item.initializer)};
        if(ts.isShorthandPropertyAssignment(item)&&!item.objectAssignmentInitializer)return {key:key(item.name.text),value:{kind:'read',name:item.name.text}};
        fail('Object methods/getters/setters are prohibited');
      })};
    }
    if(ts.isPropertyAccessExpression(node)){
      if(node.questionDotToken)fail('Optional access is not supported');
      if(ts.isIdentifier(node.expression)&&node.expression.text==='Math'&&node.name.text==='PI')return {kind:'literal',value:Math.PI};
      return {kind:'get',object:expression(node.expression),property:{kind:'literal',value:key(node.name.text)}};
    }
    if(ts.isElementAccessExpression(node)&&node.argumentExpression&&!node.questionDotToken)return {kind:'get',object:expression(node.expression),property:expression(node.argumentExpression)};
    if(ts.isCallExpression(node)&&!node.questionDotToken){
      if(ts.isIdentifier(node.expression)&&HELPERS.has(node.expression.text))return {kind:'call',helper:node.expression.text,args:node.arguments.map(expression)};
      if(ts.isIdentifier(node.expression))return {kind:'invoke',name:node.expression.text,args:node.arguments.map(expression)};
      if(ts.isPropertyAccessExpression(node.expression)){
        const callee=node.expression;
        if(ts.isIdentifier(callee.expression)&&callee.expression.text==='Math'&&MATH.has(callee.name.text))return {kind:'call',helper:'Math.'+callee.name.text,args:node.arguments.map(expression)};
        if(ts.isIdentifier(callee.expression)&&callee.expression.text==='ctx'&&callee.name.text==='progress')return {kind:'call',helper:'ctx.progress',args:node.arguments.map(expression)};
        if(callee.name.text==='replace'&&!callee.questionDotToken){
          if(node.arguments.length!==2)syntaxFail(callee,'string.replace(search,replacement) expects exactly 2 string arguments; regexes and replacement callbacks are not supported');
          let search=node.arguments[0];
          while(ts.isParenthesizedExpression(search))search=search.expression;
          if(search.kind===ts.SyntaxKind.RegularExpressionLiteral)
            syntaxFail(search,'string.replace(search,replacement) supports string searches only, not regular expressions. For Text.id use a stable ASCII prefix plus the map callback index, e.g. id={\'label_\'+index}; with multiple panels use id={\'label_\'+panelIndex+\'_\'+index}. Do not build IDs from display wording and do not delete visible text or other content to fix an ID');
          return {kind:'string_replace',value:expression(callee.expression),search:expression(node.arguments[0]),replacement:expression(node.arguments[1])};
        }
        if(callee.name.text==='map'&&node.arguments.length===1&&(ts.isArrowFunction(node.arguments[0])||ts.isFunctionExpression(node.arguments[0]))){
          const callback=localFunction(node.arguments[0],2);
          if(!callback.params.length)fail('map callback needs an item parameter: items.map((item, index) => ...)');
          return {kind:'map',array:expression(callee.expression),params:callback.params,body:callback.body};
        }
        if(ts.isIdentifier(callee.expression)&&callee.expression.text==='Object'&&callee.name.text==='assign')
          syntaxFail(callee,'Unsupported call Object.assign. Merge plain styles with object spread: const style = {...baseStyle, color:"#ffffff"}; then use style={style}. Object spread is supported; JSX prop spread is not');
        if(['indexOf','includes'].includes(callee.name.text))
          syntaxFail(callee,'Unsupported method .'+callee.name.text+'(). For flags or layout options, use explicit booleans/objects such as const sides = {top:true,bottom:false}; then sides.top ? value : other. For array position use the map callback index: items.map((item, index) => ...). Do not remove the intended decoration or content');
        syntaxFail(callee,'Unsupported call '+callee.getText(file).slice(0,120)+'. Only pure local helpers, safe Math helpers, String(primitive), string.replace(string,string), ctx.progress(start,end) and array.map are callable; declare const helper = (a) => expression, then call helper(value)');
      }
      fail('Only pure local helpers, safe Math helpers, String(primitive), string.replace(string,string), ctx.progress(start,end) and array.map are callable; declare const helper = (a) => expression, then call helper(value)');
    }
    if(ts.isTemplateExpression(node))return {kind:'template',head:text(node.head.text),parts:node.templateSpans.map(span=>({value:expression(span.expression),tail:text(span.literal.text)}))};
    if(ts.isJsxElement(node)||ts.isJsxSelfClosingElement(node))return jsx(node);
    if(ts.isJsxFragment(node))syntaxFail(node,'Unsupported JSX fragment <>...</>. Replace the fragment with one supported container: div for HTML layout, g inside SVG, or span inside Text; use Group only when a timed full-canvas layer is intended');
    fail('Unsupported executable syntax: '+ts.SyntaxKind[node.kind]+'; use const values, pure local helpers, ternary expressions and array.map((item) => JSX)');
  }
  return validateProgram({version:CODE_PROGRAM_VERSION,body:statements(fn.body)});
}

/** Validate serialized AST independently of the compiler, including lexical
 * names and exact node fields. A forged instruction document cannot add code. */
export function validateProgram(program){
  // Detach from host objects, getters, custom array methods and mutable caller
  // references before inspecting instructions. Only JSON data crosses this API.
  program=safeData(program,{count:0,seen:new Set(),maxDepth:CODE_LIMITS.depth*3,maxNodes:CODE_LIMITS.nodes*4});
  exact(program,['version','body']);if(program.version!==CODE_PROGRAM_VERSION)fail('Unsupported program version');
  let count=0;
  function bump(depth){if(depth>CODE_LIMITS.depth||++count>CODE_LIMITS.nodes)fail('AST complexity limit exceeded');}
  function list(value,max=CODE_LIMITS.array){if(!Array.isArray(value)||value.length>max)fail('AST list exceeds limit');return value;}
  function statements(body,outer,depth,parameters=[]){
    bump(depth);list(body);if(!body.length||body.at(-1)?.kind!=='return')fail('Expected final return');
    const scope=new Map(outer),locals=new Set(parameters);
    body.forEach((item,index)=>{
      try{
      bump(depth+1);
      if(item?.kind==='const'){
        exact(item,['kind','name','value']);name(item.name);
        if(locals.has(item.name))fail('Duplicate const binding');
        if(item.value?.kind==='function'){
          const fn=item.value;
          if(scope.has(item.name)&&scope.get(item.name)!=='data')fail('Local helper names cannot shadow another helper; use a distinct name to avoid ambiguous recursion');
          bump(depth+1);
          exact(fn,['kind','params','body']);list(fn.params,16);
          if(new Set(fn.params).size!==fn.params.length)fail('Duplicate helper parameter');
          const inner=new Map(scope);
          for(const param of fn.params){name(param);inner.set(param,'data');}
          // Validate before binding the function itself. This rejects direct
          // recursion, mutual/forward recursion and unsafe hoisting by design.
          statements(fn.body,inner,depth+1,fn.params);
          scope.set(item.name,fn.params.length);
        }else{
          expr(item.value,scope,depth+1);scope.set(item.name,'data');
        }
        locals.add(item.name);
      }else if(item?.kind==='return'&&index===body.length-1){exact(item,['kind','value']);expr(item.value,scope,depth+1);}
      else fail('Only const and a final return are valid statements');
      }catch(error){locatedError(error,item?.loc);}
    });
  }
  function expr(node,scope,depth){
    try{return validateExpression(node,scope,depth);}
    catch(error){locatedError(error,node?.loc);}
  }
  function validateExpression(node,scope,depth){
    bump(depth);if(!plain(node)||typeof node.kind!=='string')fail('Invalid expression');
    switch(node.kind){
      case 'literal':exact(node,['kind','value']);literal(node.value);break;
      case 'read':exact(node,['kind','name']);key(node.name);if(!scope.has(node.name))fail('Unknown local identifier: '+node.name);
        if(scope.get(node.name)!=='data')fail('Local helper '+node.name+' cannot escape as data/props; call '+node.name+'(...) instead');break;
      case 'function':fail('Pure helpers must be declared directly: const helper = (a) => expression; functions cannot be passed as data, returned or put into props');break;
      case 'invoke':{
        exact(node,['kind','name','args']);name(node.name);
        const arity=scope.get(node.name);
        if(typeof arity!=='number')fail('Unknown or recursive helper '+node.name+'; declare each pure const helper before use, do not recurse or call a data value');
        list(node.args,16);
        if(node.args.length!==arity)fail('Helper '+node.name+' expects '+arity+' explicit arguments; default/rest parameters are not supported');
        for(const arg of node.args)expr(arg,scope,depth+1);break;
      }
      case 'unary':exact(node,['kind','operator','value']);if(!UNARY.has(node.operator))fail('Unknown unary operator');expr(node.value,scope,depth+1);break;
      case 'binary':exact(node,['kind','operator','left','right']);if(!BINARY.has(node.operator))fail('Unknown binary operator');expr(node.left,scope,depth+1);expr(node.right,scope,depth+1);break;
      case 'conditional':exact(node,['kind','condition','yes','no']);for(const k of ['condition','yes','no'])expr(node[k],scope,depth+1);break;
      case 'array':exact(node,['kind','items']);for(const item of list(node.items))expr(item,scope,depth+1);break;
      case 'object':exact(node,['kind','entries']);for(const item of list(node.entries,CODE_LIMITS.keys)){
        if(plain(item)&&own(item,'spread')){exact(item,['spread']);expr(item.spread,scope,depth+1);}
        else{exact(item,['key','value']);key(item.key);expr(item.value,scope,depth+1);}
      }break;
      case 'get':exact(node,['kind','object','property']);expr(node.object,scope,depth+1);expr(node.property,scope,depth+1);if(node.property.kind==='literal'&&typeof node.property.value==='string')key(node.property.value);break;
      case 'call':exact(node,['kind','helper','args']);if(typeof node.helper!=='string'||!HELPERS.has(node.helper)&&node.helper!=='ctx.progress'&&!(node.helper.startsWith('Math.')&&MATH.has(node.helper.slice(5))))fail('Unknown helper');
        list(node.args,16);if(node.helper==='ctx.progress'&&node.args.length!==2)fail('ctx.progress(start,end) expects 2 arguments in seconds');
        if(node.helper==='String'&&node.args.length!==1)fail('String(value) expects exactly 1 primitive argument');
        for(const arg of node.args)expr(arg,scope,depth+1);break;
      case 'string_replace':
        exact(node,['kind','value','search','replacement']);
        for(const field of ['value','search','replacement'])expr(node[field],scope,depth+1);
        break;
      case 'map':{
        exact(node,['kind','array','params','body']);expr(node.array,scope,depth+1);list(node.params,2);
        if(node.params.length<1||new Set(node.params).size!==node.params.length)fail('Invalid map parameters');
        const inner=new Map(scope);for(const param of node.params){name(param);inner.set(param,'data');}statements(node.body,inner,depth+1,node.params);break;
      }
      case 'template':exact(node,['kind','head','parts']);text(node.head);for(const part of list(node.parts)){exact(part,['value','tail']);text(part.tail);expr(part.value,scope,depth+1);}break;
      case 'node':exact(node,['kind','tag','props','children']);if(!TAGS.has(node.tag))fail('Unsupported node tag');
        for(const prop of list(node.props,CODE_LIMITS.keys)){exact(prop,['key','value']);safeProp(prop.key);expr(prop.value,scope,depth+1);}
        for(const child of list(node.children))expr(child,scope,depth+1);break;
      default:fail('Unknown AST instruction');
    }
  }
  statements(program.body,new Map([['ctx','data']]),0);
  return program;
}

function safeProp(value){
  key(value);
  if(/^on/i.test(value)||['ref','dangerouslySetInnerHTML','src','srcSet','href','xlinkHref','srcDoc','formAction','innerHTML','outerHTML','children'].includes(value))fail('Unsafe node property: '+value);
  return value;
}
function numeric(value){if(typeof value!=='number'||!Number.isFinite(value))fail('Expected finite numeric value');return value;}
function primitiveString(value){if(value===null)return '';if(['string','number','boolean'].includes(typeof value))return text(String(value));fail('Cannot convert object to text');}
function boundedStringReplace(value,search,replacement){
  const at=value.indexOf(search);
  if(at<0)return value;
  // Preflight native GetSubstitution expansion without allocating its output.
  // Even a short replacement full of $`/$' can duplicate a long input many
  // times. No regex captures exist here, so $1/$<name> remain literal text.
  let length=value.length-search.length;
  for(let i=0;i<replacement.length;i++){
    const token=replacement[i]==='$'?replacement[i+1]:undefined;
    if(token==='$'){length++;i++;}
    else if(token==='&'){length+=search.length;i++;}
    else if(token==='`'){length+=at;i++;}
    else if(token==="'"){length+=value.length-at-search.length;i++;}
    else length++;
    if(length>CODE_LIMITS.string)fail('String exceeds limit before replacement expansion');
  }
  return text(value.replace(search,replacement));
}
function safeData(value,state,depth=0){
  if(++state.count>(state.maxNodes??CODE_LIMITS.nodes)||depth>(state.maxDepth??20))fail('Input data complexity limit exceeded');
  if(value===null||typeof value!=='object')return literal(value);
  if(state.seen.has(value))fail('Cyclic input data');state.seen.add(value);
  const descriptors=Object.getOwnPropertyDescriptors(value);
  if(Reflect.ownKeys(descriptors).some(k=>typeof k!=='string'))fail('Symbol properties are prohibited');
  for(const descriptor of Object.values(descriptors))if(!own(descriptor,'value'))fail('Input getters/setters are prohibited');
  let result;
  if(Array.isArray(value)){
    if(value.length>CODE_LIMITS.array)fail('Input array exceeds limit');
    if(Object.keys(descriptors).some(k=>k!=='length'&&(!/^(0|[1-9]\d*)$/.test(k)||Number(k)>=value.length)))fail('Custom array properties are prohibited');
    result=[];
    for(let index=0;index<value.length;index++){
      if(!own(descriptors,String(index)))fail('Sparse input arrays are prohibited');
      result.push(safeData(descriptors[index].value,state,depth+1));
    }
  }else{
    if(!plain(value)||Object.keys(descriptors).length>CODE_LIMITS.keys)fail('Input object must be a plain dictionary');
    result=Object.create(null);
    for(const [k,descriptor] of Object.entries(descriptors)){
      key(k);
      result[k]=safeData(descriptor.value,state,depth+1);
    }
  }
  state.seen.delete(value);return result;
}
function propertyValue(object,index){
  if(typeof index==='number'){if(!Number.isSafeInteger(index)||index<0)fail('Invalid array index');index=String(index);}
  key(index);
  if(Array.isArray(object)){
    if(index==='length')return object.length;
    if(!/^(0|[1-9]\d*)$/.test(index)||!own(object,index))fail('Array access outside bounds');
    return object[index];
  }
  if(typeof object==='string'){
    if(index==='length')return object.length;
    if(!/^(0|[1-9]\d*)$/.test(index)||Number(index)>=object.length)fail('String access outside bounds');
    return object[Number(index)];
  }
  if(!plain(object)||!own(object,index))fail('Missing own data property: '+index);
  return object[index];
}
function checkNodeProps(props){
  for(const [name,value] of Object.entries(props)){
    safeProp(name);
    if(typeof value==='string'&&(/(?:https?:|file:|javascript:|data:|vbscript:|@import|url\s*\()/i.test(value)))fail('External/embedded resource URLs are prohibited');
    if(name==='style'){
      if(!plain(value))fail('style must be a data object');
      for(const [css,setting] of Object.entries(value)){
        key(css);
        if(/^(?:behavior|MozBinding)$/i.test(css)||!['string','number'].includes(typeof setting)||
          typeof setting==='string'&&/(?:\\|\/\*|url\s*\(|(?:image-set|image|src|paint|element|cross-fade)\s*\(|expression\s*\(|@import|https?:|file:|javascript:|data:)/i.test(setting))fail('Unsafe CSS value');
      }
    }
  }
}

/** Execute only our data instructions, with bounded pure arithmetic and JSX.
 * ctx is copied to prototype-free data; it cannot contain a capability/function.
 */
export function evaluateProgram(program,context){
  program=validateProgram(program);
  const ctx=safeData(context,{count:0,seen:new Set()});
  const rendered=new WeakSet();
  const closures=new WeakMap(),activeCalls=new WeakSet();
  let operations=0,outputNodes=0,callDepth=0;
  const step=(cost=1)=>{operations+=cost;if(operations>CODE_LIMITS.operations)fail('Operation budget exceeded');};
  function run(body,outer){
    const env=Object.assign(Object.create(null),outer);
    for(const item of body){step();const value=expr(item.value,env);if(item.kind==='return')return value;env[item.name]=value;}
    fail('Missing component return');
  }
  function expr(node,env){
    try{return evaluateExpression(node,env);}
    catch(error){locatedError(error,node?.loc);}
  }
  function evaluateExpression(node,env){
    step();
    switch(node.kind){
      case 'literal':return node.value;
      case 'read':{
        if(!own(env,node.name))fail('Unknown binding');
        const value=env[node.name];
        if(value&&typeof value==='object'&&closures.has(value))fail('Local helpers cannot escape into data, return values or props; call the helper instead');
        return value;
      }
      case 'function':{
        const token=Object.create(null);
        closures.set(token,{params:node.params,body:node.body,environment:Object.assign(Object.create(null),env)});
        return token;
      }
      case 'invoke':{
        const token=env[node.name],closure=token&&typeof token==='object'?closures.get(token):null;
        if(!closure)fail('Only local interpreted helper functions can be invoked');
        if(activeCalls.has(token)||callDepth>=CODE_LIMITS.callDepth)fail('Helper recursion/call-depth limit exceeded; use bounded array.map or direct arithmetic');
        if(node.args.length!==closure.params.length)fail('Wrong local helper argument count');
        const args=node.args.map(arg=>expr(arg,env));
        const inner=Object.assign(Object.create(null),closure.environment);
        for(let i=0;i<closure.params.length;i++)inner[closure.params[i]]=args[i];
        activeCalls.add(token);callDepth++;
        try{return run(closure.body,inner);}
        finally{callDepth--;activeCalls.delete(token);}
      }
      case 'unary':{const value=expr(node.value,env);return node.operator==='!'?!value:node.operator==='-'?numeric(-numeric(value)):numeric(value);}
      case 'binary':{
        const a=expr(node.left,env);
        if(node.operator==='&&')return a?expr(node.right,env):a;
        if(node.operator==='||')return a?a:expr(node.right,env);
        if(node.operator==='??')return a===null?expr(node.right,env):a;
        const b=expr(node.right,env);
        if(node.operator==='==='||node.operator==='!==')return node.operator==='==='?a===b:a!==b;
        if(node.operator==='+'&&(typeof a==='string'||typeof b==='string'))return text(primitiveString(a)+primitiveString(b));
        if(['<','<=','>','>='].includes(node.operator)){
          if(!((typeof a==='number'&&typeof b==='number')||(typeof a==='string'&&typeof b==='string')))fail('Comparison requires matching primitive types');
          return node.operator==='<'?a<b:node.operator==='<='?a<=b:node.operator==='>'?a>b:a>=b;
        }
        numeric(a);numeric(b);
        return numeric(node.operator==='+'?a+b:node.operator==='-'?a-b:node.operator==='*'?a*b:node.operator==='/'?a/b:node.operator==='%'?a%b:a**b);
      }
      case 'conditional':return expr(node.condition,env)?expr(node.yes,env):expr(node.no,env);
      case 'array':return node.items.map(item=>expr(item,env));
      case 'object':{
        const out=Object.create(null);
        for(const entry of node.entries){
          if(own(entry,'spread')){const value=expr(entry.spread,env);if(!plain(value))fail('Object spread requires data dictionary');for(const k of Object.keys(value)){key(k);out[k]=value[k];}}
          else out[entry.key]=expr(entry.value,env);
          if(Object.keys(out).length>CODE_LIMITS.keys)fail('Object key limit exceeded');
        }return out;
      }
      case 'get':return propertyValue(expr(node.object,env),expr(node.property,env));
      case 'call':{
        if(node.helper==='String'){
          const value=expr(node.args[0],env);
          if(value!==null&&!['string','number','boolean'].includes(typeof value))
            fail('String(value) accepts only null, boolean, finite number or string; objects, arrays and helpers cannot be converted');
          if(typeof value==='number')numeric(value);
          if(typeof value==='string')step(Math.ceil(value.length/32));
          return text(String(value));
        }
        const args=node.args.map(arg=>numeric(expr(arg,env)));
        if(node.helper==='ctx.progress')return sceneProgress(ctx,args[0],args[1]);
        if(node.helper==='clamp'){
          if(![1,3].includes(args.length))fail('clamp(value[,min,max]) expects 1 or 3 arguments');
          const [value,lo=0,hi=1]=args;if(lo>hi)fail('clamp bounds reversed');return Math.min(hi,Math.max(lo,value));
        }
        if(node.helper==='lerp'){if(args.length!==3)fail('lerp expects 3 arguments');return numeric(args[0]+(args[1]-args[0])*args[2]);}
        if(node.helper==='ease'){if(args.length!==1)fail('ease expects 1 argument');const value=Math.max(0,Math.min(1,args[0]));return value*value*(3-2*value);}
        const method=node.helper.slice(5);
        if((['min','max'].includes(method)&&!args.length)||(method==='pow'&&args.length!==2)||(!['min','max','pow'].includes(method)&&args.length!==1))fail('Wrong math argument count');
        return numeric(Math[method](...args));
      }
      case 'string_replace':{
        const value=expr(node.value,env),search=expr(node.search,env),replacement=expr(node.replacement,env);
        if([value,search,replacement].some(value=>typeof value!=='string'))
          fail('string.replace(search,replacement) requires 3 primitive strings; regexes, objects and replacement callbacks are prohibited');
        text(value);text(search);text(replacement);
        // All operands are bounded primitives, never property-dispatched model
        // objects. Native replace only scans one literal-string occurrence;
        // regexp execution and user callbacks cannot enter this instruction.
        // Charge work proportional to input length rather than one AST step.
        step(1+Math.ceil((value.length+search.length+replacement.length)/32));
        return boundedStringReplace(value,search,replacement);
      }
      case 'map':{
        const array=expr(node.array,env);if(!Array.isArray(array)||array.length>CODE_LIMITS.array)fail('map requires a bounded array');
        return array.map((item,index)=>{step();const inner=Object.assign(Object.create(null),env);inner[node.params[0]]=item;if(node.params.length>1)inner[node.params[1]]=index;return run(node.body,inner);});
      }
      case 'template':{let value=node.head;for(const part of node.parts)value=text(value+primitiveString(expr(part.value,env))+part.tail);return value;}
      case 'node':{
        if(++outputNodes>CODE_LIMITS.outputNodes)fail('Rendered node budget exceeded');
        const props=Object.create(null);
        for(const prop of node.props){step();props[prop.key]=expr(prop.value,env);}checkNodeProps(props);
        const children=[];
        function append(value,depth=0){
          step();if(depth>CODE_LIMITS.depth)fail('Child nesting limit exceeded');
          if(value===null||typeof value==='boolean')return;
          if(Array.isArray(value)){for(const item of value)append(item,depth+1);return;}
          if(typeof value==='string'||typeof value==='number'){children.push(value);return;}
          if(value&&typeof value==='object'&&rendered.has(value)){children.push(value);return;}
          fail('JSX children must be text or rendered nodes');
        }
        for(const child of node.children)append(expr(child,env));
        const renderedNode={tag:node.tag,props,children};rendered.add(renderedNode);return renderedNode;
      }
      default:fail('Unknown runtime instruction');
    }
  }
  const result=run(program.body,Object.assign(Object.create(null),{ctx}));
  if(!result||typeof result!=='object'||!rendered.has(result))fail('Scene must return one JSX node');
  // Count occurrences, not just distinct allocated nodes: repeated references
  // must not let a compact program expand into an exponentially large DOM.
  let occurrences=0;
  function checkTree(node,depth=0){
    step();if(depth>CODE_LIMITS.depth||++occurrences>CODE_LIMITS.outputNodes)fail('Rendered tree budget exceeded');
    for(const child of node.children){
      if(typeof child==='object')checkTree(child,depth+1);
      else if(++occurrences>CODE_LIMITS.outputNodes)fail('Rendered tree budget exceeded');
    }
  }
  checkTree(result);
  return result;
}
