import {resolvePalette} from './palette.mjs';

export const BACKGROUND_KINDS=['solid','soft_gradient','technical_grid','paper'];

// Frame-driven, deterministic ambient motion; no wall-clock CSS animation.
export function ambientMotion(time,kind) {
  if(!Number.isFinite(time))throw new Error('Invalid ambient time');
  if(!kind||kind==='solid'||kind==='paper')return {enabled:false,x:0,y:0,rotation:0};
  return {enabled:true,x:Math.sin(time*.14)*2.4,y:Math.cos(time*.11)*1.8,rotation:time*1.6};
}

const rgba=(hex,alpha)=>`rgba(${[1,3,5].map(i=>parseInt(hex.slice(i,i+2),16)).join(',')},${alpha})`;

export function validateBackground(background) {
  if(background===undefined)return;
  if(!background||typeof background!=='object'||Array.isArray(background)||
    Object.keys(background).some(key=>key!=='kind')||!BACKGROUND_KINDS.includes(background.kind)) {
    throw new Error('Invalid background: kind must be solid, soft_gradient, technical_grid or paper');
  }
}

// Native deterministic CSS only. There is no file, network, arbitrary model CSS,
// generated noise, or theme-derived light overlay when a source palette is bound.
export function backgroundLayers(plan,scale=1) {
  const palette=resolvePalette(plan);
  const kind=plan.background?.kind;
  if(!kind)return plan.palette?[]:[{background:`radial-gradient(ellipse at 90% 5%, ${palette.soft} 0%, transparent 60%)`}];
  if(kind==='solid')return [];
  const px=n=>Math.max(.5,Math.round(n*scale*100)/100);
  if(kind==='soft_gradient')return [{background:[
    `radial-gradient(ellipse at 92% 8%, ${rgba(palette.accent,.095)} 0%, transparent 57%)`,
    `radial-gradient(ellipse at 6% 92%, ${rgba(palette.line,.09)} 0%, transparent 56%)`,
  ].join(',')}];
  if(kind==='technical_grid')return [
    {backgroundImage:[
      `linear-gradient(${rgba(palette.line,.10)} ${px(1)}px, transparent ${px(1)}px)`,
      `linear-gradient(90deg, ${rgba(palette.line,.10)} ${px(1)}px, transparent ${px(1)}px)`,
    ].join(','),backgroundSize:`${px(64)}px ${px(64)}px`,maskImage:`linear-gradient(90deg, transparent 0%, ${rgba(palette.ink,1)} 68%)`},
    {background:[
      `radial-gradient(ellipse at 106% 95%, transparent 0% 41%, ${rgba(palette.accent,.075)} 41.1% 41.25%, transparent 41.4%)`,
      `radial-gradient(ellipse at 106% 95%, transparent 0% 49%, ${rgba(palette.line,.065)} 49.1% 49.25%, transparent 49.4%)`,
    ].join(',')},
  ];
  if(kind==='paper')return [{background:[
    `repeating-linear-gradient(113deg, ${rgba(palette.line,.055)} 0px ${px(.55)}px, transparent ${px(.7)}px ${px(6)}px)`,
    `repeating-linear-gradient(7deg, ${rgba(palette.ink,.017)} 0px ${px(.5)}px, transparent ${px(.8)}px ${px(13)}px)`,
    `radial-gradient(ellipse at 17% 4%, ${rgba(palette.surface,.055)} 0%, transparent 71%)`,
  ].join(',')}];
  throw new Error('Invalid background kind');
}
