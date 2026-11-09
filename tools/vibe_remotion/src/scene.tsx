import React, {useEffect} from 'react';
import {AbsoluteFill, Easing, Img, interpolate, spring, useCurrentFrame, useVideoConfig} from 'remotion';
import type {Item, Props, Stage} from './types';
import {activeEvidence,entranceSeconds,exitSeconds} from '../timing.mjs';
import {presentationForStage,visualGeometry,visualSpan,usesEditorialFocus,editorialContext} from '../layout.mjs';
import {resolvePalette} from '../palette.mjs';
import {backgroundLayers,ambientMotion} from '../background.mjs';

type Theme = {background:string;ink:string;muted:string;surface:string;line:string;accent:string;soft:string};
const clamp = (x:number) => Math.max(0, Math.min(1, x));
const ease = Easing.bezier(.22, 1, .36, 1);
const fontFamily = '"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif';

function amount(text:string) {
  return [...text].reduce((sum,c)=>sum+(/[\u0000-\u00ff]/.test(c) ? .55 : 1),0);
}

function Text({text='',size,lines=2,width,color,weight=500,label}: {
  text?:string;size:number;lines?:number;width:number;color:string;weight?:number;label:string;
}) {
  const units=amount(text);
  const fitted=Math.min(size, Math.sqrt(Math.max(1,width)*lines*size/Math.max(1,units))*.95);
  const font=Math.max(size*.74,fitted);
  return <div data-vibe-fit={label} style={{fontSize:font,lineHeight:1.38,fontWeight:weight,
    minHeight:0, maxHeight:font*1.38*lines+2, overflow:'hidden', color, overflowWrap:'anywhere',
    letterSpacing:weight>=700?'-.025em':'0', whiteSpace:'pre-wrap'}}>{text}</div>;
}

function entrance(t:number, index:number, offset:number|undefined, duration:number) {
  const delay=offset??index*.18;
  const f=(t-delay)*30;
  const settled=spring({fps:30,frame:f,config:{damping:25,stiffness:82,mass:1.1},durationInFrames:Math.round(duration*30)});
  return {opacity:interpolate(f,[0,Math.min(13,duration*30)],[0,1],{extrapolateLeft:'clamp',extrapolateRight:'clamp'}),
    transform:`translateY(${(1-settled)*36}px) scale(${.97+.03*settled})`};
}

function Audit({frame}:{frame:number}) {
  useEffect(()=> {
    const failures:string[]=[];
    document.querySelectorAll<HTMLElement>('[data-vibe-fit]').forEach((el)=> {
      if (el.clientWidth>10 && el.clientHeight>1 && (el.scrollHeight>el.clientHeight+3 || el.scrollWidth>el.clientWidth+3)) failures.push((el.dataset.vibeFit??'text')+`[${el.scrollWidth}x${el.scrollHeight}/${el.clientWidth}x${el.clientHeight}]`);
    });
    document.querySelectorAll<HTMLElement>('[data-vibe-card]').forEach((el)=> {
      if(el.clientWidth>10 && el.clientHeight>1 && (el.scrollHeight>el.clientHeight+3||el.scrollWidth>el.clientWidth+3)) failures.push((el.dataset.vibeCard??'card')+`:content[${el.scrollWidth}x${el.scrollHeight}/${el.clientWidth}x${el.clientHeight}]`);
    });
    if(failures.length) throw new Error('OCV_LAYOUT_OVERFLOW:'+failures.join(','));
  },[frame]);
  return null;
}

