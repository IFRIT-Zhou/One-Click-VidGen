# Visual themes and palette editing

Open the **主题 (Themes)** tab in the existing left sidebar. Its upper section selects a preset; its lower section edits background, card surface, text, secondary text, accent, subtle fill, and border colors using a color picker or a six-digit HEX value. Press Enter or leave the HEX field to apply it. Invalid input is discarded.

Presets: Ink Press, Modern Light, Midnight, Sage, Coral, Iris, Deep Ocean, Obsidian Violet, and Vintage Kraft. Sage, Coral, and Iris replace the earlier bright experiments with pale surfaces and restrained accents. Their existing IDs (`solar-pop`, `coral-burst`, `color-play`) remain compatible with saved projects. Coral's neutral surface / dark violet text / coral accent direction draws on [Happy Hues](https://www.happyhues.co/); values are adapted for this fixture, not an exact copy of a complete site.

- `themeId` and optional `themeColors` are stored in project JSON and browser saves. Changes support undo/redo. A continuous color-picker gesture creates one undo step and keeps the current clip selected.
- Selecting another preset clears palette overrides; **恢复预设** clears them for the current preset. Explicit per-clip edits remain intact and take precedence.
- Ink Press preserves its original raster screenshots and has no palette editor. Select another preset to recolor both the film and its demo UI assets.
- Editable presets preserve the weekly heading in the report-header band and use compact 36px captions near the bottom. Explicit per-clip text, size, and position edits still take precedence.
- Themes apply to the adapted template scenes. Ordinary Gallery cards and the editor's own chrome are not globally recolored.
- Old projects without a theme marker are normalized once; explicit edits are never inferred or discarded after migration.

## Single palette source

`src/themes/palettes.json` defines the seven semantic colors per editable preset. Both the scene adapter and `themes/ui.cjs` read it. Add the palette, its type ID, and a manifest label to add a preset using the same fixture. No copied scene tree is needed.

`src/themes/palette-assets.json` contains five trusted, serialized demo pages and 27 crop definitions. Runtime SVG assets embed this markup using `foreignObject` and validated HEX variables. The same renderer is used in preview and video export, with a bounded cache. Editing colors does not invoke a capture service or recolor pixels with a filter. Third-party screenshots still require a separately adapted source.

After changing fixture markup or geometry, run `node scripts/build-palette-assets.cjs` from `template/`. It requires Playwright in the development environment. `SHOTCRAFT_PLAYWRIGHT` and `SHOTCRAFT_BROWSER` can select an existing module and Chromium executable. The compiled JSON is committed, so end users do not need these tools.

The optional manifest additions are `themes[].palette` and `paletteProp`, alongside `themes`, `defaultTheme`, `themeProp`, and each unit's `themeKey`. Props resolve schema defaults → preset defaults → project palette → explicit clip edits. Manifests without palette support keep their existing behavior.

## Direct render

The existing `AiflPromo` composition accepts `theme` and optional `colors`:

```json
{"theme":"coral-burst","colors":{"accent":"#2459ad","surface":"#e1ecff"}}
```

Save this as a props JSON and pass it to `npm run render -- --props=<file>`. Workbench exports instead carry `project.themeId` and `project.themeColors`. Missing or unknown preset IDs fall back to Ink Press.

## Verification and provenance

After installing dependencies in both `workbench/` and `template/`, run `npm run test:themes` and `npx tsc --noEmit` from `workbench/`, and `npx tsc --noEmit` from `template/`. Tests cover custom palette validation and precedence, reset, edit preservation, legacy saves, unthemed projects, palette history, preserved weekly copy, caption defaults, and separate light/accent/shadow roles. Browser/export validation results are recorded in PR #80.

The fixture describes a fictional research workspace, not a connected service or real research results. Original assets retain their existing attribution/license terms; new fixture code and derived assets use this repository's Apache-2.0 license.

## Save, import and export palettes

Name a palette and click **保存配色** to add it to **我的主题**. These entries live in this browser's local storage, scoped to the current manifest name (up to 50 entries); they are separate from the project timeline. Click an entry to apply it, or its × button to remove it. Export a JSON file to back it up or move it to another browser.

**导入配色** accepts version-1 `shotcraft-palette` JSON exported by this panel, validates the compatible base theme and complete HEX fields, and applies it without changing timeline edits. Click Save afterward to add the imported palette to My Themes. Arbitrary color-site JSON schemas are not yet supported. **导出配色** downloads the current palette, name and base theme ID. Vintage Kraft (`vintage-kraft`) preserves its procedural paper texture when its palette is saved or imported.
