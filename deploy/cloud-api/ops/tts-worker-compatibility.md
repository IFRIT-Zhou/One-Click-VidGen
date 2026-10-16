# TTS worker compatibility repair

EXE integration handoff (Chinese, including real audio acceptance and pending
work): [worker20-exe-handoff-20261014.md](worker20-exe-handoff-20261014.md).

The `.20` worker using `pytorch-env:2.11-tts25` had two independent bootstrap
failures on 2026-10-14:

1. Ray 2.56.0's Serve configuration conversion uses `FieldDescriptor.label`,
   which is absent from the installed protobuf 7.35.1 upb implementation.
2. The image lacks `minio` and `redis`, imported by the shipped service module.

The compatibility script changes only the repeated-field check to prefer
`is_repeated`, with a fallback for older descriptors. It backs up `config.py`,
refuses unexpected source, rolls back on failed validation, and runs real Serve
configuration round trips plus scalar/repeated/nested protobuf checks. Running
it twice is safe. It does not change torch, model packages, network settings, or
Ray versions.

Run these commands from this directory on the worker host before starting new
Ray model actors (substitute the container name only if necessary):

```sh
docker exec -i ray-worker-managed python -m pip install --disable-pip-version-check -r /dev/stdin < tts-worker-service-requirements.txt
docker exec -i ray-worker-managed python - < patch_ray_protobuf.py
```

Both package installs and the source patch persist in the existing container's
writable layer across stop/start. **Recreating the container from the original
image requires applying both steps again.** These instructions do not alter the
container creation policy or take an image snapshot. Production image building
should install the requirements and run the patch using that image's Python.

On `.20`, an additional repair copy is stored on the host under
`/opt/ray-worker/bin/patch_ray_protobuf.py`. The original source and first repaired
source are backed up under `/opt/ops-backups/repair20-protobuf7-20261014`; later
patch backups also remain next to Ray's `config.py` in the container.

Validation performed on `.20`: repeated execution reports `changed=False`,
all serialization checks pass, and the actual shipped `service.config`,
`service.audio`, `service.schemas`, `service.state`, `service.drain`,
`service.storage`, and `service.deployments` modules import successfully.
These checks establish actor bootstrap compatibility; successful model loading
and audio generation must still be verified by the deployment workflow.