function Card({item,index,count,t,duration,theme,scale,compact=false,findings=false,asset,width}: {
  item:Item;index:number;count:number;t:number;duration:number;theme:Theme;scale:number;
  compact?:boolean;findings?:boolean;asset?:string;width:number;
}) {
  const padding=36*scale;
  const cw=width-padding*2;
  const emphasis=findings && !!item.value;
  const move=entrance(t,index,item.at,entranceSeconds(duration));
  const highlight=clamp((t-(item.at??index*.18)-.65)/.7);
  return <div data-vibe-card={item.id} style={{position:'relative',display:'flex',flexDirection:'column',
    minWidth:0, flex:1, borderRadius:24*scale, padding, background:theme.surface,
    border:`${1.5*scale}px solid ${theme.line}`,boxShadow:`0 ${12*scale}px ${38*scale}px #082C3D09`,
    overflow:'hidden',...move}}>
    <div style={{display:'flex',alignItems:'center',gap:12*scale,marginBottom:compact?18*scale:24*scale}}>
      <span style={{fontSize:20*scale,fontWeight:700,letterSpacing:'.08em',color:theme.accent}}>{String(index+1).padStart(2,'0')}</span>
      <div style={{height:2*scale,width:40*scale,background:theme.accent,opacity:.42}}/>
    </div>
    {asset && <div style={{flex:1,minHeight:70*scale,maxHeight:compact?140*scale:230*scale,marginBottom:24*scale}}>
      <Img src={asset} style={{width:'100%',height:'100%',objectFit:'contain'}}/>
    </div>}
    <Text text={item.title} size={(count>2?37:43)*scale} lines={2} width={cw} color={theme.ink} weight={700} label={item.id+':title'}/>
    {item.value && <div style={{marginTop:emphasis?30*scale:20*scale,marginBottom:22*scale}}>
      <Text text={item.value+(item.unit?' '+item.unit:'')} size={(count>2?54:72)*scale} lines={2} width={cw} color={theme.accent} weight={800} label={item.id+':value'}/>
      <div style={{height:3*scale,marginTop:17*scale,width:`${highlight*65}%`,background:theme.accent,opacity:.35}}/>
    </div>}
    {item.body && <div style={{marginTop:20*scale}}><Text text={item.body} size={31*scale} lines={compact?3:4} width={cw} color={theme.muted} label={item.id+':body'}/></div>}
    {item.note && <div style={{marginTop:'auto',paddingTop:20*scale}}><Text text={item.note} size={24*scale} lines={2} width={cw} color={theme.muted} label={item.id+':note'}/></div>}
  </div>;
}

function Process({stage,t,theme,scale,width,portrait,assets}: {
  stage:Stage;t:number;theme:Theme;scale:number;width:number;portrait:boolean;assets:Props['assets'];
}) {
  const count=stage.items.length;
  const gap=portrait?24*scale:48*scale;
  const cw=portrait?width:(width-(count-1)*gap)/count;
  return <div style={{display:'flex',height:'100%',gap,flexDirection:portrait?'column':'row',alignItems:'stretch'}}>
    {stage.items.map((item,index)=><React.Fragment key={item.id}>
      <div style={{display:'flex',position:'relative',minWidth:0,flex:1}}>
        <Card item={item} index={index} count={count} t={t} duration={stage.end-stage.start} theme={theme} scale={scale}
          compact={count>2} asset={item.asset_id?assets[item.asset_id]:undefined} width={cw}/>
        {index<count-1 && <svg width={portrait?30*scale:gap} height={portrait?gap:30*scale}
          style={{position:'absolute',left:portrait?'calc(50% - '+15*scale+'px)':'100%',top:portrait?'100%':'calc(50% - '+15*scale+'px)',
            opacity:clamp((t-(stage.items[index+1].at??(index+1)*.18))/.7),overflow:'visible'}}>
          {portrait?<path d={`M ${15*scale} 0 V ${gap-8*scale} m ${-6*scale} ${-6*scale} l ${6*scale} ${6*scale} l ${6*scale} ${-6*scale}`}
            fill="none" stroke={theme.accent} strokeWidth={2*scale}/>:<path d={`M ${8*scale} ${15*scale} H ${gap-8*scale} m ${-7*scale} ${-6*scale} l ${7*scale} ${6*scale} l ${-7*scale} ${6*scale}`}
            fill="none" stroke={theme.accent} strokeWidth={2*scale}/>}
        </svg>}
      </div>
    </React.Fragment>)}
  </div>;
}

