import { createContext, useContext, type FC, type ReactNode } from 'react';
import { staticFile } from 'remotion';
import palettes from './palettes.json';
import { paletteAsset } from './palette-assets';

export type ThemeId = 'ink-press' | keyof typeof palettes;
const font = '"Segoe UI Variable", "Segoe UI", "Microsoft YaHei", Arial, sans-serif';
export const THEME = {
  id: 'modern-light', page: '#f4f7fb', surface: '#ffffff', field: '#edf2f8',
  text: '#142238', muted: '#5c6b82', accent: '#2563eb', border: '#d8e2ef',
  shadowRgb: '20,34,56', pageRgb: '244,247,251', lightRgb: '239,245,255',
  accentRgb: '37,99,235', stage: '#142238', font,
};
export type VisualTheme = typeof THEME;
const rgb = (hex: string) => [1,3,5].map(i => parseInt(hex.slice(i,i+2),16)).join(',');
export const VISUAL_THEMES: Record<ThemeId, VisualTheme> = {
  'ink-press': {...THEME, id:'ink-press', page:'#f2eee6'},
  ...Object.fromEntries(Object.entries(palettes).map(([id,p]) => [id, {...THEME,...p,id,
    pageRgb:rgb(p.page),accentRgb:rgb(p.accent),shadowRgb:rgb(p.text),lightRgb:rgb(p.surface),stage:p.field}]))
} as Record<ThemeId, VisualTheme>;
export const resolveTheme = (id?: string, colors?: Record<string, string>): VisualTheme => {
  const base = VISUAL_THEMES[id as ThemeId] ?? VISUAL_THEMES['ink-press'];
  if (base.id === 'ink-press' || !colors) return base;
  const valid = Object.fromEntries(Object.entries(colors).filter(([k,v]) => k in palettes['modern-light'] && /^#[0-9a-f]{6}$/i.test(v)));
  const p = {...base,...valid};
  return {...p,pageRgb:rgb(p.page),accentRgb:rgb(p.accent),shadowRgb:rgb(p.text),lightRgb:rgb(p.surface),stage:p.field};
};
const ThemeContext = createContext(VISUAL_THEMES['ink-press']);
export const VisualThemeProvider: FC<{theme?: string; colors?: Record<string,string>; children: ReactNode}> = ({theme, colors, children}) =>
  <ThemeContext.Provider value={resolveTheme(theme, colors)}>{children}</ThemeContext.Provider>;
export const useVisualTheme = () => useContext(ThemeContext);
export const rgba = (color: string, alpha: number) =>
  `color-mix(in srgb, ${color} ${Math.max(0, Math.min(1, alpha)) * 100}%, transparent)`;
export const themeAsset = (theme: VisualTheme, src: string) => {
  if (src.startsWith('textures/live/') && theme.id !== 'ink-press') {
    const asset = paletteAsset(theme, src.slice('textures/live/'.length));
    if (!asset) throw new Error(`Missing editable texture: ${src}`);
    return asset;
  }
  return staticFile(src);
};
// Roles of the original scene materials. Warm light is illumination, not an
// amber UI accent; unknown RGB colors are left alone instead of guessed.
const INK_RGB_ROLES: Record<string, 'pageRgb' | 'lightRgb' | 'accentRgb' | 'shadowRgb'> = {
  '250,247,242': 'pageRgb',
  '255,190,120': 'lightRgb', '255,214,150': 'lightRgb',
  '255,240,210': 'lightRgb', '255,240,214': 'lightRgb',
  '255,241,214': 'lightRgb', '255,244,224': 'lightRgb',
  '255,246,228': 'lightRgb', '255,248,232': 'lightRgb',
  '255,248,235': 'lightRgb', '255,255,255': 'lightRgb',
  '180,120,50': 'accentRgb',
  '0,0,0': 'shadowRgb', '30,25,18': 'shadowRgb',
  '31,41,55': 'shadowRgb', '40,30,20': 'shadowRgb',
  '60,45,30': 'shadowRgb', '62,48,32': 'shadowRgb', '70,56,38': 'shadowRgb',
};

/** Material adapter for existing Ink Press CSS. Returning the input verbatim keeps
 * the default preset backward compatible, including its original gradients. */
export const themePaint = (theme: VisualTheme, css: string): string => {
  if (theme.id === 'ink-press') return css;
  const hex: Record<string, string> = {
    '#f2eee6': theme.page, '#faf7f2': theme.page, '#f9f6f1': theme.page,
    '#fdfcfa': theme.page, '#fefcf9': theme.field, '#fff': theme.surface,
    '#13110f': theme.text, '#1f2937': theme.text, '#955905': theme.accent,
    '#ae6700': theme.accent, '#b5651d': theme.accent,
    '#65635f': theme.muted, '#575552': theme.muted, '#6b7280': theme.muted, '#9ca3af': theme.muted,
  };
  return css.replace(/#[\da-f]{3,8}\b/gi, c => hex[c.toLowerCase()] ?? c)
    .replace(/oklch\(\s*([\d.]+)%?[^)]+\)/g, (cssColor, lightness) => {
      const l = Number(lightness) <= 1 ? Number(lightness) * 100 : Number(lightness);
      const color = l >= 94 ? theme.page : l >= 80 ? theme.border : l >= 40 ? theme.accent : theme.text;
      const alpha = cssColor.match(/\/\s*([\d.]+)(%)?/);
      return alpha ? rgba(color, Number(alpha[1]) / (alpha[2] ? 100 : 1)) : color;
    })
    .replace(/(rgba?)\(\s*(\d+),\s*(\d+),\s*(\d+)/g, (cssColor, fn, r, g, b) => {
      const role = INK_RGB_ROLES[`${+r},${+g},${+b}`];
      return role ? `${fn}(${theme[role]}` : cssColor;
    });
};

/** Per-preset defaults, shared by direct rendering and the workbench manifest. */
export const sceneDefaults = <T extends Record<string, unknown>>(theme: VisualTheme, key: string, defaults: T): T => {
  if (theme.id === 'ink-press') return defaults;
  const mapped = Object.fromEntries(Object.entries(defaults).map(([k,v]) =>
    [k, typeof v === 'string' && /^(#|oklch|rgba)/.test(v) ? themePaint(theme, v) : v]));
  const sizes: Record<string, Record<string, unknown>> = {
    morning: {wordmarkSize: 116, kickerSize: 44}, outro: {wordmarkSize: 124, taglineSize: 44},
    caption: {fontSize: 36, bottom: 32, color: theme.text}, wbr: {kickerSize: 20},
  };
  return {...mapped, ...sizes[key]} as T;
};
