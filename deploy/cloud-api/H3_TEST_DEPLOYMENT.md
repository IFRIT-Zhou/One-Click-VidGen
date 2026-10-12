# H3 free-test API deployment contract

Deploy `cloud_video_api.py` to `/opt/cloud-api/app/`. Runtime dependency: Pillow
(image decode, pixel bounds, metadata stripping); it was absent from the live API
venv during inspection. Existing FastAPI, python-multipart, Pydantic 2, SQLAlchemy
2 are reused. No billing imports/calls or changes to authentication.

In `app/main.py` add:

```python
from .cloud_video_api import router as cloud_video_router
```

Run the schema creation ONCE during deployment with the production environment
loaded (not concurrently in the two Uvicorn worker lifespans):

```python
from app.cloud_video_api import create_schema
from app.db import engine
create_schema(engine)
```

And include `app.include_router(cloud_video_router)`. This creates only the three
new tables, not the whole existing schema. Don't enable routes until tables exist.

Environment:

* `CLOUD_VIDEO_ENABLED=1` explicitly enables submissions.
* `CLOUD_VIDEO_TEST_USER_IDS=` optionally comma-separated existing user IDs.
  Empty means every authenticated active account can test for free.
* `CLOUD_VIDEO_STORAGE_DIR=/var/lib/cloud-api/h3`, private writable directory.
* `CLOUD_VIDEO_MAX_PENDING_PER_USER=6`, `CLOUD_VIDEO_MAX_ASSETS_PER_USER=500`.

No static alias may expose this storage. Authenticated result route returns MP4.
Assets expire in seven days; quotes expire in 15 minutes. Existing asset quota is
conservative and counts retained rows; future cleanup must preserve references
from nonterminal jobs. No background deletion is currently installed.

## Worker integration

Import `CloudVideoJob`, `CloudVideoAsset`, `storage_path`, `utcnow`. DB sessions
come from app.db.SessionLocal. Claim queued jobs under FOR UPDATE SKIP LOCKED,
set status running, attempt_token, lease_owner, lease_expires_at, heartbeat_at,
started_at in a committed transaction. Do not hold database locks throughout
GPU inference. Persist remote_prompt_id as soon as ComfyUI accepts the job.

Lease/attempt fencing must protect every worker update, including success/failure
and cancellation. Recovery must not automatically rerun jobs whose execution
outcome is unknown. Serialize whole-GPU ownership across TTS and H3 on node .3.

Cancellation changes queued directly to cancelled, running to cancelling. Worker
must act on cancelling, stop its owned GPU work/container, then mark cancelled;
must not publish success after a cancellation request won the database race.

Normalized spec contains prompt, reference_image_asset_id, width, height,
duration_seconds, seed, model='minimax-h3', workflow_revision='h3-fast-linux-v1',
fps=24, num_frames (ceil(seconds*24), rounded upwards to 17n+5),
actual_duration_seconds. Asset storage_key yields the validated RGB PNG via
storage_path(). No user-provided graph, remote URL, or audio is accepted.

Write complete, validated output atomically to `results/{job.id}.mp4`; set
result_path to that relative key (not an absolute filesystem path), status
succeeded, progress=1, finished_at. Record measured stage timings in timings
JSON (container_start_seconds, model_load_seconds, inference_seconds etc).
Server-side error diagnostics must be sanitized before writing error_message,
because API returns it to the user. No model storage or host paths in messages.

Result/status reads and cancellation remain available if new submissions are
disabled. client_job_id is optional; retries should use an application-generated
stable ID. A quote can produce only one job, including when client ID is omitted.
Changed quote with reused ID is 409, never an implicit regeneration.

## Verification

`test_cloud_video_api.py`: eight isolated real SQLAlchemy/FastAPI tests covering
auth/allowlist, frozen free quote, frame alignment, idempotency/conflict, tenant
isolation, cancellation states, quote expiration, image/spec validation and
authenticated result retrieval. Local test-only dependencies under test-deps
must not be copied to production.

## Scheduler deployment map

* cloud_video_api.py, cloud_video_worker.py, cloud_gpu_scheduler.py ->
  `/opt/cloud-api/app/`.
* `gpu_scheduler.py`, `tts_redis_status.py`, `node3_client.py` ->
  `/opt/cloud-api/` (daemon helper imports).
* ocv-gpu-scheduler.service -> `/etc/systemd/system/`.
* add `/api/v1/cloud/video/assets: 21 * 1024 * 1024` to the existing
  `app/upload_boundary.py` UPLOAD_LIMITS mapping before multipart parsing.
* API service drop-in loads `/etc/cloud-api-h3.env`, shared with scheduler.

Create `/var/lib/cloud-api/h3/{assets,results}` and
`/var/lib/cloud-api/gpu-scheduler` owned root:cloudapi mode2770 (or cloudapi:cloudapi
mode2770) so both API and scheduler can use them. The scheduler intentionally runs
root with Group=cloudapi to read existing private head-to-node SSH/lifecycle
credentials; no additional credential is returned to public API. New artifact
files are0640. Node3 operations retain their own lock. Do not run the old manual
phase switcher concurrently after this daemon becomes lifecycle owner.

The daemon holds `/run/ocv-gpu-scheduler/owner.lock` for its complete lifetime.
It starts in recovering/drain mode, recovers persisted H3 attempts before new TTS,
waits up to600s for TTS model and audio finalizer health before scheduler resume,
checks both container states after stop, and retains drained admission when
remote/Redis/DB state is unknown. TTS quantum120s controls admission fairness;
it does not abort already admitted TTS jobs. An empty pair of queues stops both
containers. During consecutive jobs of the same type the container stays warm.

Measured phase startup events append to gpu-scheduler/events.jsonl; the journal
also reports them. H3 execution_seconds includes model loading and first inference
unless node supplies finer timings; do not present this as model-load-only time.

Additional local verification: test_cloud_video_worker.py (6 worker scenarios
plus8 inherited API tests), test_cloud_gpu_scheduler.py (4 phase tests), and
tests/h3_isolated/test_gpu_scheduler.py (scheduler ownership/fairness tests).

On-demand admission requires GPU_SCHEDULER_ENABLED=1 in the shared API/scheduler environment. Default is disabled. /health.tts_admission only permits sleeping-model admission with a fresh healthy scheduler heartbeat; all four flags must be true.

Repository isolated tests: python -m unittest discover -s tests/h3_isolated -v (install project dependencies first). No real GPU, users or billing are touched.