function HeroFinding({item,t,theme,scale,width,asset,duration}: {
  item:Item;t:number;theme:Theme;scale:number;width:number;asset?:string;duration:number;
}) {
  return <div data-vibe-card={item.id} style={{height:'100%',display:'flex',alignItems:'center',justifyContent:'center',
    gap:60*scale,minWidth:0,overflow:'hidden',...entrance(t,0,item.at,entranceSeconds(duration))}}>
    {asset&&<div style={{flex:1,minWidth:0,height:'90%'}}><Img src={asset} style={{width:'100%',height:'100%',objectFit:'contain'}}/></div>}
    <div style={{flex:asset?1:undefined,width:asset?undefined:'86%',minWidth:0,textAlign:asset?'left':'center'}}>
      <Text text={item.title} size={(item.value?40:65)*scale} lines={2} width={width*(asset ? .45 : .86)}
        color={theme.ink} weight={700} label={item.id+':title'}/>
      {item.value&&<div style={{marginTop:26*scale}}><Text text={item.value+(item.unit?' '+item.unit:'')} size={118*scale}
        lines={2} width={width*(asset ? .45 : .86)} color={theme.accent} weight={800} label={item.id+':value'}/></div>}
      <div style={{height:3*scale,width:72*scale,background:theme.accent,opacity:.55,margin:asset?'28px 0':'28px auto'}}/>
      {item.body&&<Text text={item.body} size={33*scale} lines={3} width={width*(asset ? .45 : .86)} color={theme.muted} label={item.id+':body'}/>}
      {item.note&&<div style={{marginTop:22*scale}}><Text text={item.note} size={26*scale} lines={2} width={width*(asset ? .45 : .86)} color={theme.muted} label={item.id+':note'}/></div>}
    </div>
  </div>;
}

function PairedFindings({stage,t,theme,scale,width,portrait,compact=false}: {
  stage:Stage;t:number;theme:Theme;scale:number;width:number;portrait:boolean;compact?:boolean;
}) {
  const stacked=portrait||compact;
  const gap=(compact?24:80)*scale;
  const cw=stacked?width*.90:(width-gap)/2;
  const spacing=(compact?16:28)*scale;
  const life=stage.end-stage.start;
  return <div style={{height:'100%',display:'flex',flexDirection:stacked?'column':'row',
    alignItems:'center',justifyContent:'center',gap,position:'relative'}}>
    {!stacked&&<div style={{position:'absolute',left:'50%',top:'24%',bottom:'24%',width:scale,background:theme.line,
      opacity:clamp((t-.25)/.75)}}/>}
    {stage.items.map((item,index)=><div key={item.id} data-vibe-card={item.id}
      style={{width:cw,minWidth:0,maxHeight:stacked?`calc((100% - ${gap}px) / 2)`:'100%',overflow:'hidden',textAlign:'center',
        padding:`${(compact?12:24)*scale}px ${28*scale}px`,boxSizing:'border-box',...entrance(t,index,item.at,entranceSeconds(life)),
        transform:`translateX(${(index===0?-1:1)*(1-ease(clamp((t-(item.at??index*.18))/entranceSeconds(life))))*70*scale}px)`}}>
      <div style={{height:3*scale,width:120*scale,background:theme.accent,margin:`0 auto ${(compact?16:30)*scale}px`,borderRadius:2*scale,
        transform:`scaleX(${ease(clamp((t-(item.at??index*.18))/.9))})`,transformOrigin:index===0?'left':'right'}}/>
      <Text text={item.title} size={(compact?(item.value?31:49):(item.value?38:70))*scale} lines={2} width={cw-56*scale}
        color={item.value?theme.muted:theme.ink} weight={item.value?500:700} label={item.id+':title'}/>
      {item.value&&<div style={{marginTop:spacing}}><Text text={item.value+(item.unit?' '+item.unit:'')} size={(compact?57:80)*scale}
        lines={2} width={cw-56*scale} color={theme.accent} weight={800} label={item.id+':value'}/></div>}
      {item.body&&<div style={{marginTop:spacing,opacity:ease(clamp((t-(item.at??index*.18)-Math.min(.3,life*.05))/.5))}}><Text text={item.body} size={(compact?27:33)*scale} lines={3}
        width={cw-56*scale} color={theme.muted} label={item.id+':body'}/></div>}
      {item.note&&<div style={{marginTop:spacing}}><Text text={item.note} size={(compact?23:27)*scale} lines={2}
        width={cw-56*scale} color={theme.muted} label={item.id+':note'}/></div>}
    </div>)}
  </div>;
}

