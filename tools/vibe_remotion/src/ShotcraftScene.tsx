/* OCV adaptation of video-shotcraft's SpotlightHeroCard, WordRelayFilmstrip,
 * and OutroGroupPhotoLaunch; based on the verified private ShotcraftFilm trial.
 * Modified: data-only plans, source palette/type/material tokens, contained
 * source images, content fitting, per-stage timing and explicit reading holds.
 * No upstream demonstration brands, sample data or hard-coded narration remain.
 * Apache-2.0 sources/attribution: ../lib/video-shotcraft/NOTICE.txt and LICENSE.
 */
import React,{useEffect} from 'react';
import {AbsoluteFill,Img,Sequence,useCurrentFrame} from 'remotion';
import {backgroundLayers} from '../background.mjs';
import {fittedSize,isTextOnlyCard,textOnlyCardGeometry} from '../lib/shotcraft-card-layout.mjs';
import {alpha,bezier,clamp,EASE,mix,ramp,SpeedBlur,velocity} from './lib/ShotcraftMotion';
import type {Palette,ShotcraftItem,ShotcraftPlan,ShotcraftProps,ShotcraftStage} from './lib/shotcraft-types';
import {ExpandedStage} from './ShotcraftExpanded';

const ABS:React.CSSProperties={position:'absolute'};
const FONTS={sans:'"Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", Arial, sans-serif',serif:'"STSong", "SimSun", "Noto Serif CJK SC", Georgia, serif'};
const pop=bezier(.2,1.25,.3,1),settle=bezier(.4,0,.3,1.05),pushEase=bezier(.35,0,.2,1),landEase=bezier(.34,1.4,.44,1);

export type TextProps={
  text?:string;x?:number;y?:number;w:number;h:number;size?:number;min?:number;color:string;weight?:number;label:string;style?:React.CSSProperties;children?:React.ReactNode;
};
function Text({text='',x=0,y=0,w,h,size=48,min=32,color,weight=500,label,style={},children}: TextProps){
  if(!text&&!children)return null;
  const fontSize=fittedSize(text,w,h,size,min);
  // Animated glyph transforms intentionally cross their reveal mask. Audit an
  // identically sized, non-animated text twin so motion is not a false overflow;
  // all settled glyphs and every source character still use this same layout.
  return <div data-vibe-fit={children?undefined:label} style={{...ABS,left:x,top:y,width:w,height:h,fontSize,lineHeight:1.28,fontWeight:weight,color,whiteSpace:'pre-wrap',overflowWrap:'anywhere',overflow:'hidden',...style}}>{children?<><div data-vibe-fit={label} aria-hidden style={{...ABS,inset:0,opacity:0,overflow:'hidden'}}>{text}</div><div style={{position:'relative'}}>{children}</div></>:text}</div>;
}

function Audit({frame}:{frame:number}){
  useEffect(()=>{
    const failures:string[]=[];
    document.querySelectorAll<HTMLElement>('[data-vibe-fit],[data-vibe-card]').forEach(el=>{
      if(el.clientWidth>10&&el.clientHeight>1&&(el.scrollHeight>el.clientHeight+3||el.scrollWidth>el.clientWidth+3))failures.push(`${el.dataset.vibeFit??el.dataset.vibeCard}[${el.scrollWidth}x${el.scrollHeight}/${el.clientWidth}x${el.clientHeight}]`);
    });
    if(failures.length)throw new Error('OCV_LAYOUT_OVERFLOW:'+failures.join(','));
  },[frame]);
  return null;
}

function Field({plan}:{plan:ShotcraftPlan}){
  return <AbsoluteFill style={{background:plan.palette.background}}>{backgroundLayers(plan).map((style:React.CSSProperties,i:number)=><AbsoluteFill key={i} style={style}/>)}</AbsoluteFill>;
}

