import type { WorkbenchManifest } from './cards/manifest';
import type { ProjectData } from './types';
import { selectedTheme, validThemeColors } from './theme';
import { t, themeLabel } from './i18n';

export type PalettePreset = {format:'shotcraft-palette';version:1;name:string;baseThemeId:string;colors:Record<string,string>};
export const paletteFromProject = (m:WorkbenchManifest | null,p:ProjectData,name:string):PalettePreset => {
  const theme=selectedTheme(m,p.themeId);
  if(!theme?.palette)throw Error(t("palette.needTheme"));
  return {format:'shotcraft-palette',version:1,name:name.trim().slice(0,60)||`${themeLabel(theme.label)} · ${t("palette.custom")}`,baseThemeId:theme.id,colors:{...theme.palette,...validThemeColors(m,p.themeId,p.themeColors)}};
};
export const parsePalette = (text:string,m:WorkbenchManifest | null):PalettePreset => {
  if(text.length>65536)throw Error(t("palette.tooBig"));
  let value:unknown;
  try{value=JSON.parse(text)}catch{throw Error(t("palette.badJson"))}
  if(!value||typeof value!=='object')throw Error(t("palette.badFormat"));
  const p=value as Partial<PalettePreset>;
  if(p.format!=='shotcraft-palette'||p.version!==1||typeof p.name!=='string'||!p.name.trim()||p.name.length>60)throw Error(t("palette.badVersion"));
  const theme=m?.themes?.find(t=>t.id===p.baseThemeId);
  if(!theme?.palette)throw Error(t("palette.badTheme"));
  if(!p.colors||typeof p.colors!=='object'||Array.isArray(p.colors))throw Error(t("palette.noColors"));
  const keys=Object.keys(theme.palette);
  if(Object.keys(p.colors).length!==keys.length||keys.some(k=>typeof p.colors?.[k]!=='string'||!/^#[0-9a-f]{6}$/i.test(p.colors[k])))throw Error(t("palette.badColors"));
  return {format:'shotcraft-palette',version:1,name:p.name.trim(),baseThemeId:theme.id,colors:Object.fromEntries(keys.map(k=>[k,p.colors![k]]))};
};
export const applyPalette = (p:ProjectData,preset:PalettePreset):ProjectData => ({...p,themeId:preset.baseThemeId,themeColors:{...preset.colors}});
