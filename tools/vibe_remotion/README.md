# OCV semantic motion and Shotcraft renderer

An isolated Remotion renderer for OCV Vibe previews. This is not an
arbitrary JavaScript execution endpoint. `render.mjs` uses bundled React components;
`render-code.mjs` accepts bounded model-authored JSX through a restricted AST and
trusted interpreter, with local asset bindings and layout/motion inspection.
The model supplies semantic stages and known asset IDs; the OCV backend resolves
saved subtitle anchors and validated local image paths before invoking this tool.

Install the pinned dependencies in this directory with `npm ci`. Invoke an
existing local headless Chromium explicitly; rendering never downloads a browser
or contacts a model/image API.

## Windows 更新说明

Launcher 更新分发程序源码和 12 款示意预览，不携带 `node_modules`、浏览器、
RMBG/OCR 权重或用户素材。新整合包可预置这些运行依赖；旧整合包首次使用云端
Vibe 时，如提示渲染组件未就绪，请先安装依赖，未就绪时不会调用付费模型。
在 OCV 根目录的 PowerShell 中执行：

```powershell
$env:PATH = "$PWD\runtime\node;" + $env:PATH
Push-Location tools/vibe_remotion
npm.cmd ci
Pop-Location
```

还需本地 Chromium（可复用整合包内 Hyperframes 的浏览器）。RMBG 依赖通过
Launcher 的“RMBG 抠图组件”检查或安装；OCR 通过
`runtime/python/python.exe tools/vibe_ocr/setup_runtime.py --check` 检查，
用户确认后可执行同脚本 `--install`。推理和重试不会自动下载这些组件。

```
node render.mjs --input /absolute/job/input.json --output /absolute/job/render --browser /absolute/chrome-headless-shell --concurrency 2
```

`--stills-only` outputs all phase inspection frames without encoding a movie.
The backend creates the proof sheet and muxes the existing final narration.
The source bundle is cached by source/dependency fingerprint, separately from
each job's assets and output. No project record or source media is changed.

## Trusted rendering paths

The JSON wrapper remains `{plan, assets, duration, fps}`. `plan.version` selects
one of the bundled components; model-generated JavaScript, JSX and CSS are not
accepted or executed.

| Plan version | Component | Renderer metadata |
| --- | --- | --- |
| `ocv-motion-v1` | `src/scene.tsx` (`MotionScene`) | `ocv-remotion-v1` |
| `ocv-shotcraft-v1` | `src/ShotcraftScene.tsx` | `ocv-remotion-shotcraft-v1` |
| `ocv-layered-v1` | `src/LayeredScene.tsx` | `ocv-remotion-layered-v1` |

`--check` retains the existing `ready` and `renderer` fields and additionally
reports supported renderer IDs and plan versions. All paths produce
`stage_frames`, `detail_early.png`, `detail_middle.png`, `detail_end.png` and
`metadata.json`; Shotcraft also produces `item_frames` for every relay item.

### Independent poster layers

`ocv-layered-v1` is a standalone layer adapter, not a Shotcraft card recipe or
an extension to the production project task API. Its wrapper is the same
`{plan, assets, duration, fps}`; `assets` binds IDs to absolute local raster
files, including transparent PNG/WebP files. Use the same `render.mjs` command.
Each background, subject and decorative patch can be a separate image asset;
text remains native, editable typography. Array order is back-to-front order.

The plan contains `version`, `canvas`, a six-digit `background` base colour,
`fade_in: 0`, `fade_out: 0`, and `elements`. Each element has its own `id`,
`type`, `box: [x,y,width,height]`, absolute `at`/`end` seconds, `enter`/`exit`
seconds and finite `motion`. Supported types are `image`, `text`, `rect`,
`ellipse`, `line` and `polyline`. Common optional fields are `role`
(`background`/`content`), `rotation` (-15 to 15 degrees), `opacity` (0 to 1)
and `shadow` (`none`/`soft`). Motions are `none`, `fade`, `slide_left`,
`slide_right`, `slide_up`, `scale`, `grow`, and line-only `draw`.

Images require `asset_id` and accept `fit: contain|cover`. Text requires `text`
and accepts `font_size`, `bold`, `align`, `fill`, `line_height`,
`letter_spacing`, and `font_family: sans|serif`. Explicit newlines are retained;
other text wraps normally within the box. Overflow fails with
`OCV_LAYOUT_OVERFLOW`; text is never silently clipped or shrunk. Geometry,
colours, fields, time ranges and image bindings are validated before rendering.
Arbitrary JavaScript, HTML, CSS and remote image URLs are not accepted.

A persistent background image is required: use `role: background`, a full-canvas
box, `at: 0`, `enter: 0`, `exit: 0`, `motion: none`, `fit: cover`, and omit
`end` to hold for the entire narration. Background layers cannot fade out or
end early. Foreground layers default to `enter: 0.6`, `exit: 0`, and hold
until narration end unless an earlier `end` is supplied. The background is
visible on frame zero and the last frame, preventing a blank-canvas flash.
Supply an opaque background asset; transparent foreground images remain
separate. Layers must occupy at least one video frame, and their settled
rotated bounds must fit the canvas as well as their unrotated boxes.

