/* OCV data-only adaptations of nine pinned video-shotcraft recipes.
 * Preserve each recipe's motion grammar, replace demo copy/colors with approved
 * narration and frozen source palette. Apache-2.0 attribution in lib/video-shotcraft.
 * Cards/typography are shared with the original adapters, including layout audit.
 */
import React from 'react';
import {AbsoluteFill,Img,spring,useCurrentFrame} from 'remotion';
import {alpha,bezier,EASE,mix,ramp} from './lib/ShotcraftMotion';
import type {CardProps,TextProps} from './ShotcraftScene';
import type {Palette,ShotcraftPlan,ShotcraftStage} from './lib/shotcraft-types';

type Props={stage:ShotcraftStage;plan:ShotcraftPlan;assets:Record<string,string>;frames:number;
  Text:React.ComponentType<TextProps>;Card:React.ComponentType<CardProps>;
  Edges:React.ComponentType<{stage:ShotcraftStage;p:Palette}>};
const abs:React.CSSProperties={position:'absolute'};
const bounce=(f:number,delay=0,damping=18)=>f<delay?0:spring({frame:f-delay,fps:30,config:{damping,stiffness:120,mass:1}});
const exit=(f:number,frames:number)=>1-ramp(f,frames-9,9,EASE.exit);
const step=(f:number,n:number,frames:number)=>Array.from({length:n-1},(_,i)=>ramp(f,(i+1)*frames/n,20,EASE.swift)).reduce((a,b)=>a+b,0);
function Heading({stage,plan,Text}:Props){return <Text text={stage.headline} x={112} y={155} w={1696} h={115} size={64} min={40} color={plan.palette.ink} weight={650} label={stage.id+':headline'}/>;}
function Caption({stage,plan,Text}:Props){return <Text text={stage.body} x={112} y={835} w={1696} h={110} size={38} color={plan.palette.muted} label={stage.id+':body'}/>;}
function Frame({children,...props}:Props&{children:React.ReactNode}){
  const f=useCurrentFrame();
  return <AbsoluteFill style={{opacity:exit(f,props.frames)}}>{children}<props.Edges stage={props.stage} p={props.plan.palette}/></AbsoluteFill>;
}

function Flip(props:Props){
  const {stage,plan,assets,Card,Text}=props,f=useCurrentFrame(),p=plan.palette,n=stage.items.length,w=Math.min(740,(1632-(n-1)*38)/n),left=(1920-n*w-(n-1)*38)/2;
  return <Frame {...props}><Heading {...props}/><div style={{...abs,inset:0,perspective:1800}}>
    {stage.items.map((item,i)=>{
      const t=f-48-i*6;
      const angle=t<0?0:t<4?-10*ramp(t,0,4):t<24?mix(-10,194,ramp(t,4,20,EASE.smooth)):194-14*bounce(t,24,15);
      const face={...abs,inset:0,backfaceVisibility:'hidden' as const,borderRadius:12,overflow:'hidden'};
      const back={...item,title:'',body:'',motif:'none' as const,asset_id:undefined};
      return <div key={item.id} style={{...abs,left:left+i*(w+38),top:300,width:w,height:490,transformStyle:'preserve-3d',transform:`translateY(${-50*Math.sin(angle*Math.PI/180)}px) rotateY(${angle}deg)`,opacity:ramp(f,i*5,12)}}>
        <div style={face}><Card item={{...item,body:''}} p={p} asset={item.asset_id?assets[item.asset_id]:undefined} w={w} h={490}/></div>
        <div style={{...face,transform:'rotateY(180deg)',background:p.surface,border:`2px solid ${p.accent}`,boxSizing:'border-box'}}><Text text={item.body} x={36} y={105} w={w-72} h={300} size={54} min={32} color={p.ink} label={back.id+':back'} weight={600}/></div>
        <div style={{...abs,inset:0,pointerEvents:'none',background:`linear-gradient(100deg,transparent,${alpha(p.accent,.3*Math.abs(Math.sin(angle*Math.PI/180)))},transparent)`}}/>
      </div>;
    })}</div><Caption {...props}/></Frame>;
}

