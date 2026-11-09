import React,{useCallback,useEffect,useLayoutEffect,useRef} from 'react';
import {AbsoluteFill,Img,useCurrentFrame,useVideoConfig} from 'remotion';
import {layerMotion,visibleImageFadeStops} from '../lib/layered-contract.mjs';
import type {Layer,LayeredProps} from './lib/layered-types';
import {fitLayeredText,isCompactNumericLabel} from '../lib/layered-text-fit.mjs';
import {collectLayeredTextRects,findLayeredTextOverlaps,isStaticLayeredPlan} from '../lib/layered-text-overlap.mjs';

const FONTS={sans:'"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif',serif:'"Noto Serif CJK SC", "SimSun", serif'};
function Audit({frame,checkTextOverlap}:{frame:number;checkTextOverlap:boolean}){
  useEffect(()=>{
    const failures:string[]=[];
    document.querySelectorAll<HTMLElement>('[data-layered-text]').forEach(el=>{
      if(el.scrollWidth>el.clientWidth+2||el.scrollHeight>el.clientHeight+2)failures.push(`${el.dataset.layeredText}[${el.scrollWidth}x${el.scrollHeight}/${el.clientWidth}x${el.clientHeight}]`);
    });
    if(failures.length)throw new Error('OCV_LAYOUT_OVERFLOW:'+failures.join(','));
    // Animated groups can cross transiently. Audit collisions only in the
    // full, static reconstruction that is reviewed before motion scheduling.
    if(checkTextOverlap){
      const collisions=findLayeredTextOverlaps(collectLayeredTextRects(document));
      if(collisions.length)throw new Error('OCV_LAYOUT_OVERLAP:'+collisions.map((item:{first:string;second:string;width:number;height:number})=>`${item.first}<->${item.second}[${item.width}x${item.height}]`).join(','));
    }
  },[frame,checkTextOverlap]);
  return null;
}

function LayerText({layer}:{layer:Layer}){
  const ink=useRef<HTMLDivElement>(null),halo=useRef<HTMLDivElement>(null);
  const [,,width,height]=layer.box;
  useLayoutEffect(()=>{
    const el=ink.current!;
    el.style.height='auto';
    const measure=(size:number)=>{
      el.style.fontSize=`${size}px`;
      return el.scrollWidth<=width+1&&el.scrollHeight<=height+1;
    };
    const chosen=fitLayeredText(layer.font_size!,layer.min_font_size,measure);
    // If no readable size fits, retain the requested size so Audit still fails.
    const font=chosen??layer.font_size!;
    el.style.fontSize=`${font}px`;
    // CJK glyph ink can be a few pixels taller than its CSS line box. Include
    // that measured ink in the centered block instead of falsely auditing the
    // natural line-height as the entire available label region.
    el.style.height=`${Math.min(height,el.scrollHeight)}px`;
    if(halo.current)halo.current.style.fontSize=`${font}px`;
    el.dataset.layeredFontSize=String(font);
    el.dataset.layeredRequestedFontSize=String(layer.font_size);
  },[layer,width,height]);
  const compact=layer.min_font_size!==undefined&&isCompactNumericLabel(layer.text);
  const type:React.CSSProperties={width:'100%',boxSizing:'border-box',whiteSpace:compact?'pre':'pre-wrap',overflowWrap:compact?'normal':'anywhere',wordBreak:'normal',fontFamily:FONTS[layer.font_family??'sans'],fontSize:layer.font_size,fontWeight:layer.bold?700:400,lineHeight:layer.line_height,letterSpacing:layer.letter_spacing,textAlign:layer.align,color:layer.fill};
  const gradient=layer.gradient?{backgroundImage:`linear-gradient(${layer.gradient.angle}deg,${layer.fill},${layer.gradient.color})`,backgroundClip:'text',WebkitBackgroundClip:'text',color:'transparent'}:undefined;
  return <div style={{width:'100%',height:'100%',display:'flex',flexDirection:'column',justifyContent:layer.valign==='middle'?'center':layer.valign==='bottom'?'flex-end':'flex-start'}}>
    <div style={{position:'relative',flexShrink:0,isolation:'isolate',width:'100%'}}>
      {Boolean(layer.readability_halo?.opacity)&&<div ref={halo} aria-hidden style={{...type,position:'absolute',inset:0,zIndex:-1,pointerEvents:'none',color:'transparent',opacity:layer.readability_halo!.opacity,filter:'blur(.25em)'}}>
        <span style={{color:'transparent',backgroundColor:layer.readability_halo!.color,boxShadow:`0 0 0 .12em ${layer.readability_halo!.color}`,WebkitBoxDecorationBreak:'clone',boxDecorationBreak:'clone'}}>{layer.text}</span>
      </div>}
      <div ref={ink} data-layered-text={layer.id} data-layered-box-height={height} style={{...type,...gradient,maxHeight:height}}>{layer.text}</div>
    </div>
  </div>;
}