function MainVisual({stage,span,time,assets,theme,scale,width,height,portrait}: {
  stage:Stage;span:{start:number;end:number};time:number;assets:Props['assets'];theme:Theme;scale:number;width:number;height:number;portrait:boolean;
}) {
  const visual=stage.visual!;
  const rect=visualGeometry(width,height,scale,portrait,visual.placement).visual;
  const life=span.end-span.start;
  const t=time-span.start;
  const entered=ease(clamp(t/entranceSeconds(life)));
  const exited=ease(clamp((life-t)/exitSeconds(life)));
  const clean=visual.treatment==='cutout';
  const captionScale=scale*(portrait?1.35:1);
  const captionHeight=visual.caption?67*captionScale+16*scale:0;
  return <div data-vibe-visual={visual.asset_id} style={{position:'absolute',...rect,opacity:entered*exited,
    transform:`translateY(${(1-entered)*20*scale}px)`,display:'flex',flexDirection:'column',gap:16*scale}}>
    <div style={{height:rect.height-captionHeight,minHeight:0,padding:(clean?0:18)*scale,boxSizing:'border-box',
      background:clean?'transparent':theme.surface,borderRadius:24*scale,border:clean?'none':`${scale}px solid ${theme.line}`}}>
      <Img src={assets[visual.asset_id]} style={{width:'100%',height:'100%',objectFit:'contain',
        transform:`scale(${.975+.02*entered+.005*clamp(t/life)})`,transformOrigin:'center center'}}/>
    </div>
    {visual.caption&&<Text text={visual.caption} size={24*captionScale} lines={2} width={rect.width} color={theme.muted} label={stage.id+':visual-caption'}/>}
  </div>;
}

function Evidence({stage,t,theme,scale,width,portrait,assets}: {
  stage:Stage;t:number;theme:Theme;scale:number;width:number;portrait:boolean;assets:Props['assets'];
}) {
  const current=activeEvidence(stage.items,t);
  const src=current?assets[current.item.asset_id]:undefined;
  const enter=entranceSeconds(stage.end-stage.start);
  return <div style={{display:'flex',gap:42*scale,height:'100%',flexDirection:portrait?'column':'row'}}>
    <div style={{flex:1.45,position:'relative',minHeight:0,minWidth:0,padding:20*scale,borderRadius:24*scale,
      background:theme.surface,border:`${1.5*scale}px solid ${theme.line}`,overflow:'hidden',...entrance(t,0,0,enter)}}>
      {src&&<Img key={current.item.id} src={src} style={{width:'100%',height:'100%',objectFit:'contain',...entrance(t,0,current.at,enter)}}/>}
    </div>
    <div style={{flex:1,display:'flex',flexDirection:'column',gap:22*scale,minWidth:0}}>
      {stage.items.map((item,index)=><div key={item.id} style={{borderLeft:`${3*scale}px solid ${theme.accent}`,
        paddingLeft:24*scale,paddingTop:12*scale,paddingBottom:12*scale,...entrance(t,index,item.at,enter)}}>
        <Text text={item.title} size={36*scale} lines={2} width={width*.37} color={theme.ink} weight={700} label={item.id+':title'}/>
        {item.value&&<div style={{marginTop:10*scale}}><Text text={item.value+(item.unit?' '+item.unit:'')} size={42*scale} lines={2} width={width*.37} color={theme.accent} weight={800} label={item.id+':value'}/></div>}
        {item.body&&<div style={{marginTop:10*scale}}><Text text={item.body} size={28*scale} lines={3} width={width*.37} color={theme.muted} label={item.id+':body'}/></div>}
        {item.note&&<div style={{marginTop:10*scale}}><Text text={item.note} size={23*scale} lines={2} width={width*.37} color={theme.muted} label={item.id+':note'}/></div>}
      </div>)}
    </div>
  </div>;
}

