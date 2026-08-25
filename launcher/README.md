# OCV Launcher

## Phase 1

- Start, stop, and restart the local OCV services.
- Run portable-environment checks.
- Open the workspace, output directory, and runtime logs.

## Phase 2: safe updates

- Git worktrees use `fetch` plus `merge --ff-only`. Dirty, ahead, or diverged worktrees are never overwritten.
- Portable packages read `update-sources.json`, try the mainland update channel first, and immediately fall back to GitHub when the primary source is unavailable.
- A remote channel may publish multiple `archive_urls`; package downloads use the same primary/fallback order and may optionally provide `archive_sha256`.
- Portable overlays protect `.env`, `runtime`, models, `output`, `workspace`, logs, archives, user presets, and unknown user folders.
- Every overwritten portable file is backed up under `Archives/launcher_updates/`; a failed copy restores that backup automatically.
- Updates are not applied while OCV services are running.

## Publishing a portable update

Update `launcher/update-channel.json` whenever a new portable update should become visible:

1. Increase `release_order` monotonically.
2. Change `release_id` and `display_version`.
3. Keep `portable_overlay_safe` set to `true` only when every supported historical portable package can safely apply the source overlay.
4. After a runtime/model-breaking release, keep `portable_overlay_safe` false and set `portable_overlay_min_order` to that complete-package release order. New launchers then permit overlays only when the installed portable baseline is new enough; legacy launchers remain locked instead of accidentally crossing the incompatible boundary.
5. Keep the legacy `archive_url` field for older launchers. New launchers prefer `archive_urls` in order.
6. See `UPDATE_SOURCE_INTEGRATION.md` before enabling a mainland mirror.
