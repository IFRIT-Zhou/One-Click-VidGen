import { useVisualTheme, themePaint } from '../themes/visual-theme';
import { AbsoluteFill, interpolate, useCurrentFrame } from 'remotion';

/** Bright-field cut: a warm-white bloom that flashes over the hard cut. */
export const FlashCut: React.FC<{ duration?: number }> = ({ duration = 10 }) => {
  const theme = useVisualTheme();
  const paperStyle = theme.id === 'ink-press';
  const paint = (css: string) => themePaint(theme, css);

  const frame = useCurrentFrame();
  const o = interpolate(frame, [0, duration * 0.4, duration], [0, paperStyle ? 0.85 : 0.24, 0], {
    extrapolateLeft: 'clamp',
    extrapolateRight: 'clamp',
  });
  return (
    <AbsoluteFill
      style={{
        pointerEvents: 'none',
        opacity: o,
        background: paint('radial-gradient(ellipse at 50% 45%, rgba(255,248,235,0.98), rgba(255,244,224,0.55) 55%, transparent 80%)'),
      }}
    />
  );
};