function Carousel(props:Props){
  const {stage,plan,assets,Card,frames}=props,f=useCurrentFrame(),p=plan.palette,n=stage.items.length,current=step(f,n,frames),radius=n===2?590:680;
  return <Frame {...props}><Heading {...props}/><div style={{...abs,left:0,top:280,width:1920,height:520,perspective:2200,perspectiveOrigin:'50% 50%'}}>
    <div style={{...abs,left:960,top:260,transformStyle:'preserve-3d',transform:`translateZ(${-radius}px) rotateX(-5deg) rotateY(${-current*360/n}deg)`}}>
      {stage.items.map((item,i)=>{
        const d=Math.abs(i-current),front=Math.max(0,1-d),face:React.CSSProperties={...abs,inset:0,backfaceVisibility:'hidden'};
        const card=<Card item={item} p={p} asset={item.asset_id?assets[item.asset_id]:undefined} w={900} h={490}/>;
        return <div key={item.id} style={{...abs,left:-450,top:-245,width:900,height:490,transformStyle:'preserve-3d',transform:`rotateY(${i*360/n}deg) translateZ(${radius+30*front}px)`,boxShadow:`0 20px 50px ${alpha(p.ink,.13)}`}}>
          <div style={face}>{card}</div><div style={{...face,transform:'rotateY(180deg)'}}>{card}</div>
        </div>;
      })}</div></div><Caption {...props}/></Frame>;
}

function Stack(props:Props){
  const {stage,plan,assets,Card}=props,f=useCurrentFrame(),p=plan.palette,fan=bounce(f,48,22),lift=bounce(f,82,20);
  return <Frame {...props}><Heading {...props}/><div style={{...abs,inset:0,transform:`scale(${mix(1.03,.91,fan)})`,transformOrigin:'960px 540px'}}>
    {stage.items.map((item,i)=>{
      const k=i-1,entry=bounce(f,5+i*8,15);
      return <div key={item.id} style={{...abs,left:710+k*570*fan,top:330+(1-entry)*850-Math.abs(k)*12-(i===1?44*lift:0),width:500,height:450,zIndex:i===1&&lift>.2?5:i,transformOrigin:'50% 130%',transform:`rotate(${k*6.5*fan}deg)`,boxShadow:`0 24px 45px ${alpha(p.ink,.16)}`,opacity:ramp(f,i*8,8)}}>
        <Card item={item} p={p} asset={item.asset_id?assets[item.asset_id]:undefined} w={500} h={450}/>
      </div>;
    })}</div><Caption {...props}/></Frame>;
}

function Timeline(props:Props){
  const {stage,plan,assets,Card,frames}=props,f=useCurrentFrame(),p=plan.palette,n=stage.items.length,current=step(f,n,frames),zoom=1+.045*ramp(f,(n-1)*frames/n+24,14);
  return <Frame {...props}><Heading {...props}/><div style={{...abs,left:90,top:290,width:1740,height:520,overflow:'hidden'}}>
    <div style={{...abs,inset:0,transform:`scale(${zoom})`,transformOrigin:'870px 260px'}}><div style={{...abs,left:870-current*1100,top:0}}>
      <div style={{...abs,left:-1000,top:445,width:n*1100+2000,height:2,background:p.line}}/>
      {Array.from({length:n*11+20},(_,i)=><div key={i} style={{...abs,left:-1000+i*100,top:440,width:2,height:i%11===0?22:10,background:p.line}}/>)}
      {stage.items.map((item,i)=>{const q=bounce(f,i*frames/n-4,20);return <div key={item.id} style={{...abs,left:i*1100-430,top:25+50*(1-q),opacity:ramp(f,i*frames/n-4,12)}}>
        <Card item={item} p={p} asset={item.asset_id?assets[item.asset_id]:undefined} w={860} h={365}/><div style={{...abs,left:423,top:415,width:14,height:14,borderRadius:'50%',background:p.accent}}/>
      </div>;})}</div></div></div><Caption {...props}/></Frame>;
}