function EditorialFocus({stage,stages,index,t,theme,scale,width,portrait}: {
  stage:Stage;stages:Stage[];index:number;t:number;theme:Theme;scale:number;width:number;portrait:boolean;
}) {
  const item=stage.items[0];
  const labels=editorialContext(stage,index);
  const markerW=(portrait?120:220)*scale;
  const gap=48*scale;
  const textW=width-markerW-gap;
  const duration=entranceSeconds(stage.end-stage.start);
  const move=entrance(t,0,item.at,duration);
  const prior=stages.slice(Math.max(0,index-2),index+1);
  return <div style={{height:'100%',display:'flex',flexDirection:'column',justifyContent:'space-between',minHeight:0}}>
    <div style={{display:'flex',alignItems:'center',gap,flex:1,minHeight:0,...move}}>
      <div style={{width:markerW,flexShrink:0,alignSelf:'stretch',display:'flex',flexDirection:'column',justifyContent:'center',
        borderRight:`${2*scale}px solid ${theme.line}`,paddingRight:24*scale}}>
        {labels.eyebrow&&<Text text={labels.eyebrow} size={25*scale} lines={2} width={markerW-24*scale}
          color={theme.muted} weight={600} label={stage.id+':eyebrow'}/>}
        <div style={{fontSize:(portrait?100:174)*scale,fontWeight:800,lineHeight:1.15,letterSpacing:'-.065em',color:theme.accent}}>{labels.ordinal}</div>
      </div>
      <div data-vibe-card={stage.id+':editorial'} style={{width:textW,minWidth:0,maxHeight:'100%',overflow:'hidden',padding:`${18*scale}px 0`}}>
        {labels.context&&<div style={{marginBottom:22*scale}}><Text text={labels.context} size={29*scale} lines={2}
          width={textW} color={theme.muted} weight={600} label={item.id+':context'}/></div>}
        <Text text={stage.headline} size={(portrait?66:92)*scale} lines={3} width={textW} color={theme.ink} weight={800} label={stage.id+':headline'}/>
        <div style={{height:5*scale,width:130*scale,margin:`${29*scale}px 0`,background:theme.accent,
          transform:`scaleX(${ease(clamp((t-(item.at??0)-.2)/.65))})`,transformOrigin:'left'}}/>
        {item.body&&<Text text={item.body} size={37*scale} lines={4} width={textW} color={theme.muted} label={item.id+':body'}/>}
        {item.note&&<div style={{marginTop:22*scale}}><Text text={item.note} size={27*scale} lines={2} width={textW}
          color={theme.muted} label={item.id+':note'}/></div>}
      </div>
    </div>
    <div style={{display:'flex',gap:28*scale,paddingTop:24*scale,marginTop:24*scale,borderTop:`${scale}px solid ${theme.line}`,
      opacity:ease(clamp((t-(item.at??0)-.3)/.55))}}>
      {prior.map((s,j)=>{
        const n=Math.max(0,index-2)+j;
        const current=n===index;
        return <div key={s.id} style={{flex:1,minWidth:0,display:'flex',alignItems:'flex-start',gap:12*scale}}>
          <span style={{fontSize:23*scale,lineHeight:1.38,fontWeight:700,color:current?theme.accent:theme.muted}}>{String(n+1).padStart(2,'0')}</span>
          <Text text={s.items[0]?.title??s.headline} size={24*scale} lines={2} width={width/prior.length-60*scale}
            color={current?theme.ink:theme.muted} weight={current?700:500} label={stage.id+':progress:'+s.id}/>
        </div>;
      })}
    </div>
  </div>;
}

