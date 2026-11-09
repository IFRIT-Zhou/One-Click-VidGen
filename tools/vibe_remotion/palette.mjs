export const THEMES = {
  paper: {background:'#F6F3ED',ink:'#152D34',muted:'#64757A',surface:'#FFFEFA',line:'#D9E0DB',accent:'#217568',soft:'#E7EEE6'},
  clinical: {background:'#F2F7F8',ink:'#102F43',muted:'#5E7683',surface:'#FFFFFF',line:'#D5E4E9',accent:'#167B91',soft:'#E4F0F3'},
  midnight: {background:'#0B1821',ink:'#F1F6F6',muted:'#A1B8C1',surface:'#142A35',line:'#304A55',accent:'#89D9BF',soft:'#213F46'},
};
export const PALETTE_KEYS=['background','ink','muted','surface','line','accent'];
export function luminance(hex) {
  const channels=[1,3,5].map(offset=>parseInt(hex.slice(offset,offset+2),16)/255)
    .map(s=>s<=.04045?s/12.92:((s+.055)/1.055)**2.4);
  return .2126*channels[0]+.7152*channels[1]+.0722*channels[2];
}
export function contrastRatio(a,b) {
  const [x,y]=[luminance(a),luminance(b)];
  return (Math.max(x,y)+.05)/(Math.min(x,y)+.05);
}
export function resolvePalette(plan) {
  return {...THEMES[plan.theme],...(plan.accent?{accent:plan.accent}:{}),...plan.palette};
}
export function validatePalette(plan) {
  const palette=plan.palette;
  if(palette!==undefined) {
    if(!palette||typeof palette!=='object'||Array.isArray(palette)) throw new Error('Invalid palette');
    for(const [key,value] of Object.entries(palette)) {
      if(!PALETTE_KEYS.includes(key)||typeof value!=='string'||!/^#[0-9a-fA-F]{6}$/.test(value)) throw new Error('Invalid palette.'+key);
    }
  }
  const effective=resolvePalette(plan);
  if(contrastRatio(effective.ink,effective.background)<4.5) throw new Error('Palette ink/background contrast must be at least 4.5');
  if(contrastRatio(effective.muted,effective.background)<3) throw new Error('Palette muted/background contrast must be at least 3');
  return effective;
}
