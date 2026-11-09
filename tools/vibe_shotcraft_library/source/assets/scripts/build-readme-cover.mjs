// Rebuild the README's 3×2 animated cover from the current Gallery clips.
// Usage: node assets/scripts/build-readme-cover.mjs [output.webp] [preview.mp4]
// Requires FFmpeg with libwebp_anim; override its executable with FFMPEG.
import { spawnSync } from 'node:child_process';
import { mkdirSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const root = resolve(fileURLToPath(new URL('../../', import.meta.url)));
const output = resolve(process.argv[2] ?? `${root}/assets/readme/cover.webp`);
const preview = process.argv[3] && resolve(process.argv[3]);
// Row-major order. panel-grid-moves uses its comic-panel-split variant;
// space-camera-moves uses its exploded-view variant.
const clips = [
  'graze-face-tour',
  'logo-shrink-wordmark-lockup',
  'comic-panel-split',
  'product-card-progressive-assemble',
  'exploded-view',
  'spotlight-hero-card',
];
const filters = clips.map((_, index) =>
  `[${index}:v]setpts=PTS-STARTPTS,fps=15,scale=400:225:flags=lanczos,` +
  `setsar=1,tpad=stop_mode=clone:stop_duration=6,trim=duration=6[v${index}]`,
);
filters.push(
  clips.map((_, index) => `[v${index}]`).join('') +
  'xstack=inputs=6:layout=0_0|404_0|808_0|0_229|404_229|808_229:fill=black[grid]',
);
// Each clip plays in full at its original speed, then holds its last frame
// until the six-second grid loop restarts. Audio is intentionally omitted.
filters.push(preview ? '[grid]split=2[cover][preview]' : '[grid]null[cover]');
mkdirSync(dirname(output), { recursive: true });
if (preview) mkdirSync(dirname(preview), { recursive: true });
const args = [
  '-hide_banner', '-y', '-filter_complex_threads', '1',
  ...clips.flatMap(clip => ['-i', `${root}/gallery/media/${clip}.mp4`]),
  '-filter_complex', filters.join(';'),
  '-map', '[cover]', '-an', '-c:v', 'libwebp_anim',
  '-quality', '68', '-compression_level', '6', '-loop', '0',
  '-f', 'webp', output,
];
if (preview) args.push(
  '-map', '[preview]', '-an', '-c:v', 'libx264', '-crf', '16',
  '-pix_fmt', 'yuv420p', '-movflags', '+faststart', preview,
);
const result = spawnSync(process.env.FFMPEG ?? 'ffmpeg', args, { stdio: 'inherit' });
if (result.error) throw result.error;
process.exitCode = result.status ?? 1;