// Symbolic page/book/inspection/refinement only; no fabricated chart values,
// axes, data points, study figures, result tables or explanatory text.
function Motif({kind,p}:{kind:ShotcraftItem['motif'];p:Palette}){
  if(kind==='none')return null;
  return <svg viewBox="0 0 440 340" width="100%" height="100%" aria-hidden>
    {kind==='document'&&<g fill="none" stroke={p.accent}><path d="M104 46H280L334 100V294H104Z" fill={alpha(p.accent,.035)} strokeWidth="3"/><path d="M280 46V100H334" strokeWidth="3"/>{[0,1,2,3].map(i=><path key={i} d={`M137 ${132+i*35}h${i===3?111:160}`} stroke={p.ink} opacity=".3" strokeWidth="5"/>)}</g>}
    {kind==='book'&&<g fill="none" stroke={p.accent} strokeWidth="3"><path d="M60 77Q137 45 220 83Q303 45 380 77V270Q299 239 220 278Q138 239 60 270Z" fill={alpha(p.accent,.035)}/><path d="M220 83V278"/>{[0,1,2,3].map(i=><g key={i} stroke={p.ink} opacity=".3"><path d={`M87 ${117+i*34}q47 -12 106 4`}/><path d={`M246 ${121+i*34}q47 -15 106 -4`}/></g>)}</g>}
    {kind==='inspection'&&<g fill="none"><rect x="72" y="56" width="214" height="234" rx="5" stroke={p.accent} strokeWidth="3"/>{[0,1,2,3,4].map(i=><path key={i} d={`M99 ${95+i*34}h145`} stroke={p.ink} opacity=".25" strokeWidth="5"/>)}<circle cx="266" cy="158" r="69" fill={p.surface} stroke={p.accent} strokeWidth="6"/><path d="M315 208l53 62" stroke={p.accent} strokeWidth="15" strokeLinecap="round"/><path d="M234 140h61M234 163h48M234 186h54" stroke={p.ink} opacity=".4" strokeWidth="5"/></g>}
    {kind==='refinement'&&<g fill="none" stroke={p.accent} strokeWidth="3"><path d="M86 56H282V282H86Z" fill={alpha(p.accent,.035)}/>{[0,1,2,3].map(i=><path key={i} d={`M113 ${96+i*38}h123`} stroke={p.ink} opacity=".25" strokeWidth="5"/>)}<path d="M296 100l57 43-94 125-47 12 8-48Z" fill={p.surface}/><path d="M220 232l39 36M296 100l-76 132"/><path d="M202 298q62 12 118-5"/></g>}
  </svg>;
}

export type CardProps={item:ShotcraftItem;p:Palette;asset?:string;w:number;h:number;compact?:boolean;alternate?:boolean};
function Card({item,p,asset,w,h,compact=false,alternate=false}:CardProps){
  const pad=compact?24:30,titleH=item.title?(compact?82:112):0,bodyH=item.body?(compact?92:116):0;
  const visualH=Math.max(50,h-pad*2-titleH-bodyH-(titleH||bodyH?16:0));
  const textOnly=isTextOnlyCard(item,asset);
  const textLayout=textOnly?textOnlyCardGeometry(item,w,h,compact):null;
  return <div data-vibe-card={item.id} style={{width:w,height:h,position:'relative',background:alternate?p.background:p.surface,border:`2px solid ${alternate?p.accent:p.line}`,borderRadius:12,overflow:'hidden',boxSizing:'border-box'}}>
    {textLayout?<>
      <Text text={item.title} x={textLayout.pad} y={textLayout.title.top} w={textLayout.width} h={textLayout.title.height} size={textLayout.title.size} color={p.ink} weight={650} label={`${item.id}:title`} style={{textAlign:'center'}}/>
      {item.body&&<Text text={item.body} x={textLayout.pad} y={textLayout.body.top} w={textLayout.width} h={textLayout.body.height} size={textLayout.body.size} color={p.muted} label={`${item.id}:body`} style={{textAlign:'center'}}/>}
    </>:<>
    <div style={{...ABS,left:pad,top:pad,width:w-pad*2,height:visualH}}>
      {asset?<Img src={asset} style={{width:'100%',height:'100%',objectFit:'contain',display:'block'}}/>:<Motif kind={item.motif} p={p}/>}
    </div>
    <Text text={item.title} x={pad} y={pad+visualH+8} w={w-pad*2} h={titleH} size={compact?42:52} color={p.ink} weight={650} label={`${item.id}:title`}/>
    {item.body&&<Text text={item.body} x={pad} y={pad+visualH+8+titleH} w={w-pad*2} h={bodyH} size={compact?34:38} color={p.muted} label={`${item.id}:body`}/>}
    </>}
  </div>;
}

type StageProps={stage:ShotcraftStage;plan:ShotcraftPlan;assets:Record<string,string>;frames:number};
function Edges({stage,p}:{stage:ShotcraftStage;p:Palette}){
  return <><Text text={stage.eyebrow} x={112} y={70} w={1696} h={72} size={37} color={p.accent} label={`${stage.id}:eyebrow`}/><Text text={stage.footer} x={112} y={954} w={1696} h={94} size={35} color={p.muted} label={`${stage.id}:footer`}/></>;
}

