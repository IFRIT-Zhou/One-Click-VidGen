import type { WorkbenchManifest } from './cards/manifest';
import type { CardDef } from './cards/types';
import { defaultsOf } from './cards/types';
import type { ProjectData } from './types';

export const selectedTheme = (m: WorkbenchManifest | null, id?: string) =>
  m?.themes?.find(t => t.id === id) ?? m?.themes?.find(t => t.id === m.defaultTheme) ?? m?.themes?.[0];

/** Same precedence in the inspector, all players, and the exported film. */
export const themedProps = (m: WorkbenchManifest | null, card: CardDef, id?: string, overrides: Record<string, unknown> = {}, colors?: Record<string, string>) => {
  const theme = selectedTheme(m, id);
  const palette = validThemeColors(m, id, colors);
  const aliases: Record<string,string> = {paper:"page",ink:"text",color:"text",muted:"muted",amber:"accent",accent:"accent"};
  const custom = Object.fromEntries(Object.entries(theme?.unitDefaults?.[card.themeKey ?? ""] ?? {}).filter(([k]) => palette[aliases[k]]).map(([k]) => [k,palette[aliases[k]]]));
  return {...defaultsOf(card),
    ...(card.themeKey ? theme?.unitDefaults?.[card.themeKey] : {}), ...(card.themeKey ? custom : {}), ...overrides,
    ...(card.themeKey && m?.themeProp ? {[m.themeProp]: theme?.id} : {}),
    ...(card.themeKey && m?.paletteProp && Object.keys(palette).length ? {[m.paletteProp]: palette} : {}),
  };
};

/** Omit inherited style values on import so future theme changes stay lightweight.
 * Explicit values differing from the default preset remain per-clip overrides. */
export const inheritedProps = (m: WorkbenchManifest, key: string | undefined, props: Record<string, unknown> = {}) => {
  const defaults = key ? selectedTheme(m)?.unitDefaults?.[key] : undefined;
  return Object.fromEntries(Object.entries(props).filter(([k,v]) => !defaults || !(k in defaults) || defaults[k] !== v));
};

/** Upgrade pre-theme JSON once. Never infer inheritance again after this marker
 * is saved: a user can explicitly choose a color equal to another preset. */
export const upgradeLegacyTheme = (project: ProjectData, m: WorkbenchManifest | null, cards: Record<string, CardDef>): ProjectData => {
  if (project.themeId !== undefined || !m?.themes?.length) return project;
  return {...project, themeId: selectedTheme(m)?.id, tracks: project.tracks.map(track => ({...track,
    clips: track.clips.map(clip => ({...clip, props: inheritedProps(m, cards[clip.cardId]?.themeKey, clip.props)})),
  }))};
};

export const switchTheme = (project: ProjectData, m: WorkbenchManifest | null, id: string): ProjectData => {
  if (!m?.themes?.some(t => t.id === id)) return project;
  return {...project, themeId: id, themeColors: undefined};
};
export const themedBackground = (project: ProjectData, m: WorkbenchManifest | null) =>
  validThemeColors(m, project.themeId, project.themeColors).page ?? (project.themeId ? selectedTheme(m, project.themeId)?.background ?? project.background : project.background);

export const validThemeColors = (m: WorkbenchManifest | null, id?: string, colors?: Record<string,string>) => {
 const palette = selectedTheme(m,id)?.palette;
 return Object.fromEntries(Object.entries(colors ?? {}).filter(([k,v]) => palette && Object.prototype.hasOwnProperty.call(palette,k) && typeof v === 'string' && /^#[0-9a-f]{6}$/i.test(v)));
};
