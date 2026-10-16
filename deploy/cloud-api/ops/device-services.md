# Per-device TTS and H3 status

Deployed 2026-10-15. Device list and detail responses now include independent
`services.tts` and `services.h3` observations. Agent heartbeat and lifecycle
remain separate. `PAUSED` is the TTS Agent state, not the H3 container state.

Each service includes `container_name`, `container_running`, `ray_connected`,
`ready`, `status`, `reason`, `checked_at`, and `source`. Unknown observations use
null values. H3 is ComfyUI, not a Ray worker, so its `ray_connected` is always null.

Sources:

- `.3`: existing scheduler `node-health.json`, valid for 45 seconds.
- `.20`: new independent `ocv-device-telemetry.service` observes Docker every
  30 seconds; `node20-health.json` is valid for 90 seconds. It first checks the
  running WSL list and never launches a stopped RayWorker distribution for a
  status query.
- Ray/model ownership: independent read-only placement snapshots every
  30 seconds. Ready requires matching actual pin and lifecycle target, no
  pending transition, live Ray membership and verified model placement.
- Other devices: no container telemetry has been enrolled yet; their separate
  service fields remain unknown. Never infer a running container from Agent
  heartbeat or copy the primary node's readiness to another device.

The device-list API reads these files and does not perform Docker operations
or wait on SSH. Failed samples replace success with unknown and preserve
acquisition timestamps. Separate collector loops prevent an unreachable `.20`
from blocking placement observations. The telemetry collector remains
read-only and cannot start/stop model containers.

Deploy the two `app/device_*` modules and the matching systemd unit, then enable
`ocv-device-telemetry`. The unit runs as root to reuse the existing SSH identity;
it only returns whitelisted status fields, never environment/credential data.
Cache files have mode 0640 and cloudapi group access. The existing multi-worker
Cloud API can reload with SIGHUP without restarting GPU scheduling or Ray.

The deployed page assets use `20261015.services1` for cache invalidation. They
refresh every 15 seconds while visible, clear old observations on API failure,
and preserve both container columns on narrow screens using table scrolling.

## Manual container controls (prepared 2026-10-16)

The device list and detail page now expose Start/Stop controls only for the
enrolled `.3` H3 and `.20` TTS services when the dedicated scheduler heartbeat
and each node's control-role readiness marker are fresh. The browser submits a
reason with the request; the API records an audit entry and enqueues a durable,
idempotent operation. The single GPU scheduler process executes the fixed
allowlisted action asynchronously, checks drain/placement conditions, then
reconciles observed state. It never accepts a container name or shell command
from the browser. A Stop request is rejected while the service has active or
queued work. A manually stopped service is kept stopped by scheduler intent
until an administrator explicitly starts it again.

This control implementation is prepared in the source tree; it is not a claim
that these controls have been deployed to the production site or installed on
the nodes. Before enabling it in production:

1. Deploy the cloud API modules and scheduler changes, restart the API workers,
   `ocv-gpu-scheduler`, and `ocv-device-telemetry`, and publish the updated
   admin assets.
2. On `.3`, review and run `deploy/h3-node/install_scheduler_role.py` with
   `--apply` to install the H3 control role marker expected by the agent.
3. On `.20`, review `deploy/node20/install_control_role.py`; its default mode
   only validates prerequisites. Apply it only after verifying the RayWorker
   Agent source anchors and arranging a maintenance window. It fails closed if
   the existing Agent source differs. Install `node20_agent.py` at the fixed
   `/opt/ray-worker/bin/ocv-node20-control.py` path with root ownership and
   mode 0750.
4. Verify that both services report `control_ready`, then test start and stop
   on an idle device and verify audit records and scheduler behavior before
   enabling operator use.

The updated local admin assets use `20261016.controls1` for cache invalidation.
Deployment, role installation, and live control verification remain separate
operational steps.

Validation: 21 backend tests, 8 frontend tests, real Chrome desktop and 390px
layout/failed-refresh checks. At 17:02 live backend data showed `.3` heartbeat
online, TTS stopped and H3 ready; `.20` TTS ready and H3 not installed.

Deployment backup: `/opt/ops-backups/device-services-20261015T090205Z`.

Known dependency: the enrollment device inventory still comes through the
`.3` database tunnel. If this tunnel fails, the device endpoint returns 503 and
the UI reports that current device state cannot be obtained. Model health on
`.20` is independent of that administrative database path. Inventory migration
or a clearly timestamped last-known fallback would require a separate change.

At 17:04 during external authenticated API verification this dependency failed:
the response was `Enrollment 数据库暂时不可读`. The head could no longer connect
to `.3` SSH, its local enrollment DB port 25432 refused connections, and `.3`
WireGuard's last handshake was 17:02:44. At 17:07 `.20` remained ready and
accepting TTS. This is a loss of the observed management/network path, not proof
that Windows powered off. The UI deployment itself does not resolve that path.

Recovery: WSL started at 17:12:38 and the scheduler started H3 at 17:12:58.
At 17:15 both authenticated public device endpoints returned 200 with the
expected separate states. Subsequent logs prove WSL began powering off at
17:03:37 while Windows remained up since 14:27:38; the caller of that shutdown
request is not identified. The older Windows keepalive/guard tasks were disabled.
