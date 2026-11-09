// Independent, allowlisted layers. No generated code, CSS, URLs or templates.
export const LAYERED_VERSION='ocv-layered-v1';
export const LAYERED_RENDERER='ocv-remotion-layered-v1';
export const MAX_LAYERED_ELEMENTS=128;
const TYPES=['image','text','rect','ellipse','line','polyline'];
const MOTIONS=['none','fade','slide_left','slide_right','slide_up','scale','grow','draw'];
const COMMON=['id','type','role','box','at','end','enter','exit','motion','rotation','opacity','shadow'];
const SPEC={image:['asset_id','fit','edge_fade_right'],text:['text','font_size','min_font_size','valign','gradient','bold','align','fill','line_height','letter_spacing','font_family','readability_halo'],rect:['fill','stroke','stroke_width','radius','gradient','glow'],ellipse:['fill','stroke','stroke_width'],line:['points','stroke','stroke_width'],polyline:['points','stroke','stroke_width']};
const object=(value,allowed,label)=>{
  if(!value||typeof value!=='object'||Array.isArray(value))throw new Error('Invalid '+label);
  for(const key of Object.keys(value))if(!allowed.includes(key))throw new Error(`Unsupported ${label}.${key}`);
};
const number=(value,min,max,label)=>{if(!Number.isFinite(value)||value<min||value>max)throw new Error('Invalid '+label);};
const colour=(value,label)=>{if(typeof value!=='string'||!/^#[0-9a-fA-F]{6}$/.test(value))throw new Error('Invalid '+label);};
const identifier=value=>typeof value==='string'&&/^[a-zA-Z0-9_:-]{1,80}$/.test(value);

export function validateLayeredInput(raw){
  const input=structuredClone(raw);
  object(input,['plan','assets','duration','fps'],'input');
  const {plan,duration,fps}=input;
  number(duration,.1,60,'duration');
  if(fps!==30)throw new Error('Layered scenes require 30 fps');
  object(plan,['version','canvas','background','fade_in','fade_out','elements'],'plan');
  if(plan.version!==LAYERED_VERSION)throw new Error('Unsupported layered version');
  if(!Array.isArray(plan.canvas)||plan.canvas.length!==2||plan.canvas.some((v,i)=>!Number.isInteger(v)||v<(i===0?160:90)||v>1920||v%2)||plan.canvas[0]*plan.canvas[1]>1920*1080)throw new Error('Invalid canvas');
  const [width,height]=plan.canvas;
  plan.background??='#F3EEE4';colour(plan.background,'background');
  plan.fade_in??=0;plan.fade_out??=0;
  if(plan.fade_in!==0||plan.fade_out!==0)throw new Error('Layered backgrounds must not fade to a blank canvas; animate content layers instead');
  object(input.assets,Object.keys(input.assets??{}),'assets');
  if(Object.keys(input.assets).length>40)throw new Error('Too many assets');
  for(const [id,file] of Object.entries(input.assets))if(!identifier(id)||typeof file!=='string'||!file)throw new Error('Invalid asset binding');
  if(!Array.isArray(plan.elements)||!plan.elements.length||plan.elements.length>MAX_LAYERED_ELEMENTS)throw new Error(`A layered scene needs 1–${MAX_LAYERED_ELEMENTS} elements`);
  const ids=new Set();let area=0,textSize=0;
  for(const e of plan.elements){
    if(!TYPES.includes(e?.type))throw new Error('Unsupported layer type');
    object(e,[...COMMON,...SPEC[e.type]],'element');
    if(!identifier(e.id)||ids.has(e.id))throw new Error('Invalid/duplicate element id');ids.add(e.id);
    if(!Array.isArray(e.box)||e.box.length!==4||e.box.some(v=>!Number.isFinite(v)))throw new Error('Invalid layer box');
    const [x,y,w,h]=e.box;
    if(x<0||y<0||w<1||h<1||x+w>width+.01||y+h>height+.01)throw new Error('Layer box outside canvas: '+e.id);
    area+=w*h;if(area>12*width*height)throw new Error('Layer area budget exceeded');
    e.role??='content';e.at??=0;e.end??=duration;e.enter??=.6;e.exit??=0;e.motion??='fade';e.rotation??=0;e.opacity??=1;e.shadow??='none';
    if(!['background','content'].includes(e.role))throw new Error('Invalid layer role');
    number(e.at,0,duration,'layer at');number(e.end,0,duration+.001,'layer end');
    number(e.enter,0,5,'layer enter');number(e.exit,0,5,'layer exit');
    if(e.at>=e.end||e.enter+e.exit>e.end-e.at+.001)throw new Error('Invalid layer timing: '+e.id);
    const firstFrame=Math.ceil(e.at*fps-1e-7),lastFrame=Math.min(Math.round(duration*fps)-1,Math.ceil(e.end*fps-1e-7)-1);
    if(firstFrame>lastFrame||(e.enter>0&&e.motion!=='none'&&lastFrame/fps<=e.at))throw new Error('Layer has no visible video frame: '+e.id);
    if(!MOTIONS.includes(e.motion))throw new Error('Invalid motion');
    if(e.motion==='draw'&&!['line','polyline'].includes(e.type))throw new Error('draw is only supported for lines');
    number(e.rotation,-15,15,'rotation');number(e.opacity,0,1,'opacity');
    const radians=Math.abs(e.rotation)*Math.PI/180,rotatedW=w*Math.cos(radians)+h*Math.sin(radians),rotatedH=w*Math.sin(radians)+h*Math.cos(radians);
    if(x+w/2-rotatedW/2<-.01||y+h/2-rotatedH/2<-.01||x+w/2+rotatedW/2>width+.01||y+h/2+rotatedH/2>height+.01)throw new Error('Rotated layer extends outside canvas: '+e.id);
    if(!['none','soft'].includes(e.shadow))throw new Error('Invalid shadow');
    if(e.role==='background'&&(e.at!==0||Math.abs(e.end-duration)>.001||e.enter!==0||e.exit!==0||e.motion!=='none'||e.opacity!==1))throw new Error('Background layers must remain visible for the entire scene');
    if(e.gradient!==undefined){
      object(e.gradient,['color','angle'],'gradient');colour(e.gradient.color,'gradient color');number(e.gradient.angle,0,360,'gradient angle');
    }
    if(e.type==='image'){
      if(!identifier(e.asset_id)||!Object.hasOwn(input.assets,e.asset_id))throw new Error('Unbound image asset: '+e.asset_id);
      e.fit??='contain';if(!['contain','cover'].includes(e.fit))throw new Error('Invalid image fit');
      if(e.edge_fade_right!==undefined){
        if(e.role!=='content')throw new Error('Edge fade is only supported for foreground images');
        number(e.edge_fade_right,0,.25,'image edge_fade_right');
      }
    }else if(e.type==='text'){
      if(typeof e.text!=='string'||!e.text.trim()||e.text.length>2000)throw new Error('Invalid layer text');
      textSize+=e.text.length;if(textSize>12000)throw new Error('Too much scene text');
      e.font_size??=42;e.bold??=false;e.align??='left';e.fill??='#182F3B';e.line_height??=1.2;e.letter_spacing??=0;e.font_family??='sans';
      number(e.font_size,12,200,'font size');number(e.line_height,1,1.8,'line height');number(e.letter_spacing,-3,12,'letter spacing');
      if(e.min_font_size!==undefined)number(e.min_font_size,12,e.font_size,'minimum font size');
      e.valign??='top';if(!['top','middle','bottom'].includes(e.valign))throw new Error('Invalid text vertical alignment');
      if(typeof e.bold!=='boolean'||!['left','center','right'].includes(e.align)||!['sans','serif'].includes(e.font_family))throw new Error('Invalid text typography');
      colour(e.fill,'text fill');
      if(e.readability_halo!==undefined){
        object(e.readability_halo,['color','opacity'],'text readability_halo');
        colour(e.readability_halo.color,'text readability_halo.color');
        number(e.readability_halo.opacity,0,1,'text readability_halo.opacity');
      }
    }else{
      e.stroke_width??=2;number(e.stroke_width,0,30,'stroke width');
      if(e.stroke!==undefined)colour(e.stroke,'stroke');
      if(['rect','ellipse'].includes(e.type)){
        e.fill??='#D5B66E';colour(e.fill,'shape fill');
        if(e.type==='rect'){e.radius??=0;number(e.radius,0,500,'radius');if(e.glow!==undefined)number(e.glow,0,30,'shape glow');}
      }else{
        e.stroke??='#182F3B';
        if(!Array.isArray(e.points)||e.points.length<2||e.points.length>512||(e.type==='line'&&e.points.length!==2))throw new Error('Invalid line points');
        for(const p of e.points)if(!Array.isArray(p)||p.length!==2||!Number.isFinite(p[0])||!Number.isFinite(p[1])||p[0]<0||p[0]>w||p[1]<0||p[1]>h)throw new Error('Line points must be local to their box');
        if(!e.points.slice(1).some((p,i)=>p[0]!==e.points[i][0]||p[1]!==e.points[i][1]))throw new Error('Line path is empty');
      }
    }
  }
  if(!plan.elements.some(e=>e.type==='image'&&e.role==='background'&&e.fit==='cover'&&e.rotation===0&&e.box[0]===0&&e.box[1]===0&&e.box[2]===width&&e.box[3]===height))throw new Error('Layered posters require a full-canvas persistent background image');
  if(!plan.elements.some(e=>e.role==='content'&&e.opacity>0))throw new Error('Scene has no visible content layer');
  return input;
}

// Img uses object-position:center. A portrait inside a wide contain box has
// transparent gutters: fading the last 15% of the *box* might miss it entirely.
// For cover, use the visible crop rather than pixels outside the box.
export function visibleImageFadeStops(w,h,sourceW,sourceH,fit,fraction){
  if(![w,h,sourceW,sourceH].every(v=>Number.isFinite(v)&&v>0))throw new Error('Invalid visible image dimensions');
  if(!['contain','cover'].includes(fit))throw new Error('Invalid image fit');
  number(fraction,0,.25,'image edge_fade_right');
  const visibleWidth=fit==='contain'?Math.min(w,h*sourceW/sourceH):w;
  const end=(w+visibleWidth)/2;
  return {start:end-visibleWidth*fraction,end};
}

export function layeredProofFrames(plan,duration,fps=30){
  const max=Math.max(1,Math.round(duration*fps))-1;
  const idx=t=>Math.max(0,Math.min(max,Math.round(t*fps)));
  const stages=plan.elements.filter(e=>e.role!=='background').map((e,i)=>{
    const t=Math.min(e.end-e.exit-.05,e.at+e.enter+.15);
    const frame=idx(Math.max(e.at,t));
    return {id:e.id,file:`layer_${String(i).padStart(2,'0')}.png`,frame,time:frame/fps};
  });
  return {stages,details:{early:0,middle:idx(duration/2),end:max}};
}

export function layerMotion(element,time,canvas){
  const clamp=x=>Math.max(0,Math.min(1,x)),ease=x=>1-(1-clamp(x))**3;
  const entered=element.enter?ease((time-element.at)/element.enter):1;
  const exited=element.exit?ease((element.end-time)/element.exit):1;
  const visible=time>=element.at&&time<element.end;
  const alpha=['none','grow','draw'].includes(element.motion)?1:entered;
  return {opacity:visible?element.opacity*alpha*exited:0,
    x:element.motion==='slide_left'?-Math.min(canvas[0]*.04,100)*(1-entered):element.motion==='slide_right'?Math.min(canvas[0]*.04,100)*(1-entered):0,
    y:element.motion==='slide_up'?Math.min(canvas[1]*.04,70)*(1-entered):0,
    scale:element.motion==='scale'?.92+.08*entered:1,
    reveal:['grow','draw'].includes(element.motion)?entered:1};
}
