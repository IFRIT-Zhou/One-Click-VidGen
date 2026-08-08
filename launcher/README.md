# OCV Launcher

## Phase 1

- Start, stop, and restart the local OCV services.
- Run portable-environment checks.
- Open the workspace, output directory, and runtime logs.

## Phase 2: safe updates

- Git worktrees use `fetch` plus `merge --ff-only`. Dirty, ahead, or diverged worktrees are never overwritten.
- Portable packages download the public GitHub source archive and run `safe_update_helper.ps1` after the launcher exits.
- Portable overlays protect `.env`, `runtime`, models, `output`, `workspace`, logs, archives, user presets, and unknown user folders.
- Every overwritten portable file is backed up under `Archives/launcher_updates/`; a failed copy restores that backup automatically.
- Updates are not applied while OCV services are running.

## Publishing a portable update

Update `launcher/update-channel.json` whenever a new portable update should become visible:

1. Increase `release_order` monotonically.
2. Change `release_id` and `display_version`.
3. Keep `portable_overlay_safe` set to `true` only for source-only updates compatible with the bundled dependencies and models.
4. Set `portable_overlay_safe` to `false` when Python, Node, FFmpeg, browser, model, or other portable-runtime files must change. The launcher will then require users to download a complete portable package.