function Opening({stage,plan,assets,frames}:StageProps){
  const actual=useCurrentFrame(),p=plan.palette;
  // Compress only when needed; longer narration extends the reading hold,
  // not the 106f action arc. The minimum 4s shot still gives motion a full 3s.
  const f=Math.min(106,actual*106/Math.min(106,frames-30));
  const push=ramp(f,12,26,pushEase),rise=ramp(f,28,12,pop),reseat=ramp(f,88,18,settle);
  const lift=rise*(1-reseat),z=110*lift+Math.sin((f-40)/40*Math.PI*2)*3*lift;
  const note=ramp(f,32,16,EASE.out),mark=ramp(f,48,20,EASE.swift);
  const sweepX=mix(300,650,ramp(f,0,18,EASE.smooth));
  const zoom=mix(.86,1.04,push),cx=mix(960,950,push),cy=540;
  const beam1=f>=43&&f<=59,beam2=f>=65&&f<=87;
  const beamP=beam1?ramp(f,43,16,EASE.linear):ramp(f,65,22,EASE.swift);
  const item=stage.items[0];
  return <AbsoluteFill>
    <AbsoluteFill style={{background:`radial-gradient(ellipse 510px 570px at ${sweepX}px 550px,${alpha(p.accent,.09)},transparent)`,opacity:ramp(f,0,8,EASE.out)}}/>
    <svg width="1920" height="1080" style={{...ABS,opacity:.065}} aria-hidden><path d={`M${sweepX+300} -300L${sweepX-430} 960L${sweepX+420} 960Z`} fill={p.accent} style={{filter:'blur(65px)'}}/></svg>
    <div style={{...ABS,inset:0,perspective:1400*zoom,perspectiveOrigin:'960px 540px'}}><div style={{...ABS,width:1920,height:1080,zoom,transformOrigin:`${cx}px ${cy}px`,transformStyle:'preserve-3d',transform:`translate(${960/zoom-cx}px,${540/zoom-cy}px) rotateY(${mix(0,16,push)}deg) rotateX(${mix(0,5,push)}deg)`}}>
      {[0,1,2].map(i=><div key={i} style={{...ABS,left:190+i*15,top:267+i*17,width:650,height:548,borderRadius:12,border:`2px solid ${p.line}`,background:p.surface,opacity:.18,transform:`rotate(${-9+i*4}deg) translateZ(${-25+i*8}px)`}}/>)}
      <div style={{...ABS,left:205,top:265,width:650,height:548,borderRadius:12,border:`2px solid ${alpha(p.accent,.35*lift)}`,background:p.surface,transform:'translateZ(1px)'}}/>
      <div style={{...ABS,left:205,top:265,width:650,height:548,transform:`translateZ(${z+2}px) rotateZ(${-3*(1-push)}deg)`,transformStyle:'preserve-3d',boxShadow:`${-30*lift}px ${42*lift+8}px ${26+66*lift}px ${alpha(p.ink,.16)}`,borderRadius:12}}>
        <Card item={item} p={p} asset={item.asset_id?assets[item.asset_id]:undefined} w={650} h={548}/>
        {(beam1||beam2)&&<svg width="650" height="548" style={{...ABS,left:0,top:0,overflow:'hidden',borderRadius:12,opacity:beam1?1:.62}} aria-hidden><rect x="3" y="3" width="644" height="542" rx="10" fill="none" stroke={p.accent} strokeWidth="5" pathLength="1" strokeDasharray=".16 1" strokeDashoffset={-beamP}/><rect x="3" y="3" width="644" height="542" rx="10" fill="none" stroke={p.surface} strokeWidth="2" pathLength="1" strokeDasharray=".16 1" strokeDashoffset={-beamP}/></svg>}
      </div>
      <div style={{...ABS,left:1000,top:305,width:760,height:500,transform:`translateZ(${4+54*(1-reseat)}px) translateY(${26*(1-note)}px)`,opacity:note,filter:`blur(${5*(1-note)}px)`}}>
        <div style={{...ABS,left:0,top:12,width:700*mark,height:246,background:alpha(p.accent,.09),transform:'rotate(-1deg)',borderRadius:4}}/>
        <Text text={stage.headline} w={740} h={280} size={105} min={62} color={p.ink} weight={650} label={`${stage.id}:headline`}/>
        <Text text={stage.body} y={315} w={740} h={190} size={43} color={p.muted} label={`${stage.id}:body`}/>
      </div>
    </div></div>
    <Edges stage={stage} p={p}/>
  </AbsoluteFill>;
}