function Document(props:Props){
  const {stage,plan,assets,Text}=props,f=useCurrentFrame(),p=plan.palette,n=stage.items.length,pull=ramp(f,0,70,EASE.smooth),rowH=510/n;
  return <Frame {...props}><div style={{...abs,left:310,top:185,width:1300,height:720,background:p.surface,border:`2px solid ${p.line}`,borderRadius:16,boxShadow:`0 32px 85px ${alpha(p.ink,.14)}`,transformOrigin:'50% 40%',transform:`perspective(2000px) scale(${mix(1.18,1,pull)}) rotateY(${mix(-15,0,pull)}deg) rotateX(${mix(5,0,pull)}deg)`}}>
    <Text text={stage.headline} x={56} y={38} w={1180} h={110} size={62} min={42} color={p.ink} weight={650} label={stage.id+':headline'}/>
    {stage.items.map((item,i)=>{const cue=18+i*21,q=ramp(f,cue,18,EASE.linear),active=f>=cue&&f<cue+21;return <div key={item.id} style={{...abs,left:56,top:165+i*rowH,width:1180,height:rowH-16,clipPath:`inset(0 ${(1-q)*100}% 0 0)`}}>
      {item.asset_id&&<Img src={assets[item.asset_id]} style={{...abs,left:0,top:0,width:240,height:rowH-20,objectFit:'contain'}}/>}
      <Text text={item.title} x={item.asset_id?270:0} w={item.asset_id?900:1180} h={64} size={44} min={32} color={p.ink} weight={600} label={item.id+':title'}/>
      <Text text={item.body} x={item.asset_id?270:0} y={67} w={item.asset_id?900:1180} h={rowH-87} size={32} min={28} color={p.muted} label={item.id+':body'}/>
      {active&&<div style={{...abs,left:Math.max(0,1180*q-5),top:0,width:3,height:rowH-25,background:p.accent}}/>}
    </div>;})}</div><Text text={stage.body} x={112} y={915} w={1696} h={38} size={28} min={24} color={p.muted} label={stage.id+':body'}/></Frame>;
}

function Marker(props:Props){
  const {stage,plan,Text}=props,f=useCurrentFrame(),p=plan.palette,land=bounce(f,4,21),stroke=ramp(f,30,14,EASE.out),item=stage.items[0];
  return <Frame {...props}><Text text={stage.headline} x={160} y={240} w={1600} h={250} size={115} min={62} color={p.ink} weight={700} label={stage.id+':headline'} style={{transform:`translateY(${60*(1-land)}px)`,opacity:ramp(f,4,10)}}/>
    <svg width="1920" height="1080" style={abs} aria-hidden><defs><clipPath id={stage.id+'-brush'}><rect x="160" y="470" width={1600*stroke} height="58"/></clipPath></defs><path d="M162 500L1750 480L1765 501L169 522Z" fill={p.accent} opacity=".65" clipPath={`url(#${stage.id}-brush)`}/><path d="M180 518L1725 499" stroke={p.accent} strokeWidth="3" opacity=".55" clipPath={`url(#${stage.id}-brush)`}/></svg>
    <Text text={item.title} x={165} y={580} w={1590} h={110} size={62} color={p.ink} label={item.id+':title'} style={{opacity:ramp(f,45,15),transform:`translateX(${35*(1-ramp(f,45,15))}px)`}}/>
    <Text text={item.body} x={165} y={710} w={1590} h={115} size={43} color={p.muted} label={item.id+':body'} style={{opacity:ramp(f,54,15)}}/><Caption {...props}/></Frame>;
}

function Quad(props:Props){
  const {stage,plan,assets,Card,Text}=props,f=useCurrentFrame(),p=plan.palette;
  return <Frame {...props}><Heading {...props}/>{stage.items.map((item,i)=>{
    const q=ramp(f,8+i*7,25,EASE.out),x=112+(i%2)*866,y=290+Math.floor(i/2)*262;
    return <div key={item.id} style={{...abs,left:x,top:y,width:830,height:238,opacity:q,clipPath:i===1?`inset(0 ${(1-q)*100}% 0 0)`:undefined,transform:i===0?`translateX(${-90*(1-q)}px)`:i===2?`scale(${.75+.25*q})`:i===3?`translateY(${75*(1-q)}px)`:undefined}}>
      {item.asset_id?<div style={{width:830,height:238,background:p.surface,border:`2px solid ${p.line}`,borderRadius:12,boxSizing:'border-box'}}>
        <Img src={assets[item.asset_id]} style={{...abs,left:20,top:20,width:300,height:198,objectFit:'contain'}}/>
        <Text text={item.title} x={345} y={28} w={460} h={95} size={42} min={32} color={p.ink} weight={650} label={item.id+':title'}/>
        <Text text={item.body} x={345} y={132} w={460} h={85} size={32} min={28} color={p.muted} label={item.id+':body'}/>
      </div>:<Card item={item} p={p} w={830} h={238} compact/>}
    </div>;
  })}<Caption {...props}/></Frame>;
}

