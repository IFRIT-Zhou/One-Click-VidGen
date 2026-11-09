/* Adapted from video-shotcraft demos/_fixtures/Polish.tsx (Apache-2.0).
 * Modified for OCV: reduced to deterministic motion primitives and SpeedBlur.
 * Source and license: ../../lib/video-shotcraft/NOTICE.txt and LICENSE.
 */
import React from 'react';
export const clamp=(x:number)=>Math.max(0,Math.min(1,x));
export const bezier=(x1:number,y1:number,x2:number,y2:number)=>{
  const ax=1-3*x2+3*x1,bx=3*x2-6*x1,cx=3*x1;
  const ay=1-3*y2+3*y1,by=3*y2-6*y1,cy=3*y1;
  const sx=(u:number)=>((ax*u+bx)*u+cx)*u,sy=(u:number)=>((ay*u+by)*u+cy)*u;
  return (t:number)=>{
    if(t<=0)return 0;if(t>=1)return 1;
    let lo=0,hi=1,u=t;
    for(let i=0;i<40;i++){const x=sx(u);if(Math.abs(x-t)<1e-7)break;if(x<t)lo=u;else hi=u;u=(lo+hi)/2;}
    return sy(u);
  };
};
export const EASE={snappy:bezier(.16,1,.3,1),out:bezier(.22,1,.36,1),smooth:bezier(.65,0,.35,1),swift:bezier(.4,0,.2,1),exit:bezier(.7,0,.84,0),linear:clamp};
export const ramp=(frame:number,start:number,duration:number,ease:(n:number)=>number=EASE.snappy)=>ease(clamp((frame-start)/Math.max(1e-6,duration)));
export const mix=(a:number,b:number,t:number)=>a+(b-a)*t;
export const velocity=(fn:(f:number)=>number,f:number)=>fn(f+.5)-fn(f-.5);
export const alpha=(hex:string,a:number)=>`rgba(${[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(',')},${clamp(a)})`;
export function SpeedBlur({vx,vy=0,amount=.3,max=22,children}:{vx:number;vy?:number;amount?:number;max?:number;children:React.ReactNode}){
  const id='shotcraft-blur-'+React.useId().replace(/[^a-zA-Z0-9]/g,'');
  const sd=Math.min(max,Math.hypot(vx,vy)*amount);
  const fill:React.CSSProperties={position:'absolute',inset:0};
  if(sd<.35)return <div style={fill}>{children}</div>;
  const angle=Math.atan2(vy,vx)*180/Math.PI;
  return <div style={fill}><svg width={0} height={0} style={{position:'absolute'}} aria-hidden><filter id={id} x="-50%" y="-50%" width="200%" height="200%" colorInterpolationFilters="sRGB"><feGaussianBlur stdDeviation={`${sd.toFixed(2)} 0`} edgeMode="none"/></filter></svg><div style={{...fill,transform:`rotate(${angle}deg)`,filter:`url(#${id})`}}><div style={{...fill,transform:`rotate(${-angle}deg)`}}>{children}</div></div></div>;
}
