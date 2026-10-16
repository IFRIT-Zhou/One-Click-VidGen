# .3 restart ownership

The dedicated policy assigns H3 to .3 while .20 TTS is available. When .20 TTS
is unavailable, the head scheduler rotates .3 H3/TTS for queued work and releases
the idle model; neither model reserves .3 memory indefinitely in this mode.
Only the head scheduler may start .3 TTS as a fallback. A Docker restart policy alone is insufficient:
the previously captured `worker-agent` creates containers with
`restart=unless-stopped`, and both Windows RayWorkerClient autostart and the BAT
`switch-agent join` path can invoke `worker-agent join/rejoin`.

## Local guard

`install_scheduler_role.py` is an opt-in patch for the installed .3 Agent. Its
default command validates without changing files. `--apply` requires root,
the existing Agent lock and GPU phase lock, and the exact .3 node/container
configuration. It rejects unfamiliar source rather than replacing an Agent
with an old copied version. It preserves a source backup and installs:

- A root-owned `/etc/ray-worker/gpu-scheduler-role.json` ownership marker.
- An early `SCHEDULER_OWNS_GPU` refusal for Agent join/rejoin and direct
  `start_managed`, before network repair, control-plane calls or container start.
- Agent container validation accepting `restart=no` only under this role.
- Agent `pause` can record `PAUSED` when a scheduler-owned TTS container is
  already stopped, without requesting a drain from the now-unassigned .3
  lifecycle endpoint. Running containers still require the existing drain.
- Docker `restart=no`, retained by node3_agent during fallback start/stop and
  H3 transitions. Normal status and pause continue through the existing Agent.

No .20 credentials are copied to .3 and no lifecycle authorization is expanded.
Stopping an already running .3 TTS container still requires its existing drain
confirmation. A successful role installation does **not** authorize stopping a
container that may still own admitted work. It does not start or stop containers.

The head restores H3 after .3 becomes reachable and .20 TTS is available. Docker and the desktop client
cannot independently start fallback TTS through the guarded Agent while .20 is
the primary. If .20 TTS is unavailable and work is queued, the scheduler can still use
the existing node3_agent fallback path, which intentionally bypasses Agent join.

## Deployment and remaining checks

On 2026-10-15, a live read-only preflight matched the installed Agent (SHA-256
`3d3248ae7ed5ee37f78078b761d90425935810a0e614d4f77b8de0484dc48697`).
The heartbeat timer was present and recent reports succeeded over WireGuard;
the `FAILED` report came from a persisted `DISPATCH_CONTROL_UNAVAILABLE` join
error, not a missing heartbeat. Windows `RayWorkerMainAutoStart` was running
`D:\\wsl\\v34-release\\RayWorkerClient.exe`; `RayWorkerV31KeepaliveTest` was
also enabled (`Ready`). The other RayWorker keepalive tasks were disabled.
The installer changes neither these tasks nor network configuration. Their
Agent join/rejoin path is guarded. A script doing `docker start` directly, or
an executable replacing the Agent, bypasses this guard and needs separate
adaptation. A live reboot/reconnect still needs explicit verification.

On .3, with the updated node3_agent already present:

```sh
python3 /opt/ocv-h3/install_scheduler_role.py
# Review current startup chain and dry-run validation before applying.
python3 /opt/ocv-h3/install_scheduler_role.py --apply
```

Verify `worker-agent status` remains valid, join/rejoin return
`SCHEDULER_OWNS_GPU` without starting TTS, and the container restart policy is
`no`. Then verify one idle reboot/reconnect with the head scheduler alive. Do
not claim restart persistence validated until these live checks pass.

If .3 auto-started the old Ray container while the dispatch target is already
.20, the old .3 lifecycle credential cannot drain .20. Do not copy .20's token
or enlarge .3 permissions. The head operator must hold the placement lock,
verify the persisted and actual pin both target .20, verify no transition is
pending and no live/creating Ray actors are assigned to .3, then gracefully
stop only the identified .3 TTS container. After this stop, use normal Agent
`pause` to persist a true `PAUSED` state and send the heartbeat. H3 job records
must remain intact. No synthetic `JOINED` state should be written to hide the
prior failure; the heartbeat itself requires no transport repair.

An Agent software update can replace patched source. Re-run the validator and
reapply this guard after an update; do not assume the marker protects an Agent
that no longer checks it. Keep the original source backup for deliberate role
rollback. Removing the marker alone does not restore Docker's restart policy;
rollback must be coordinated with the scheduler and retain drain protection.