// Remotion calls onImageFrame after decoding natural dimensions, before it
// releases the frame render. Multiplying the image alpha leaves both the source
// file and all other layers untouched, including the device in front of a coat.
function FeatheredImage({asset,width,height,fit,fraction}:{asset:string;width:number;height:number;fit:'contain'|'cover';fraction:number}){
  const applyMask=useCallback((img:HTMLImageElement)=>{
    const {start,end}=visibleImageFadeStops(width,height,img.naturalWidth,img.naturalHeight,fit,fraction);
    img.style.maskImage=`linear-gradient(to right, black ${start}px, transparent ${end}px)`;
  },[width,height,fit,fraction]);
  return <Img src={asset} onImageFrame={applyMask} style={{display:'block',width:'100%',height:'100%',objectFit:fit}}/>;
}

function Content({layer,asset,reveal}:{layer:Layer;asset?:string;reveal:number}){
  const [, ,width,height]=layer.box;
  if(layer.type==='image'&&layer.edge_fade_right)return <FeatheredImage asset={asset!} width={width} height={height} fit={layer.fit??'contain'} fraction={layer.edge_fade_right}/>;
  if(layer.type==='image')return <Img src={asset!} style={{display:'block',width:'100%',height:'100%',objectFit:layer.fit}}/>;
  if(layer.type==='text')return <LayerText layer={layer}/>;
  if(layer.type==='rect'||layer.type==='ellipse')return <div style={{width:'100%',height:'100%',boxSizing:'border-box',background:layer.gradient?`linear-gradient(${layer.gradient.angle}deg,${layer.fill},${layer.gradient.color})`:layer.fill,border:layer.stroke?`${layer.stroke_width}px solid ${layer.stroke}`:undefined,borderRadius:layer.type==='ellipse'?'50%':layer.radius,boxShadow:layer.glow?`inset 0 0 ${layer.glow}px ${layer.stroke??layer.fill}, 0 0 ${layer.glow}px ${layer.stroke??layer.fill}`:undefined}}/>;
  const points=layer.points!;
  const length=points.slice(1).reduce((sum,p,i)=>sum+Math.hypot(p[0]-points[i][0],p[1]-points[i][1]),0);
  return <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} style={{display:'block',overflow:'visible'}}><polyline points={points.map(p=>p.join(',')).join(' ')} fill="none" stroke={layer.stroke} strokeWidth={layer.stroke_width} strokeLinecap="round" strokeLinejoin="round" strokeDasharray={layer.motion==='draw'?length:undefined} strokeDashoffset={layer.motion==='draw'?length*(1-reveal):undefined}/></svg>;
}

export function LayeredScene({plan,assets}:LayeredProps){
  const frame=useCurrentFrame(),{fps}=useVideoConfig(),time=frame/fps;
  return <AbsoluteFill style={{background:plan.background,overflow:'hidden'}}>
    {plan.elements.map(layer=>{
      const [x,y,width,height]=layer.box,move=layerMotion(layer,time,plan.canvas);
      return <div key={layer.id} data-layered-element={layer.id} data-layered-asset={layer.asset_id} style={{position:'absolute',left:x,top:y,width,height,opacity:move.opacity,transform:`translate(${move.x}px,${move.y}px) rotate(${layer.rotation}deg) scale(${move.scale})`,transformOrigin:'center',filter:layer.shadow==='soft'?'drop-shadow(0 10px 15px rgba(16,26,32,.22))':undefined}}>
        <div style={{width:'100%',height:'100%',clipPath:layer.motion==='grow'?`inset(${(1-move.reveal)*100}% 0 0 0)`:undefined}}><Content layer={layer} asset={layer.asset_id?assets[layer.asset_id]:undefined} reveal={move.reveal}/></div>
      </div>;
    })}
    <Audit frame={frame} checkTextOverlap={isStaticLayeredPlan(plan)}/>
  </AbsoluteFill>;
}