function StageView({stage,stages,stageIndex,time,theme,scale,width,height,portrait,assets}: {
  stage:Stage;stages:Stage[];stageIndex:number;time:number;theme:Theme;scale:number;width:number;height:number;portrait:boolean;assets:Props['assets'];
}) {
  const t=time-stage.start;
  const life=stage.end-stage.start;
  const fadeIn=ease(clamp(t/Math.min(.4,life*.15)));
  const fadeOut=ease(clamp((life-t)/exitSeconds(life)));
  const opacity=fadeIn*fadeOut;
  const editorial=usesEditorialFocus(stage,stages.length);
  const count=stage.items.length;
  const bodyTop=editorial?0:44*scale;
  const bodyHeight=height-bodyTop-(stage.visual||stage.footer?74*scale:0);
  const textRect=stage.visual?visualGeometry(width,bodyHeight,scale,portrait,stage.visual.placement).text:{left:0,top:0,width,height:bodyHeight};
  const textPortrait=stage.visual?textRect.height>textRect.width*.72:portrait;
  const textScale=stage.visual?scale*(portrait?1.5:.86):scale;
  const rows=textPortrait?(count>2?2:count):(count>2?2:1);
  const cols=textPortrait?(count>2?2:1):Math.min(2,count);
  const gap=26*textScale;
  const cardW=(textRect.width-(cols-1)*gap)/cols;
  const presentation=presentationForStage(stage);
  return <div style={{position:'absolute',inset:0,opacity,transform:`translateX(${(1-fadeIn)*64*scale-(1-fadeOut)*32*scale}px)`}}>
    {!editorial&&stage.eyebrow&&<div style={{position:'absolute',top:-72*scale,color:theme.accent,fontSize:23*scale,fontWeight:700,letterSpacing:'.06em'}}>{stage.eyebrow}</div>}
    {!editorial&&<div style={{position:'absolute',top:-38*scale,left:0,right:0}}>
      <Text text={stage.headline} size={42*scale} lines={1} width={width} color={theme.ink} weight={700} label={stage.id+':headline'}/>
    </div>}
    <div style={{position:'absolute',left:textRect.left,top:bodyTop+textRect.top,width:textRect.width,height:textRect.height,
      transform:`scale(${1+.008*clamp(t/life)})`,transformOrigin:'center center'}}>
      {editorial?<EditorialFocus stage={stage} stages={stages} index={stageIndex} t={t} theme={theme} scale={scale} width={width} portrait={portrait}/>
      :presentation==='evidence'?<Evidence stage={stage} t={t} theme={theme} scale={textScale} width={textRect.width} portrait={textPortrait} assets={assets}/>
      :presentation==='hero'?<HeroFinding item={stage.items[0]} t={t} theme={theme} scale={textScale} width={textRect.width} duration={life}
        asset={stage.items[0].asset_id?assets[stage.items[0].asset_id]:undefined}/>
      :presentation==='paired'?<PairedFindings stage={stage} t={t} theme={theme} scale={textScale} width={textRect.width} portrait={textPortrait} compact={!!stage.visual}/>
      :presentation==='process'?<Process stage={stage} t={t} theme={theme} scale={textScale} width={textRect.width} portrait={textPortrait} assets={assets}/>
      :<div style={{display:'grid',gridTemplateColumns:`repeat(${cols},minmax(0,1fr))`,gridTemplateRows:`repeat(${rows},minmax(0,1fr))`,gap,height:'100%'}}>
        {stage.items.map((item,index)=><Card key={item.id} item={item} index={index} count={count} t={t} duration={life}
          theme={theme} scale={textScale} compact={rows>1} findings={stage.layout==='findings'}
          asset={item.asset_id?assets[item.asset_id]:undefined} width={cardW}/>)}
      </div>}
    </div>
    {stage.footer&&<div style={{position:'absolute',left:0,right:0,bottom:0,paddingTop:16*scale,borderTop:`${1.5*scale}px solid ${theme.line}`,
      opacity:ease(clamp((t-.75)/.7))}}>
      <Text text={stage.footer} size={28*scale} lines={2} width={width} color={theme.muted} label={stage.id+':footer'}/>
    </div>}
  </div>;
}