function Scan(props:Props){
  const {stage,plan,assets,Text}=props,f=useCurrentFrame(),p=plan.palette,item=stage.items[0],scan=ramp(f,8,55,EASE.linear),bracket=bounce(f,64,21),note=ramp(f,73,18,EASE.out);
  return <Frame {...props}><Heading {...props}/><div style={{...abs,left:112,top:295,width:1000,height:500}}>
    <Img src={assets[item.asset_id!]} style={{width:'100%',height:'100%',objectFit:'contain'}}/>
    {f>=8&&f<64&&<div style={{...abs,left:0,right:0,top:scan*500,height:3,background:p.accent,boxShadow:`0 0 18px ${alpha(p.accent,.6)}`}}/>}
    <svg width="1000" height="500" style={{...abs,inset:0,overflow:'visible',opacity:ramp(f,64,12)}} aria-hidden>{[[0,0],[1000,0],[0,500],[1000,500]].map(([x,y],i)=>{
      const sx=x===0?1:-1,sy=y===0?1:-1;return <path key={i} d={`M${x+sx*45} ${y}H${x}V${y+sy*45}`} transform={`translate(${-sx*65*(1-bracket)} ${-sy*65*(1-bracket)})`} fill="none" stroke={p.accent} strokeWidth="4"/>;
    })}</svg></div><div style={{...abs,left:1180,top:335,width:625,height:445,opacity:note,transform:`translateX(${40*(1-note)}px)`}}><Text text={item.title} w={625} h={130} size={65} min={40} color={p.ink} weight={650} label={item.id+':title'}/><Text text={item.body} y={155} w={625} h={280} size={43} min={32} color={p.muted} label={item.id+':body'}/></div><Caption {...props}/></Frame>;
}

function Demote(props:Props){
  const {stage,plan,assets,Text,Card}=props,f=useCurrentFrame(),p=plan.palette,q=ramp(f,40,26,bezier(.6,0,.18,1)),scale=mix(1,.38,q),n=stage.items.length,w=(1696-(n-1)*28)/n;
  return <Frame {...props}><div style={{...abs,left:mix(160,112,q),top:mix(390,170,q),width:1600,height:210,transformOrigin:'top left',transform:`scale(${scale})`}}><Text text={stage.headline} w={1600} h={210} size={145} min={100} color={p.ink} weight={700} label={stage.id+':headline'} style={{opacity:ramp(f,0,16)}}/></div>
    {stage.items.map((item,i)=>{const a=ramp(f,52+i*7,28,EASE.out);return <div key={item.id} style={{...abs,left:112+i*(w+28),top:315+35*(1-a),clipPath:`inset(${(1-a)*100}% 0 0 0)`,opacity:a}}><Card item={item} p={p} asset={item.asset_id?assets[item.asset_id]:undefined} w={w} h={480}/></div>;})}<Caption {...props}/></Frame>;
}

const components={
  'card-flip-reveal':Flip,'carousel-3d':Carousel,'card-stack':Stack,'timeline-travel':Timeline,
  'document-typewriter-reveal':Document,'marker-underline-title':Marker,'quad-split-parallel-scenes':Quad,
  'scanline-annotate-focus':Scan,'title-demote-to-label':Demote,
};
export function ExpandedStage(props:Props){
  const Component=components[props.stage.recipe as keyof typeof components];
  if(!Component)throw new Error('No Shotcraft adapter: '+props.stage.recipe);
  return <Component {...props}/>;
}
