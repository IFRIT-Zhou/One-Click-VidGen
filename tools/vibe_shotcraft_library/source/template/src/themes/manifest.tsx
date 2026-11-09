import { type FC } from 'react';
import { SCENE_OPEN_DEFAULTS } from '../aifl/live/SceneOpen';
import { SCENE_FLYIN_DEFAULTS } from '../aifl/live/SceneFlyIn';
import { SCENE_DETAIL_DEFAULTS } from '../aifl/live/SceneDetail';
import { SCENE_PAPERS_DEFAULTS } from '../aifl/live/ScenePapers';
import { SCENE_WBR_DEFAULTS } from '../aifl/live/SceneWbr';
import { SCENE_OUTRO_DEFAULTS } from '../aifl/live/SceneOutroLive';
import { TITLE_CARD_DEFAULTS } from '../aifl/PaperTitleCard';
import { CAPTION_DEFAULTS } from '../aifl/Caption';
import { VisualThemeProvider, resolveTheme, sceneDefaults } from './visual-theme';

type Props = Record<string, unknown>;
type Unit = {id: string; component: FC<Props>; props?: Props; cardId?: string; schema?: Props[]};
type Manifest = {background: string; shots: Unit[]; transitions: Unit[]; captions: Unit[]};
const scenes: Record<string, Props> = {
  morning: SCENE_OPEN_DEFAULTS, table: SCENE_FLYIN_DEFAULTS,
  macro: SCENE_DETAIL_DEFAULTS, chart: SCENE_PAPERS_DEFAULTS,
  wbr: SCENE_WBR_DEFAULTS, outro: SCENE_OUTRO_DEFAULTS,
  'title-card': TITLE_CARD_DEFAULTS, 'caption': CAPTION_DEFAULTS,
  'flash-cut': {},
};
const styleKey = (key: string) =>
  /Size$/.test(key) || ['ink','amber','muted','paper','accent','color','bottom'].includes(key);

/** Optional presets over the same scene components, with one provider per clip. */
export const withThemes = <T extends Manifest>(paper: T) => {
  const units = [...paper.shots, ...paper.transitions, ...paper.captions];
  const presets = [
    {id: 'ink-press', label: '纸质 · Ink Press'},
    {id: 'modern-light', label: '现代浅色 · Modern Light'},
    {id: 'midnight', label: '暗黑 · Midnight'},
    {id: 'solar-pop', label: '清新鼠尾草 · Sage'},
    {id: 'coral-burst', label: '珊瑚点缀 · Coral'},
    {id: 'color-play', label: '柔和鸢尾 · Iris'},
    {id: 'deep-ocean', label: '深海蓝 · Deep Ocean'},
    {id: 'obsidian-violet', label: '黑曜紫 · Obsidian Violet'},
    {id: 'vintage-kraft', label: '复古牛皮纸 · Vintage Kraft'},
  ].map(({id, label}) => ({
    id, label, palette: id === 'ink-press' ? undefined : Object.fromEntries(['page','surface','field','text','muted','accent','border'].map(k => [k, resolveTheme(id)[k as keyof ReturnType<typeof resolveTheme>]])), background: id === 'ink-press' ? paper.background : resolveTheme(id).page,
    unitDefaults: Object.fromEntries(units.map(u => {
      const key = u.cardId ?? u.id;
      const defaults = id === 'ink-press' ? u.props ?? {} : sceneDefaults(resolveTheme(id), key, scenes[key]);
      return [key, Object.fromEntries(Object.entries(defaults).filter(([k]) => styleKey(k)))];
    })),
  }));
  const bridges = new Map<string, FC<Props>>();
  const wrap = (u: Unit) => {
    const key = u.cardId ?? u.id;
    if (!bridges.has(key)) {
      const Component = u.component;
      const Bridge: FC<Props> = ({__theme, __palette, ...props}) => {
        const colors = typeof __palette === 'object' && __palette !== null ? __palette as Record<string,string> : undefined;
        const theme = resolveTheme(typeof __theme === 'string' ? __theme : undefined, colors);
        return <VisualThemeProvider theme={theme.id} colors={colors}><Component {...props} /></VisualThemeProvider>;
      };
      Bridge.displayName = `Themed-${key}`;
      bridges.set(key, Bridge);
    }
    return {...u, component: bridges.get(key)!, themeKey: key,
      schema: u.schema?.map(f => ({...f,
        label: typeof f.label === 'string' ? f.label.replaceAll('琥珀强调色', '强调色').replaceAll('纸底','背景').replaceAll('墨色','文字颜色') : f.label,
        ...(f.key === 'fontSize' && key === 'caption' ? {max: 96} : {}),
      })),
    };
  };
  return {...paper, themeProp: '__theme', paletteProp: '__palette', defaultTheme: 'ink-press', themes: presets,
    shots: paper.shots.map(wrap), transitions: paper.transitions.map(wrap), captions: paper.captions.map(wrap)};
};
