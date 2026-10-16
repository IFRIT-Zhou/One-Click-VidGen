# Dedicated H3 / TTS deployment

Policy: `.3` hosts H3, `.20` hosts two resident IndexTTS 2.5 replicas. Select
`GPU_SCHEDULER_POLICY=dedicated` for `ocv-gpu-scheduler`. Persisted placement and
lifecycle ownership must agree before dispatch resumes. A confirmed unavailable
`.20` TTS service (three observations) plus pending TTS enables `.3` fallback.
This includes offline Ray, explicitly failed model deployment, or model startup
exceeding 600 seconds. Unknown probes and inference-queued health timeouts do
not prove model failure. Recovery requires two online observations and verified
model readiness; after model failure a persisted 300-second cooldown prevents
repeated migration attempts.
Switches drain admitted work first and never preempt an unresolved H3 job.
Fallback uses exclusive H3/TTS phases, with a 120-second TTS fairness quantum.
When both queues are empty and admitted work is drained, fallback releases both
models. `.3` H3 stays resident only while `.20` TTS is available.

The placement adapter is version-gated to Ray 2.56.0. It preserves control
deployment versions and serialized definitions; only the model workers move.
Do not replace it with `serve.run(app)` during a placement change: that can
restart the dispatcher and lose in-flight work. Durable transition records live
under `/var/lib/cloud-api/tts-placement`; investigate incomplete records rather
than deleting them.

## Verified 2026-10-14 (UTC+08:00)

- `.20` bootstrap fixed using `patch_ray_protobuf.py` and
  `tts-worker-service-requirements.txt`; see `tts-worker-compatibility.md`.
- Both model replicas RUNNING on `.20`, placement and lifecycle pinned to `.20`.
- A 31-character test completed in 21.89 seconds including concurrent workload:
  7.941-second, 24 kHz WAV, 381256 bytes, SHA-256
  `00d77e4794ae128d0e4ffd913ae885d677bb0841e3b58e915639289662303f66`.
- Two customer jobs completed at 10:05 and 10:08; the latter finished 20/20 chunks.
- API and scheduler reloaded; Ray head and control actor IDs unchanged.
- 103 isolated cloud tests passed, covering routing, draining, recovery,
  admission, H3 queue and administrative status. Node suite: 29 tests passed.

## Verified 2026-10-15 (UTC+08:00)

- `.3` became reachable. The scheduler role guard and updated node agent were
  installed at 15:05; backup: `/opt/ops-backups/node3-role-20261015-070557`.
- With placement and lifecycle pinned to `.20` and no live Ray actors on `.3`,
  the unused `.3` TTS container was gracefully stopped at 15:11. Normal Agent
  pause persisted `PAUSED`; WireGuard heartbeat reports remain successful.
- Scheduler reconciliation had been interrupted while the service was stopped.
  The revised scheduler was deployed and started at 16:46 after 115 isolated
  tests passed. At 16:47 `.3` H3 was processing queued work; at 16:50 it was
  ready and idle. `.20` retained two ready TTS replicas and admission recovered.
- The `.3` TTS container uses `restart=no`; Agent join/rejoin/start are guarded
  by scheduler ownership. Model failover and cooldown are covered by tests;
  no deliberate production outage or Windows reboot was performed.
- At 17:04 `.3` became unreachable again while `.20` remained ready. The latest
  observed `.3` WG handshake was 17:02:44; the head-to-enrollment DB tunnel also
  became unavailable. Both queued H3 jobs had completed at 16:47:31 and 16:48:25.
  Do not describe `.3` as stably restored or powered off without new evidence.

## Remaining deployment and operational limits

- The installed role guard protects the inspected Agent startup paths. Direct
  Docker commands and an EXE update replacing the Agent need corresponding
  integration. See `../../h3-node/scheduler-role.md`. A live reboot and controlled
  end-to-end failover/recovery still need verification during a maintenance window.
- Service residency keeps H3 alive between tasks. H3 weights are cached after
  actual loading; merely starting ComfyUI does not preload every workflow model.
- Worker dependency repairs survive stop/start, but original-image container
  recreation needs the patch and requirements reapplied. Incorporate these in
  the next clean image build; do not snapshot containers containing credentials.
- `patch_head_target_reload.py` was applied to the head deploy loop on disk.
  It takes effect on the next normal head process start. The existing loop
  retains its old in-memory import if it reconnects before then; validate and
  reconcile the placement after any reconnect. Do not kill deploy_loop alone:
  the parent shell's EXIT trap stops the entire Ray head.
- Model health shares the single-request inference deployment and has a
  5-second timeout. Real 20–32 second inference can make health temporarily
  false despite steady progress. No failover or restart is triggered by that
  alone. Separating health from the inference request queue remains a service
  improvement; never treat active-job count alone as proof of health.

## Rollback

Keep backups under `/opt/ops-backups/dedicated-gpu-*`. Do not merely change the
policy back while `.20` owns lifecycle control. Drain and finish admitted TTS;
confirm `.3` idle, prepare its TTS worker, then use `tts_placement.py select
--target 10.250.0.3`. Verify actual pin and lifecycle before resuming or restoring
the old scheduler. Preserve transition records and never interrupt customer
work to force a rollback.
