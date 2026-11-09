import React, {useLayoutEffect, useRef} from 'react';
import {AbsoluteFill, Img, useCurrentFrame, useVideoConfig, delayRender, continueRender, cancelRender} from 'remotion';
import {evaluateProgram} from '../lib/code-program.mjs';
import {CODE_LOG_PREFIX, FONT_SANS, FONT_SERIF, assetPlacement, safeStyle, validateTree, validateFrameManifest, backgroundCoverage, textOverflowEvidence} from '../lib/code-render-contract.mjs';
import {findLayeredTextOverlaps, measureTextNodeInk} from '../lib/layered-text-overlap.mjs';
import {textPaintVisible} from '../lib/code-text-visibility.mjs';
import {geometryVisibility, sourceAnchorFeedback} from '../lib/code-geometry-visibility.mjs';

type VirtualNode = {tag:string; props:Record<string,any>; children:any[]};
type Geometry = Record<string,{box?:number[];prepared_size:number[];anchor_available:boolean;anchor_unavailable_reason?:string}>;
export type CodeProps = {
  program?:unknown; canvas:[number,number];duration:number;fps:number;assets:Record<string,string>;
  geometry:Geometry;measurements:Record<string,any>;mode:'measure'|'preview'|'video';poster?:boolean;
  reference_manifest?:{texts:Record<string,string>;assets:string[]};collect_measurements?:boolean;
  measure_requests:Array<{id:string;text:string;font_size:number;font_weight?:string|number;font_family?:string;max_width?:number;line_height?:number}>;
};
const clamp = (x:number) => Math.max(0, Math.min(1, x));
const smooth = (x:number) => 1 - Math.pow(1 - clamp(x), 3);
const rounded = (x:number) => Math.round(x * 100) / 100;
const normalizedFont = (name?:string) => name === 'serif' || name === FONT_SERIF ? FONT_SERIF : FONT_SANS;

function Asset({props, input}:{props:Record<string,any>;input:CodeProps}) {
  const {box, crop} = assetPlacement(props, input.geometry);
  const style = safeStyle(props.style) as React.CSSProperties;
  const [l,t,r,b] = crop;
  const bounds = box ? {position:'absolute' as const,left:box[0],top:box[1],width:box[2],height:box[3]} : {};
  // In source-anchor mode the crop's target region is derived from the original
  // canvas geometry. Cropping therefore does not enlarge its contents.
  const source = input.geometry[props.id];
  let inner:React.CSSProperties = {position:'absolute',width:`${100/(r-l)}%`,height:`${100/(b-t)}%`,maxWidth:'none',left:`${-l*100/(r-l)}%`,top:`${-t*100/(b-t)}%`};
  if (box && source && props.anchor !== 'source') {
    const cw = source.prepared_size[0]*(r-l), ch = source.prepared_size[1]*(b-t);
    const scale = props.fit === 'cover' ? Math.max(box[2]/cw,box[3]/ch) : Math.min(box[2]/cw,box[3]/ch);
    const iw = source.prepared_size[0]*scale, ih = source.prepared_size[1]*scale;
    inner = {position:'absolute',maxWidth:'none',width:iw,height:ih,left:(box[2]-cw*scale)/2-l*iw,top:(box[3]-ch*scale)/2-t*ih};
  } else if (!box && l === 0 && t === 0 && r === 1 && b === 1) {
    inner = {display:'block',width:'100%',height:'100%',objectFit:props.fit??'contain'};
  }
  return <div data-code-node={`asset:${props.id}`} data-code-asset={props.id} data-code-crop={JSON.stringify(crop)}
    data-code-anchor={props.anchor??'manual'} data-code-source-box={props.anchor==='source'?JSON.stringify(box):undefined}
    style={{...bounds,...style,overflow:'hidden'}}>
    <Img src={input.assets[props.id]} style={inner}/>
  </div>;
}