export const MotionScene: React.FC<Props> = ({plan,assets,duration}) => {
  const frame=useCurrentFrame();
  const {fps,width,height}=useVideoConfig();
  const time=frame/fps;
  const scale=Math.min(width/1920,height/1080);
  const portrait=height>width;
  const theme=resolvePalette(plan);
  const pad=width*.055;
  const innerW=width-2*pad;
  const titleRows=amount(plan.title)*62*scale>innerW*.92?2:1;
  const headerBottom=height*.055+27*scale+titleRows*62*scale*1.38+(plan.subtitle?51*scale:0);
  const contentTop=Math.max(portrait?height*.24:height*.30,headerBottom+90*scale);
  const contentBottom=height*.095;
  const stage=plan.stages.find(s=>time>=s.start && time<s.end)??plan.stages[plan.stages.length-1];
  const stageIndex=plan.stages.indexOf(stage);
  const span=visualSpan(plan.stages,stageIndex);
  const intro=ease(clamp((time+.08)/.65));
  const ambient=ambientMotion(time,plan.background?.kind);
  return <AbsoluteFill style={{width,height,fontFamily,background:theme.background,color:theme.ink,overflow:'hidden'}}>
    {backgroundLayers(plan,scale).map((style,index)=><div key={index} data-vibe-background={plan.background?.kind??'legacy'}
      aria-hidden style={{position:'absolute',inset:ambient.enabled?'-6%':0,pointerEvents:'none',...style,
        transform:ambient.enabled?`translate(${ambient.x}%,${ambient.y}%)`:undefined}}/>)}
    {ambient.enabled&&<svg aria-hidden data-vibe-ambient="orbital" width={width} height={height}
      viewBox="0 0 1920 1080" preserveAspectRatio="none" style={{position:'absolute',inset:0,pointerEvents:'none'}}>
      <g transform={`translate(1880 100) rotate(${ambient.rotation})`} fill="none" stroke={theme.accent}>
        <circle r="340" strokeWidth="1.5" opacity=".12"/>
        <circle r="290" strokeWidth="2" strokeDasharray="140 1682" opacity=".24"/>
        <circle r="385" strokeWidth="1" strokeDasharray="2 18" opacity=".15"/>
        <circle cx="290" cy="0" r="5" fill={theme.accent} stroke="none" opacity=".32"/>
      </g>
      <g transform={`translate(-160 1110) rotate(${-ambient.rotation*.7})`} fill="none" stroke={theme.line}>
        <circle r="410" strokeWidth="1.5" opacity=".3"/>
        <circle r="450" strokeWidth="2" strokeDasharray="240 2587" opacity=".35"/>
      </g>
    </svg>}
    <div style={{position:'absolute',left:pad,top:height*.055,right:pad,opacity:intro,
      transform:`translateY(${(1-intro)*20*scale}px)`}}>
      <div style={{height:5*scale,width:60*scale,background:theme.accent,marginBottom:22*scale,borderRadius:5*scale}}/>
      <Text text={plan.title} size={62*scale} lines={2} width={innerW} color={theme.ink} weight={800} label="title"/>
      {plan.subtitle&&<div style={{marginTop:12*scale}}><Text text={plan.subtitle} size={27*scale} lines={1} width={innerW} color={theme.muted} label="subtitle"/></div>}
    </div>
    <div style={{position:'absolute',left:pad,right:pad,top:contentTop,bottom:contentBottom}}>
      {stage.visual&&span&&<div style={{position:'absolute',left:0,right:0,top:44*scale,bottom:74*scale}}>
        <MainVisual key={span.asset_id+':'+span.start} stage={stage} span={span} time={time} assets={assets} theme={theme} scale={scale}
          width={innerW} height={height-contentTop-contentBottom-118*scale} portrait={portrait}/>
      </div>}
      <StageView key={stage.id} stage={stage} stages={plan.stages} stageIndex={stageIndex} time={time} theme={theme} scale={scale} width={innerW}
        height={height-contentTop-contentBottom} portrait={portrait} assets={assets}/>
    </div>
    <div style={{position:'absolute',bottom:height*.037,left:pad,right:pad,display:'flex',alignItems:'center',gap:12*scale}}>
      {plan.stages.map((s,i)=><div key={s.id} style={{height:3*scale,flex:1,borderRadius:3*scale,background:theme.line,overflow:'hidden'}}>
        <div style={{height:'100%',width:`${i<stageIndex?100:i===stageIndex?clamp((time-s.start)/(s.end-s.start))*100:0}%`,background:theme.accent}}/>
      </div>)}
    </div>
    <Audit frame={frame}/>
  </AbsoluteFill>;
};
