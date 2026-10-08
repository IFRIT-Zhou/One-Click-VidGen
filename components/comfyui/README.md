# OCV managed ComfyUI — integration preview

The managed engine uses an independent, relocatable Python runtime. OCV and
IndexTTS dependencies are not changed. Existing external workflow profiles remain
external; each profile explicitly stores `engine: external | managed`.

The bundled workflow is maintained by OCV and read from the active component,
replacing stale personal-directory copies when a component changes. Normal users
cannot overwrite/delete it or open its editor through OCV. External ComfyUI keeps
the custom workflow tools. Maintainer-only development mode uses
`OCV_COMFYUI_MAINTAINER=1`; it must not be enabled in distributed defaults.

Launcher currently protects all `runtime/` files during ordinary source updates.
Managed-engine updates therefore need a dedicated component channel: verified
versioned payloads, staged installation, health check and active-version switch.
That channel has not been implemented yet; ordinary Launcher updates do not
currently install new ComfyUI binaries/dependencies.

## Layout

- `runtime/comfyui/active.json`: selected component version.
- `runtime/comfyui/releases/<version>/`: `python/`, unmodified `ComfyUI/`,
  `component.json`, sanitized `profile.json`, and Python dependency inventory.
- `runtime/comfyui/models/`: optional bundled weights.
- `workspace/managed_comfyui/`: user data, inputs, outputs, logs and isolated caches.

An additional existing `ComfyUI/models` directory can be selected without copying
or modifying its weights. It is local configuration and must not enter a package.

## Current validation (2026-09-29)

The `h3-20260929-preview1` baseline uses official ComfyUI commit
`830232b856045ca2892833212d7771078a13edd5`, Python 3.13.12 and PyTorch 2.10.0+cu130.
The tracked `h3-fast-v1` manifest lists required models and node sources.
Four model files total approximately 41.35 GiB, separate from the engine runtime.

Validated locally on RTX 4090 / 64 GB RAM:

- Independent interpreter and CUDA import; startup and all 22 required node classes.
- Two-second request, four-step sampling, H.264/AAC encoding, OCV download;
  actual frame-rounded duration 2.333333 seconds at 640×384.
- No modifications to the original ComfyUI, user workflow presets or OCV/TTS environment.
- Windows UTF-8 logs and component-specific Triton/Inductor/Numba/CUDA cache paths.
- Queue/OCV-task-aware shutdown, parent-process watchdog, explicit failure if the
  owned process dies while OCV is polling.

## Build and validate

`tools/build_comfyui_component.py --help` builds a new version from a tested local
installation and an explicitly selected saved API workflow. It snapshots only the
graph's necessary custom-node packages, strips personal prompt/reference values,
and never overwrites a component version. It does not copy weights.

`tools/validate_comfyui_component.py` checks startup/nodes without generation.
An explicit `--reference <image>` additionally performs one local GPU smoke test.
The validation script closes only the engine it started.

## Not yet a public distribution

This is a local integration preview, not a hardware compatibility promise.
`redistribution_audited` remains false. Before a public full package:

1. Rebuild registry-installed nodes from verified upstream sources; audit all
   node/model/Python-wheel licenses and exclude local plugin settings.
2. Trim the baseline dependency snapshot and test on a clean Windows machine.
3. Validate long clips, portrait output, repeated queues, VRAM/RAM pressure and
   coexistence with local TTS; establish supported GPU/driver profiles.
4. Add verified component download/install, staged update and rollback; publish
   separate engine and optional weight packs. Do not blindly update with `git pull`.

The workbench deliberately does not advertise an unavailable download. A base OCV
installation without this runtime retains external ComfyUI and API generation.
