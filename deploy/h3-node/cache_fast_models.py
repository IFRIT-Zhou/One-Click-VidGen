#!/usr/bin/env python3
"""Cache exactly the four deployed H3-fast weights on native WSL ext4.

Run as .3 WSL root only after smoke is terminal. Defaults to a read-only plan.
--copy requires H3 stopped and pauses for any queued or active TTS work,
stream-hashes the source while copying then independently re-hashes destination.
No source is modified; compose override is prepared but never activated here.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

SOURCE = Path('/mnt/g/trae_voice_over_video/models/comfyui')
DEST = Path('/opt/ocv-h3/models-cache')
ROOT = Path('/opt/ocv-h3')
FILES = {
    'diffusion_models/minimaxH3/minimax_h3_fastvideo_vsa_datafree_1300step_4step_int8_convrot.safetensors': 22898594920,
    'text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors': 15687142551,
    'vae/minimax_h3_video_vae_fp16.safetensors': 5207808496,
    'vae/minimax_h3_audio_vae_fp32.safetensors': 605254808,
}


def sha256(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(8 * 1024 * 1024):
            checkpoint()
            digest.update(chunk)
    return digest.hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w') as stream:
        json.dump(value, stream, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def assert_stopped():
    info = json.loads(subprocess.check_output(['docker', 'inspect', 'ocv-h3-comfyui']))[0]
    if info['State']['Running']:
        raise RuntimeError('H3 must be stopped before model-cache copy')
    for ledger in (ROOT / 'jobs').glob('h3job_*.json'):
        job = json.loads(ledger.read_text())
        if job.get('status') not in {'succeeded', 'failed', 'cancelled'}:
            raise RuntimeError('An unresolved H3 attempt must be reconciled before cache copy')


def await_tts_idle():
    """Read-only checks; TTS may stay running and receives priority immediately."""
    paused = False
    while True:
        assert_stopped()
        try:
            with urllib.request.urlopen('http://10.250.0.1:8000/api/tts/v1/health', timeout=15) as response:
                status = json.load(response)['dispatcher']
            if (status.get('consumer_alive') is True and status.get('redis_ready') is True
                    and status.get('queue_depth') == 0 and status.get('active_jobs') == 0):
                if paused:
                    print(json.dumps({'copy_resumed': True}), flush=True)
                return
        except (OSError, ValueError, KeyError):
            pass
        if not paused:
            print(json.dumps({'copy_paused_for_tts_or_control_unavailable': True}), flush=True)
            paused = True
        time.sleep(10)


LAST_CHECK = 0


def checkpoint():
    global LAST_CHECK
    if time.monotonic() - LAST_CHECK >= 10:
        await_tts_idle()
        LAST_CHECK = time.monotonic()


def capacity():
    needed = sum(FILES.values())
    ext4_free = shutil.disk_usage(ROOT).free
    windows_free = shutil.disk_usage('/mnt/d').free
    reserve = 20 * 1024**3
    if min(ext4_free, windows_free) < needed + reserve:
        raise RuntimeError('Insufficient ext4 or physical D-drive free capacity with 20 GiB reserve')
    return {'needed_bytes': needed, 'needed_gib': round(needed / 1024**3, 2),
            'ext4_free_bytes': ext4_free, 'physical_d_free_bytes': windows_free}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--copy', action='store_true')
    args = parser.parse_args()
    for rel, size in FILES.items():
        path = (SOURCE / rel).resolve()
        if not path.is_relative_to(SOURCE.resolve()) or not path.is_file() or path.stat().st_size != size:
            raise RuntimeError('Unexpected model source/size: ' + rel)
    print(json.dumps({'plan': capacity(), 'source': str(SOURCE), 'destination': str(DEST)}), flush=True)
    if not args.copy:
        return
    with Path('/run/lock/ocv-gpu-phase.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert_stopped()
    # Do not block lifecycle restoration while copying. A marker tells the node
    # start_h3 handler to reject starts; it does not block TTS start/resume.
    marker = ROOT / 'model-cache-copy.active'
    with marker.open('x') as handle:
        handle.write(str(os.getpid()))
    try:
        await_tts_idle()
        DEST.mkdir(parents=True, exist_ok=True, mode=0o750)
        manifest_path = DEST / 'manifest.json'
        manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {
            'source': str(SOURCE), 'created_at': time.time(), 'files': {}}
        for rel, size in FILES.items():
            source, destination = SOURCE / rel, DEST / rel
            saved = manifest['files'].get(rel)
            if destination.exists():
                if not saved or destination.stat().st_size != size or sha256(destination) != saved['sha256']:
                    raise RuntimeError('Existing destination lacks a valid manifest; inspect manually: ' + rel)
                current = source.stat()
                if current.st_mtime_ns != saved['source_mtime_ns'] or current.st_size != size:
                    raise RuntimeError('Source changed after previous cache copy: ' + rel)
                print(json.dumps({'verified_existing': rel}), flush=True)
                continue
            destination.parent.mkdir(parents=True, exist_ok=True, mode=0o750)
            temporary = destination.with_suffix(destination.suffix + '.partial')
            if temporary.exists():
                raise RuntimeError('Partial prior copy exists; inspect before resuming: ' + str(temporary))
            started = time.monotonic()
            original = source.stat()
            digest = hashlib.sha256()
            written = 0
            with source.open('rb') as stream, temporary.open('xb') as output:
                while chunk := stream.read(8 * 1024 * 1024):
                    checkpoint()
                    output.write(chunk)
                    digest.update(chunk)
                    written += len(chunk)
                output.flush()
                os.fsync(output.fileno())
            current = source.stat()
            if written != size or original.st_mtime_ns != current.st_mtime_ns or current.st_size != size:
                raise RuntimeError('Source changed during copy: ' + rel)
            source_hash = digest.hexdigest()
            if sha256(temporary) != source_hash:
                raise RuntimeError('Destination SHA256 verification failed: ' + rel)
            temporary.chmod(0o644)
            temporary.replace(destination)
            elapsed = time.monotonic() - started
            manifest['files'][rel] = {'bytes': written, 'sha256': source_hash,
                                     'source_mtime_ns': original.st_mtime_ns,
                                     'copy_and_verify_seconds': elapsed}
            atomic_json(manifest_path, manifest)
            print(json.dumps({'copied_verified': rel, 'seconds': round(elapsed, 2), 'bytes': written}), flush=True)
        override = ROOT / 'compose.models-cache.yaml'
        expected = 'services:\n  h3:\n    volumes:\n      - /opt/ocv-h3/models-cache:/models:ro\n'
        if override.exists() and override.read_text() != expected:
            raise RuntimeError('Existing cache override differs; inspect manually')
        override.write_text(expected)
        print(json.dumps({'complete': True, 'manifest': str(manifest_path), 'override_prepared': str(override), 'container_recreated': False}), flush=True)
    finally:
        marker.unlink(missing_ok=True)


if __name__ == '__main__':
    main()