Layered output includes `layer_frames` (also mirrored as `stage_frames` for
existing inspection tools), first/middle/last detail frames and standard
`silent.mp4` when `--stills-only` is not used. Run
`node tests/layered-render-smoke.mjs [--video|--overflow]` for isolated local
browser checks using synthetic assets; it never reads or updates a project.

The Shotcraft path implements twelve distinct adapted recipes. Shared runtime
limits live in `lib/shotcraft-recipes.json` (used by Python and JavaScript):

- `spotlight-hero-card`: a single hero, side-on camera push, rise/hover/reseat and
  two moving rim-light passes; minimum stage duration 4 seconds.
- `word-relay-filmstrip`: a stepped evidence strip, velocity-based motion blur
  and masked word relay; minimum 1.7 seconds per item.
- `outro-group-photo-launch`: multidirectional arrivals, overshoot landings,
  crane motion and a letter-by-letter sign-off; 2.5 to 6 seconds.
- `card-flip-reveal`: anticipation, 3D front/back flip, overshoot and settle.
- `carousel-3d`: stepped perspective ring with upright double-sided cards.
- `card-stack`: staggered spring stacking, coordinated fan-out, center lift.
- `timeline-travel`: horizontal camera travel, braking, ordered stops.
- `document-typewriter-reveal`: angled document pullback, row wipes and caret.
- `marker-underline-title`: headline landing followed by irregular marker stroke.
- `quad-split-parallel-scenes`: four independently staggered panel entrances.
- `scanline-annotate-focus`: original-image scan, delayed whole-image brackets.
- `title-demote-to-label`: continuous headline-to-label flight, content reveal.

Image requirements, short-card text limits, item counts and reading durations
are validated before rendering. Sequential items and both flip states receive
review frames. Scanning does not invent diagnostic targets; timelines require
real ordering. Neighbor preview history is an advisory selection input, not a
hard diversity gate or part of the shot's content-validity hash.

### Selection capability labels

`lib/shotcraft-capabilities.json` is the audited multi-label catalog for these
twelve OCV adapters, not a claim that all archived upstream demos are runnable.
It records content purposes, motion families, image presentation and evidence
readability cautions. `backend/app/vibe_shotcraft_capabilities.py` combines those
labels with the actual Python submission limits and shared recipe rules.

The dedicated model receives this catalog before selection, the same capability
metadata alongside the chosen full source cards, and the evidence cautions at
visual review. Catalog/detail responses expose the metadata for future pickers.
Selection reasons and capability versions are retained in the preview receipt.
Only existing runtime constraints are hard gates; editorial tags are guidance.

Reference images read by the model are not the same as displayed image slots.
Carousel/filmstrip can bind multiple photos but have one primary reading focus
at a time. Numeric comparison means verified-source numbers rendered as text,
not native bar/line/statistical chart construction. Original evidence remains
original pixels; `contain` prevents cropping, not unreadably small rendering.
Evidence support is conditional on actual final-size legibility. Neighbor
diversity considers motion families, not just different template names, and
never overrides factual continuity or explicit user choices.

This is not an implementation of the entire upstream shot catalog. Catalog
entries and available runtime adapters are separate capabilities.

Shotcraft requires a landscape canvas of at least 16:10, 30 fps, a complete
six-color source palette, a supported background material, and `sans` or `serif`
typography. Its plan-level duration/fps must match the wrapper. Supplied images
are displayed with `contain`, without redrawing their data. All visible copy
comes from the plan. Longer shots extend reading holds rather than stretching
the main action, and text that cannot fit is rejected with
`OCV_LAYOUT_OVERFLOW`, never silently truncated.

## Dependencies and attribution

The renderer needs only the pinned React/Remotion runtime and an installed local
Chromium. It does not require GPT, DeepSeek, any model SDK, model API credentials,
the private trial project, or an upstream source checkout. An upstream planning
service may supply JSON, but rendering itself makes no model/image API calls.

The original `MotionScene` components are OCV-authored. The Shotcraft components
contain modified code adapted from
[video-shotcraft](https://github.com/Vincentwei1021/video-shotcraft), including the
twelve demo implementations above and deterministic motion helpers.
Copyright 2026 Wei Yihao. These adaptations are distributed under Apache-2.0;
the full [LICENSE](lib/video-shotcraft/LICENSE) and
[attribution/change notice](lib/video-shotcraft/NOTICE.txt) are bundled in this
runtime. Do not omit `lib/` or `src/lib/` when distributing it. Upstream demo
brands, sample data, audio and image assets are not runtime dependencies.

Remotion and user-supplied media retain their separate licenses. Dependency and
distribution review remains a release prerequisite. Portable packaging excludes
this renderer's `.cache/` (bundles and private test renders) and the Shotcraft
library's `.downloads/`; it retains runtime source, manifests and license files.

Run `npm test` for offline contract tests. `tests/shotcraft-render-smoke.mjs`
provides optional local-browser rendering checks and writes only to `.cache/`.
`tests/shotcraft-expanded-smoke.mjs --asset <local-image> [--dark] [--maximum]
[--video]` exercises all twelve in three isolated batches without API calls.
