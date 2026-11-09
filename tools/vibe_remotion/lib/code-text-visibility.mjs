/** Recognize paint actually used by transparent gradient text.
 * This is evidence classification, not a CSS sanitizer or a new CSS allowance.
 * The existing style sandbox still runs before any scene reaches the browser.
 * Unknown colour/gradient syntax stays unverified instead of being guessed.
 */
import {safeStyle} from './code-render-contract.mjs';

const MIN_ALPHA=.03;
const number=/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)%?$/;
function alpha(value){
  const text=String(value).trim();
  if(!number.test(text))return null;
  const result=parseFloat(text)/(text.endsWith('%')?100:1);
  return Number.isFinite(result)&&result>=0&&result<=1?result:null;
}

export function textColorAlpha(value){
  if(typeof value!=='string')return null;
  const text=value.trim().toLowerCase();
  if(text==='transparent')return 0;
  if(/^#[0-9a-f]{3}(?:[0-9a-f])?$/.test(text))return text.length===5?parseInt(text[4]+text[4],16)/255:1;
  if(/^#[0-9a-f]{6}(?:[0-9a-f]{2})?$/.test(text))return text.length===9?parseInt(text.slice(7),16)/255:1;
  const match=/^rgba?\(([^()]*)\)$/.exec(text);
  if(!match)return null;
  const body=match[1].trim();
  let channels,opacity=1;
  if(body.includes(',')){
    const parts=body.split(',').map(x=>x.trim());
    if(parts.length!==3&&parts.length!==4)return null;
    channels=parts.slice(0,3);
    if(parts.length===4)opacity=alpha(parts[3]);
  }else{
    const parts=body.split('/');
    if(parts.length>2)return null;
    channels=parts[0].trim().split(/\s+/);
    if(parts.length===2)opacity=alpha(parts[1]);
  }
  if(channels.length!==3||channels.some(x=>!number.test(x))||opacity===null)return null;
  return opacity;
}

function splitTopLevel(value){
  const result=[];let depth=0,start=0;
  for(let i=0;i<value.length;i++){
    const char=value[i];
    if(char==='(')depth++;
    else if(char===')'&&--depth<0)return null;
    else if(char===','&&depth===0){result.push(value.slice(start,i).trim());start=i+1;}
  }
  if(depth!==0)return null;
  result.push(value.slice(start).trim());
  return result.every(Boolean)?result:null;
}

function gradientVisible(layer){
  const match=/^(?:repeating-)?(?:linear|radial|conic)-gradient\((.*)\)$/i.exec(layer);
  if(!match)return false;
  const stops=splitTopLevel(match[1]);
  if(!stops)return false;
  const opacities=[];
  for(let index=0;index<stops.length;index++){
    const stop=stops[index];
    const color=/^(#[0-9a-f]{3,8}\b|rgba?\([^()]*\)|transparent\b)(.*)$/i.exec(stop);
    if(!color){
      // Only the first entry may be a direction/shape/position prelude.
      // A plain midpoint hint is also non-paint; other unknown syntax fails.
      if(index===0&&/^(?:[+-]?[\d.]+(?:deg|turn|rad|grad)|to\s+(?:left|right|top|bottom)(?:\s+(?:left|right|top|bottom))?|(?:circle|ellipse|closest-side|farthest-side|closest-corner|farthest-corner)(?:\s+[a-z\d.%+-]+)*|from\s+[\d.+-]+(?:deg|turn|rad|grad)(?:\s+at\s+[a-z\d.%+-]+(?:\s+[a-z\d.%+-]+)?)?)$/i.test(stop))continue;
      if(/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:%|px|deg|turn|rad|grad)$/.test(stop))continue;
      return false;
    }
    if(color[2].trim()&&!/^(?:\s+[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:%|px|deg|turn|rad|grad)?){1,2}$/.test(color[2]))return false;
    const amount=textColorAlpha(color[1]);
    if(amount===null)return false;
    opacities.push(amount);
  }
  // Do not infer visibility from an opaque stop outside the painted range.
  // Mixed transparent gradients require visual review, not an automatic pass.
  return opacities.length>=2&&opacities.every(x=>x>=MIN_ALPHA);
}

function hasClippedGradient(style){
  const background=style.backgroundImage;
  const clipping=style.backgroundClip||style.webkitBackgroundClip||style.WebkitBackgroundClip;
  if(typeof background!=='string'||typeof clipping!=='string')return false;
  try{safeStyle({backgroundImage:background});}catch{return false;}
  const layers=splitTopLevel(background),clips=splitTopLevel(clipping);
  if(!layers||layers.length!==1||!clips||clips.some(x=>!['text','border-box','padding-box','content-box'].includes(x)))return false;
  // No mixed image/resource/unknown layers are accepted as gradient evidence.
  if(layers.some(x=>x!=='none'&&!/^(?:repeating-)?(?:linear|radial|conic)-gradient\(/i.test(x)))return false;
  return layers.some((layer,index)=>clips[index%clips.length]==='text'&&gradientVisible(layer));
}

/** CSS computed values only. Ancestor gradients may paint descendant glyphs;
 * display/visibility/combined opacity and geometry remain checked by caller. */
export function textPaintVisible(style,ancestors=[]){
  const fill=style.webkitTextFillColor||style.WebkitTextFillColor||style.textFillColor;
  const color=!fill||fill==='currentcolor'||fill==='currentColor'?style.color:fill;
  const amount=textColorAlpha(color);
  if(amount===null)return false;
  if(amount>=MIN_ALPHA)return true;
  return [style,...ancestors].some(hasClippedGradient);
}