function renderNode(node:any, input:CodeProps, frame:number, key:string):React.ReactNode {
  if (node === null || node === undefined || typeof node === 'boolean') return null;
  if (typeof node === 'string' || typeof node === 'number') return node;
  if (Array.isArray(node)) return node.map((child,i)=>renderNode(child,input,frame,`${key}_${i}`));
  const {tag, props:p={}, children=[]} = node as VirtualNode;
  const kids = children.map((child,i)=>renderNode(child,input,frame,`${key}_${i}`));
  const style = safeStyle(p.style) as React.CSSProperties;
  if (tag === 'Asset') return <Asset key={key} props={p} input={input}/>;
  if (tag === 'Text') return <div key={key} data-code-node={p.id} data-code-text={p.id} style={{margin:0,lineHeight:1.2,whiteSpace:'pre-wrap',...style}}>{kids}</div>;
  if (tag === 'Group') {
    const time = frame/input.fps, at = p.at??0, enter=p.enter??.5, exit=p.exit??.3;
    const incoming = input.poster ? 1 : enter === 0 ? Number(time>=at) : smooth((time-at)/enter);
    const outgoing = input.poster || exit===0 ? 1 : clamp((input.duration-time)/exit);
    const opacity = incoming*outgoing;
    const distance = 36*(1-incoming), motion = p.motion??'fade';
    const transform = motion==='slide_left'?`translateX(${-distance}px)`:motion==='slide_right'?`translateX(${distance}px)`:
      motion==='slide_up'?`translateY(${distance}px)`:motion==='slide_down'?`translateY(${-distance}px)`:motion==='scale'?`scale(${.94+.06*incoming})`:'';
    return <div key={key} data-code-node={p.id??key} data-code-group={p.id??key} data-code-timing={JSON.stringify({at,enter,exit,motion})}
      style={{position:'absolute',left:0,top:0,width:'100%',height:'100%',...style,
        opacity:(Number(style.opacity??1))*(motion==='none'?(input.poster?1:Number(time>=at)):opacity),
        transform:[style.transform,transform].filter(Boolean).join(' ')||undefined}}>{kids}</div>;
  }
  const clean = {...p,style}; delete clean.key;
  return React.createElement(tag,{...clean,key,'data-code-node':p.id??key},kids);
}

function measuredGeometry(element:Element, root:HTMLElement) {
  const canvas=root.getBoundingClientRect();
  const sx=(root.clientWidth||canvas.width)/canvas.width,sy=(root.clientHeight||canvas.height)/canvas.height;
  const local=(r:{left:number;top:number;width:number;height:number})=>[(r.left-canvas.left)*sx,(r.top-canvas.top)*sy,r.width*sx,r.height*sy];
  const clips:any[]=[];
  // The canvas is always clipped; ancestors may clip one or both axes. Use the
  // padding/client box, excluding borders and scrollbars, as CSS overflow does.
  for(let ancestor=element.parentElement;ancestor&&ancestor!==root;ancestor=ancestor.parentElement){
    const style=getComputedStyle(ancestor),r=ancestor.getBoundingClientRect();
    const clipX=['hidden','clip','auto','scroll'].includes(style.overflowX);
    const clipY=['hidden','clip','auto','scroll'].includes(style.overflowY);
    if(!clipX&&!clipY)continue;
    const scaleX=ancestor.offsetWidth?r.width/ancestor.offsetWidth:1;
    const scaleY=ancestor.offsetHeight?r.height/ancestor.offsetHeight:1;
    clips.push({target:ancestor.dataset.codeNode??ancestor.tagName.toLowerCase(),
      box:local({left:r.left+ancestor.clientLeft*scaleX,top:r.top+ancestor.clientTop*scaleY,
        width:ancestor.clientWidth*scaleX,height:ancestor.clientHeight*scaleY}),
      clip_x:clipX,clip_y:clipY,overflow_x:style.overflowX,overflow_y:style.overflowY});
  }
  return geometryVisibility(local(element.getBoundingClientRect()),[root.clientWidth||canvas.width,root.clientHeight||canvas.height],clips);
}