function Relay({stage,plan,assets,frames}:StageProps){
  const f=useCurrentFrame(),p=plan.palette,count=stage.items.length,period=frames/count;
  const switches=stage.items.map((_,i)=>i*period);
  const stepAt=(q:number)=>switches.slice(1).reduce((n,s)=>n+ramp(q,s,18,EASE.swift),0);
  const step=stepAt(f),speed=velocity(q=>stepAt(q)*578,f);
  const idx=Math.min(count-1,Math.floor(f/period)),change=switches[idx];
  const connector=idx===0?ramp(f,2,16):f<change+10?1-ramp(f,change,6,EASE.exit):ramp(f,change+10,16,EASE.out);
  return <AbsoluteFill>
    <div style={{...ABS,left:0,top:170,width:1050,height:730,overflow:'hidden'}}><SpeedBlur vx={0} vy={-speed} amount={.22} max={10}>
      {[-1,0,1].flatMap(rep=>stage.items.map((item,i)=>{
        const index=i+rep*count,y=130+(index-step)*578,dist=Math.min(1.4,Math.abs(index-step));
        if(y>850||y<-520)return null;
        return <div key={`${rep}-${i}`} style={{...ABS,left:112,top:y,width:840,height:480,transform:`scale(${1-.06*Math.min(1,dist)})`,transformOrigin:'center',filter:`blur(${Math.min(3,dist*3)}px)`,opacity:1-Math.min(.68,dist*.68),boxShadow:`0 18px 42px ${alpha(p.ink,.1)}`,borderRadius:12}}><Card item={item} p={p} asset={item.asset_id?assets[item.asset_id]:undefined} w={840} h={480} alternate={i%2!==0}/></div>;
      }))}
    </SpeedBlur></div>
    <svg width="1920" height="1080" style={ABS} aria-hidden><line x1="975" y1="540" x2={975+120*clamp(connector)} y2="540" stroke={p.accent} strokeWidth="2"/><circle cx="975" cy="540" r="5" fill={p.accent}/></svg>
    {/* Combined headline/item-title block is centered exactly at focal-card y=540. */}
    <Text text={stage.headline} x={1130} y={390} w={675} h={110} size={60} min={38} color={p.muted} label={`${stage.id}:headline`}/>
    <div style={{...ABS,left:1130,top:500,width:675,height:190,overflow:'hidden'}}>
      {stage.items.map((item,i)=>{
        const enter=switches[i]+(i?8:0),exit=switches[i+1]??Infinity;
        const leave=ramp(f,exit,8,EASE.exit);
        if(f<enter||leave>=1)return null;
        const chars=[...item.title],stagger=chars.length>1?Math.min(.6,2/(chars.length-1)):0;
        return <Text key={item.id} text={item.title} w={675} h={190} size={105} min={48} color={p.ink} weight={650} label={`${item.id}:relay-title`} style={{transform:`translateY(${-190*leave}px)`,opacity:1-leave}}>{chars.map((c,j)=>{const q=ramp(f,enter+j*stagger,9,EASE.snappy);return c==='\n'?<br key={j}/>:<span key={j} style={{display:'inline-block',whiteSpace:'pre',opacity:q,transform:`translateY(${190*(1-q)}px)`}}>{c}</span>;})}</Text>;
      })}
    </div>
    <Text text={stage.body} x={1130} y={735} w={675} h={160} size={38} color={p.muted} label={`${stage.id}:body`}/>
    <div style={{...ABS,left:1130,top:905,display:'flex',gap:14}}>{stage.items.map((item,i)=><div key={item.id} style={{width:i===idx?74:25,height:5,background:i===idx?p.accent:p.line}}/>)}</div>
    <Edges stage={stage} p={p}/>
  </AbsoluteFill>;
}

