import { AiflMain } from '../aifl/Main';
import { resolveTheme, VisualThemeProvider } from './visual-theme';

export const ThemedFilm: React.FC<{theme?: string; colors?: Record<string,string>}> = ({theme, colors}) => {
  const selected = resolveTheme(theme);
  return <VisualThemeProvider theme={selected.id} colors={colors}><AiflMain /></VisualThemeProvider>;
};