function appearance(element:Element, root:HTMLElement) {
  let opacity=1, shown=true;
  const paintStyles:CSSStyleDeclaration[]=[];
  for(let e:Element|null=element;e;e=e.parentElement){
    const style=getComputedStyle(e);
    paintStyles.push(style);
    if(style.display==='none'||style.visibility==='hidden'||style.visibility==='collapse')shown=false;
    opacity*=Number.parseFloat(style.opacity)||0;
    if(e===root)break;
  }
  const style=getComputedStyle(element), rect=element.getBoundingClientRect();
  if(!textPaintVisible(style,paintStyles.slice(1)))shown=false;
  const geometry=measuredGeometry(element,root);
  return {visible:shown&&opacity>=.03&&rect.width>0&&rect.height>0&&geometry.visible,opacity:rounded(opacity),
    painted:shown&&opacity>=.03,geometry_visibility:geometry,
    visibility:style.visibility,display:style.display,color:style.color,transform:style.transform,
    clip_path:style.clipPath,mask_image:style.maskImage};
}

function measureScene(root:HTMLElement,input:CodeProps,frame:number) {
  const rootRect=root.getBoundingClientRect(), sx=input.canvas[0]/rootRect.width, sy=input.canvas[1]/rootRect.height;
  const rect=(r:DOMRect)=>({left:rounded((r.left-rootRect.left)*sx),top:rounded((r.top-rootRect.top)*sy),right:rounded((r.right-rootRect.left)*sx),bottom:rounded((r.bottom-rootRect.top)*sy)});
  const box=(element:Element)=>{const r=rect(element.getBoundingClientRect());return [r.left,r.top,rounded(r.right-r.left),rounded(r.bottom-r.top)];};
  const texts:any[]=[],assets:any[]=[],groups:any[]=[],diagnostics:any[]=[];
  root.querySelectorAll('[data-code-text]').forEach(element=>{
    const node=element as HTMLElement, style=getComputedStyle(node), rects:any[]=[],inkRects:any[]=[],fragments:any[]=[];
    let inkComplete=true;
    const parentAppearance=appearance(node,root);
    const walker=document.createTreeWalker(node,NodeFilter.SHOW_TEXT);
    let textNode:Node|null;
    while((textNode=walker.nextNode())){
      if(!textNode.textContent?.trim())continue;
      const parent=textNode.parentElement!;
      const leafStyle=getComputedStyle(parent),font=parseFloat(leafStyle.fontSize), actual=appearance(parent,root);
      const range=document.createRange(); range.selectNodeContents(textNode);
      const fragmentRects=Array.from(range.getClientRects()).filter(item=>item.width>0&&item.height>0).map(rect);
      const measuredInk=measureTextNodeInk(textNode);
      const fragmentInk=measuredInk?.map(rect)??null;
      if(fragmentInk)inkRects.push(...fragmentInk);else inkComplete=false;
      rects.push(...fragmentRects);
      fragments.push({text:textNode.textContent,font_size:font,rects:fragmentRects,...actual});
      if(parentAppearance.visible && (!actual.visible || actual.opacity < parentAppearance.opacity*.8 || font < Math.max(10,parseFloat(style.fontSize)*.2) || !fragmentRects.length)){
        diagnostics.push({kind:'hidden_text_fragment',target:node.dataset.codeText,explanation:'A nonempty text fragment is hidden, transparent, too small, or has no measured glyph range'});
      }
      if(parentAppearance.visible){
        for(let ancestor:Element|null=parent;ancestor&&ancestor!==root;ancestor=ancestor.parentElement){
          const effect=getComputedStyle(ancestor);
          if(effect.maskImage!=='none'||effect.clipPath!=='none'||/opacity\s*\(/.test(effect.filter)){
            diagnostics.push({kind:'text_visibility_effect',target:node.dataset.codeText,explanation:'Text has a clipping/mask/opacity filter that requires explicit visibility review'});break;
          }
        }
      }
    }
    const entry={id:node.dataset.codeText,text:node.textContent??'',box:box(node),rects,ink_rects:inkComplete&&inkRects.length?inkRects:null,fragments,font_size:parseFloat(style.fontSize),font_family:style.fontFamily,
      font_weight:style.fontWeight,line_height:style.lineHeight,...parentAppearance};
    texts.push(entry);
    if(input.poster&&!entry.visible)diagnostics.push({kind:'poster_text_hidden',target:entry.id,
      explanation:'Text is not visible in the fully revealed poster. Poster uses frame=0 with ctx.poster=true, unlike video frame=0. For custom entrance opacity/width/transform use ctx.progress(start,end), or explicitly branch on ctx.poster; Group does not override custom child styles. Also check clipping and geometry.'});
    if(entry.visible){
      const bounded=(value:string)=>!!value&&!['auto','none','initial','unset'].includes(value);
      const overflow=textOverflowEvidence({font_size:entry.font_size,scroll_width:node.scrollWidth,scroll_height:node.scrollHeight,client_width:node.clientWidth,client_height:node.clientHeight,
        overflow_x:style.overflowX,overflow_y:style.overflowY,
        explicit_width:bounded(node.style.width)||bounded(node.style.maxWidth)||(bounded(node.style.left)&&bounded(node.style.right)),
        explicit_height:bounded(node.style.height)||bounded(node.style.maxHeight)||(bounded(node.style.top)&&bounded(node.style.bottom))});
      if(overflow)diagnostics.push({kind:'text_overflow',target:entry.id,
        explanation:'Text content exceeds an explicitly bounded or clipping text box',evidence:overflow});
      if(rects.some(r=>r.left < -1||r.top < -1||r.right>input.canvas[0]+1||r.bottom>input.canvas[1]+1))diagnostics.push({kind:'text_outside_canvas',target:entry.id,explanation:'Visible text extends outside the canvas'});
      for(let ancestor:HTMLElement|null=node;ancestor&&ancestor!==root;ancestor=ancestor.parentElement){
        const clipping=getComputedStyle(ancestor),b=box(ancestor);
        const clipX=['hidden','clip','auto','scroll'].includes(clipping.overflowX),clipY=['hidden','clip','auto','scroll'].includes(clipping.overflowY);
        if(rects.some(r=>(clipX&&(r.left<b[0]-1||r.right>b[0]+b[2]+1))||(clipY&&(r.top<b[1]-1||r.bottom>b[1]+b[3]+1)))){
          diagnostics.push({kind:'text_clipped',target:entry.id,explanation:'Measured text extends outside its own or an ancestor clipping box',
            evidence:{clip_target:ancestor.dataset.codeNode??ancestor.tagName.toLowerCase(),box:b,overflow_x:clipping.overflowX,overflow_y:clipping.overflowY}});break;
        }
      }
    }
  });
  root.querySelectorAll('[data-code-asset]').forEach(element=>{
    const node=element as HTMLElement;
    const ancestors:any[]=[];
    for(let parent=node.parentElement;parent&&parent!==root;parent=parent.parentElement){
      const style=getComputedStyle(parent),b=box(parent);
      if(style.transform!=='none'||Math.abs(b[0])>1||Math.abs(b[1])>1){
        if(ancestors.length<6)ancestors.push({target:parent.dataset.codeNode??parent.tagName.toLowerCase(),box:b,position:style.position,transform:style.transform});
      }
    }
    const sourceAnchor=node.dataset.codeAnchor==='source'?sourceAnchorFeedback(JSON.parse(node.dataset.codeSourceBox??'null'),box(node),ancestors):null;
    const entry={id:node.dataset.codeAsset,box:box(node),crop:JSON.parse(node.dataset.codeCrop??'[0,0,1,1]'),
      anchor:node.dataset.codeAnchor,source_anchor:sourceAnchor,...appearance(node,root)};
    assets.push(entry);
    const geometry=entry.geometry_visibility;
    if(entry.painted&&geometry.state!=='inside'){
      const complete=!geometry.visible;
      diagnostics.push({kind:complete&&input.poster?'asset_not_in_view':complete?'asset_transient_clip':'asset_partial_clip',
        severity:complete&&input.poster?'error':'warning',target:entry.id,
        explanation:complete?'Asset has no visible intersection with the canvas/ancestor overflow clip.':
          'Asset is partially clipped, not fully invisible. Check that the intentional crop retains required subject details.',
        evidence:{box:entry.box,canvas:input.canvas,geometry_visibility:geometry,source_anchor:sourceAnchor}});
    }
    if(input.poster&&sourceAnchor?.displaced)diagnostics.push({kind:'source_anchor_offset',severity:'warning',target:entry.id,
      explanation:sourceAnchor.hint,evidence:sourceAnchor});
  });
  root.querySelectorAll('[data-code-group]').forEach(element=>{
    const node=element as HTMLElement;
    groups.push({id:node.dataset.codeGroup,box:box(node),...JSON.parse(node.dataset.codeTiming??'{}'),...appearance(node,root)});
  });
  root.querySelectorAll('[data-code-node]').forEach(element=>{
    const node=element as HTMLElement,style=getComputedStyle(node);
    const evidence=backgroundCoverage({tag:node.tagName.toLowerCase(),box:box(node),background_color:style.backgroundColor,...appearance(node,root)},input.canvas);
    if(evidence)diagnostics.push({kind:'background_covered',target:node.dataset.codeNode,
      explanation:'A nearly full-canvas opaque surface covers the prepared background. Keep the supplied background visible; use local cards/backplates only.',
      evidence:{...evidence,element:node.tagName.toLowerCase(),background_image:style.backgroundImage}});
  });
  for(const overlap of findLayeredTextOverlaps(texts))diagnostics.push({kind:'text_overlap',target:overlap.first,other:overlap.second,
    explanation:`Measured text ink (or conservative ranges when ink is unavailable) overlaps by ${overlap.width}x${overlap.height} pixels`});
  return {frame,time:frame/input.fps,poster:!!input.poster,texts,assets,groups,diagnostics};
}

export const CodeScene:React.FC<CodeProps> = input => {
  const frame=useCurrentFrame(); const {width,height}=useVideoConfig(); const root=useRef<HTMLDivElement>(null);
  const tree=input.mode==='measure'?null:evaluateProgram(input.program,{
    frame,fps:input.fps,width,height,duration:input.duration,poster:!!input.poster,measurements:input.measurements,geometry:input.geometry,
  });
  if(tree!==null){
    validateTree(tree,input.assets,input.geometry,input.duration);
    if(input.reference_manifest)validateFrameManifest(tree,input.reference_manifest);
  }
  useLayoutEffect(()=>{
    if(input.collect_measurements===false)return;
    const handle=delayRender('Measure authored scene in the browser', {timeoutInMilliseconds:15000});
    let cancelled=false;
    Promise.all([document.fonts.ready,...Array.from(root.current?.querySelectorAll('img')??[]).map(img=>img.decode().catch(()=>undefined))]).then(()=>{
      if(cancelled||!root.current)return;
      if(input.mode==='measure'){
        const measured=input.measure_requests.map(request=>{
          const node=root.current!.querySelector(`[data-code-measure="${request.id}"]`) as HTMLElement;
          const r=node.getBoundingClientRect();
          return {...request,width:rounded(r.width),height:rounded(r.height),font_family:getComputedStyle(node).fontFamily,font_weight:getComputedStyle(node).fontWeight};
        });
        console.log(CODE_LOG_PREFIX+JSON.stringify({measured,texts:[],assets:[],diagnostics:[]}));
      }else console.log(CODE_LOG_PREFIX+JSON.stringify(measureScene(root.current,input,frame)));
      continueRender(handle);
    }).catch(error=>{cancelRender(error);});
    return ()=>{cancelled=true;continueRender(handle);};
  },[frame,input]);
  return <AbsoluteFill ref={root} style={{fontFamily:FONT_SANS,fontSize:32,lineHeight:1.2,color:'#ffffff',overflow:'hidden',boxSizing:'border-box'}}>
    {input.mode==='measure'?input.measure_requests.map((request,i)=><div key={request.id} data-code-measure={request.id} style={{position:'absolute',left:0,top:i*300,
      display:'inline-block',width:request.max_width??'max-content',maxWidth:request.max_width,whiteSpace:request.max_width?'pre-wrap':'pre',
      fontSize:request.font_size,fontWeight:request.font_weight??600,fontFamily:normalizedFont(request.font_family),lineHeight:request.line_height??1.2}}>{request.text}</div>):<>
      <Img src={input.assets.background} style={{position:'absolute',left:0,top:0,width:'100%',height:'100%',objectFit:'cover'}}/>
      {renderNode(tree,input,frame,'root')}
    </>}
  </AbsoluteFill>;
};