function Finale({stage,plan,assets,frames}:StageProps){
  const actual=useCurrentFrame(),f=Math.min(74,actual*74/Math.min(74,frames-30)),p=plan.palette;
  const crane=ramp(f,0,40,EASE.out),headline=ramp(f,23,18,EASE.out);
  const scale=1.06-.06*crane+.025*ramp(f,36,38,EASE.smooth);
  const cast=[{x:112,y:145,dx:-530,dy:-340,rot:-4},{x:1398,y:145,dx:520,dy:-340,rot:4},{x:112,y:598,dx:-440,dy:400,rot:3},{x:1398,y:598,dx:460,dy:380,rot:-3}];
  const characters=[...stage.headline],stagger=characters.length>1?Math.min(1.6,15/(characters.length-1)):0;
  return <AbsoluteFill>
    <div style={{...ABS,inset:0,transform:`perspective(1600px) rotateX(${4*(1-crane)}deg) scale(${scale})`}}>
      <AbsoluteFill style={{background:`radial-gradient(ellipse 48% 56% at 50% 50%,${alpha(p.accent,.08)},transparent)`,opacity:headline}}/>
      {stage.items.map((item,i)=>{
        const el=cast[i],cue=1+i*3,pos=(q:number)=>ramp(q,cue,16,landEase),q=pos(f);
        const press=1-.015*Math.sin(Math.PI*clamp((f-cue-12)/8));
        return <SpeedBlur key={item.id} vx={velocity(v=>el.dx*(1-pos(v)),f)} vy={velocity(v=>el.dy*(1-pos(v)),f)} amount={.3} max={22}><div style={{...ABS,left:el.x+el.dx*(1-q),top:el.y+el.dy*(1-q),opacity:(1-.12*headline)*ramp(f,cue,4),transform:`rotate(${el.rot*(2-q)}deg) scale(${(1.12-.12*q)*press})`,boxShadow:`0 ${16+25*(1-q)}px ${25+35*(1-q)}px ${alpha(p.ink,.15)}`,borderRadius:12}}><Card item={item} p={p} asset={item.asset_id?assets[item.asset_id]:undefined} w={410} h={314} compact alternate={i%2!==0}/></div></SpeedBlur>;
      })}
      <svg width="1920" height="1080" style={ABS} aria-hidden><ellipse cx="960" cy="540" rx="660" ry="340" fill="none" stroke={p.accent} opacity=".09"/>{Array.from({length:16},(_,i)=>{
        const a=i*2.399,r=310+(i%4)*90;return <circle key={i} cx={960+Math.cos(a)*r*1.6} cy={540+Math.sin(a)*r*.85+Math.sin(f/55+i)*8} r={i%4===0?3:1.5} fill={p.accent} opacity={.09+.12*headline}/>;
      })}</svg>
    </div>
    <div style={{...ABS,left:548,top:308,width:824,height:470,textAlign:'center',background:`radial-gradient(ellipse at 50% 48%,${alpha(p.background,.98)},${alpha(p.background,.92)} 60%,${alpha(p.background,0)} 78%)`}}>
      <Text text={stage.headline} x={0} y={16} w={824} h={245} size={104} min={52} color={p.ink} weight={650} label={`${stage.id}:headline`}>
        {characters.map((c,i)=>{const q=ramp(f,25+i*stagger,10,bezier(.2,.75,.3,1));return c==='\n'?<br key={i}/>:<span key={i} style={{display:'inline-block',whiteSpace:'pre',opacity:q,transform:`translateY(${55*(1-q)}px) scale(${1.12-.12*q})`,filter:`blur(${3*(1-q)}px)`}}>{c}</span>;})}
      </Text>
      <div style={{...ABS,left:82,top:283,height:2,width:660*ramp(f,45,18,EASE.out),background:p.accent}}/>
      <Text text={stage.body} x={25} y={325} w={774} h={145} size={40} color={p.muted} label={`${stage.id}:body`} style={{opacity:ramp(f,43,17)}}/>
    </div>
    <Edges stage={stage} p={p}/>
  </AbsoluteFill>;
}

export function ShotcraftScene({plan,assets}:ShotcraftProps){
  const frame=useCurrentFrame(),[width,height]=plan.canvas;
  const scale=Math.min(width/1920,height/1080);
  return <AbsoluteFill style={{background:plan.palette.background,fontFamily:FONTS[plan.typography],color:plan.palette.ink,overflow:'hidden'}}>
    <Field plan={plan}/>
    <div style={{...ABS,left:(width-1920*scale)/2,top:(height-1080*scale)/2,width:1920,height:1080,transform:`scale(${scale})`,transformOrigin:'top left'}}>
      {plan.stages.map(stage=>{
        const from=Math.round(stage.start*30),frames=Math.round(stage.end*30)-from,Component=stage.recipe==='spotlight-hero-card'?Opening:stage.recipe==='word-relay-filmstrip'?Relay:stage.recipe==='outro-group-photo-launch'?Finale:null;
        return <Sequence key={stage.id} from={from} durationInFrames={frames}>{Component?<Component stage={stage} plan={plan} assets={assets} frames={frames}/>:<ExpandedStage stage={stage} plan={plan} assets={assets} frames={frames} Text={Text} Card={Card} Edges={Edges}/>}</Sequence>;
      })}
    </div>
    <Audit frame={frame}/>
  </AbsoluteFill>;
}
