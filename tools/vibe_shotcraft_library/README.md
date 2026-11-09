# video-shotcraft offline library

Upstream: https://github.com/Vincentwei1021/video-shotcraft

This optional archive preserves the upstream categories, recipes, demo implementations,
shared assets, original LICENSE and Gallery previews. `manifest.json` pins the source
commit and records SHA-256 hashes. `categories/` contains one index per original category.
`source/` retains one complete tracked source tree; `previews/` groups official Gallery
previews by category without duplicating the source tree.

Install or refresh from a reviewed local checkout:

```powershell
python tools/sync_video_shotcraft.py --source-dir workspace/vibe_lab/vendor/video-shotcraft --download-previews
```

The downloader only uses the upstream `gallery-media` release. Its rule matches the
author's assembler: bulk ZIP first, then newer single MP4 assets and missing MP4 assets.
Downloads are verified by GitHub asset size and SHA-256 digest when supplied, and all
installed files receive a local SHA-256 inventory. The mutable release is recorded
separately from the pinned source commit. No install scripts or upstream code are run.

Run the same command without `--download-previews` for an offline source/index rebuild.
Missing previews remain explicitly incomplete; they are not fabricated or silently ignored.

Source availability, preview availability, runtime support and successful rendering are
different states. The archive does not assert that all 150 styles are integrated or rendered.
`runtime_supported.json` is a separate allowlist of OCV adapters, not an upstream guarantee.

The Launcher distribution includes planning source and indexes, but omits upstream
sample audio/video and the large optional Gallery download. OCV's twelve adapted
examples are independently bundled in `tools/vibe_remotion/gallery`.

The upstream Apache-2.0 LICENSE is preserved unchanged. Remotion and any other dependencies
retain their own licenses. Keep this notice, the upstream LICENSE and all attribution files
when distributing the optional archive.
