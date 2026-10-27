"""Independent, cancellable TTS edit attempts. Never cancel the parent job."""
from __future__ import annotations

import copy
import os
import subprocess
import threading
import time
import uuid


class TtsEditCancelled(RuntimeError):
    pass


class TtsEditTasks:
    def __init__(self):
        self._tasks = {}
        self._lock = threading.RLock()
        self._edit_events = {}
        self._edit_processes = {}

    def _start_edit(self, job_id, *, revision=0, total=0, message='准备配音编辑'):
        with self._lock:
            if self._tasks.get(job_id, {}).get('status') == 'running':
                raise RuntimeError('该项目已有配音编辑任务正在运行，请先等待或停止本次重配')
            self._edit_events[job_id] = threading.Event()
            self._tasks[job_id] = dict(
                task_id=uuid.uuid4().hex, status='running', phase='preparing', progress=0,
                message=message, adopted_version=revision, pending_version=revision + 1,
                completed_segments=0, total_segments=total, cancel_requested=False,
                cancellation_complete=False, submitted_to_upstream=False,
                upstream_cancel_confirmed=False, updated_at=time.time())
            return self.status(job_id)

    def _set_task(self, job_id, **values):
        with self._lock:
            current = dict(self._tasks.get(job_id) or {})
            current.update(values)
            if current.get('cancel_requested') and current.get('status') == 'running':
                current['message'] = '正在停止本次配音编辑，等待当前工作安全退出；正式配音未变'
            if values.get('status') in {'completed', 'failed', 'cancelled'}:
                current['phase'] = values['status']
                current['local_cancellation_complete'] = values['status'] == 'cancelled'
                current['cancellation_complete'] = (values['status'] == 'cancelled' and
                    (not current.get('upstream_task_id') or current.get('upstream_terminal', False)))
                current['upstream_may_still_run'] = bool(values['status'] == 'cancelled' and
                    current.get('upstream_task_id') and not current.get('upstream_terminal'))
                if values['status'] == 'completed' and current.get('pending_version') is not None:
                    current['adopted_version'] = current['pending_version']
                current['pending_version'] = None
            current['updated_at'] = time.time()
            self._tasks[job_id] = current

    def status(self, job_id):
        with self._lock:
            return copy.deepcopy(self._tasks.get(job_id) or {'status': 'idle', 'message': ''})

    def _edit_cancelled(self, job_id):
        with self._lock:
            event = self._edit_events.get(job_id)
            return bool(event and event.is_set())

    def _check_edit_cancelled(self, job_id):
        if self._edit_cancelled(job_id):
            raise TtsEditCancelled('本次配音编辑已停止，原配音、字幕和时间轴保留')

    def _begin_edit_commit(self, job_id):
        # Cancellation and the publish boundary use the same lock. Once publish
        # starts it must finish/roll back, never report a successful cancellation.
        with self._lock:
            self._check_edit_cancelled(job_id)
            self._set_task(job_id, phase='committing')

    def cancel(self, job_id, task_id):
        with self._lock:
            task = self._tasks.get(job_id) or {}
            if not task_id or task.get('task_id') != task_id:
                raise ValueError('配音编辑任务已变化，请刷新后停止对应任务')
            if task.get('status') != 'running':
                return dict(accepted=False, task=self.status(job_id))
            if task.get('phase') == 'committing':
                return dict(accepted=False, reason='正在保存已生成配音，请等待保存完成；之后可撤回', task=self.status(job_id))
            self._edit_events[job_id].set()
            self._set_task(job_id, cancel_requested=True, phase='cancelling')
            process = self._edit_processes.get(job_id)
        if process is not None:
            from .pipeline import _request_graceful_tts_stop
            _request_graceful_tts_stop(process)
        return dict(accepted=True, task=self.status(job_id))

    def _edit_failed(self, job_id, exc, message, *, rollback_failed=False):
        cancelled = self._edit_cancelled(job_id) and not rollback_failed
        self._set_task(job_id, status='cancelled' if cancelled else 'failed', progress=0,
                       message=(str(TtsEditCancelled('本次配音编辑已停止，原配音、字幕和时间轴保留'))
                                if cancelled else message))

    def _remote_edit(self, job_id, remote_id, payload):
        state = str(payload.get('status') or '').strip().lower()
        progress = payload.get('progress')
        values = dict(submitted_to_upstream=True, upstream_task_id=remote_id, upstream_status=state,
                      phase='cloud_' + (state or 'submitted'),
                      upstream_cancel_confirmed=state == 'cancelled',
                      upstream_terminal=state in {'cancelled', 'completed', 'failed', 'expired'})
        for source, target in (('completed_chunks', 'completed_segments'), ('total_chunks', 'total_segments')):
            if source in payload:
                values[target] = payload[source]
        if isinstance(progress, dict):
            for source, target in (('completed_chunks', 'completed_segments'), ('total_chunks', 'total_segments')):
                if source in progress:
                    values[target] = progress[source]
        chunks = (payload.get('result') or {}).get('chunks') if isinstance(payload.get('result'), dict) else None
        if isinstance(chunks, list):
            values['completed_segments'] = max(int(values.get('completed_segments') or 0), len(chunks))
        self._set_task(job_id, **values)

    def _run_edit_process(self, job, command, prefix):
        from .pipeline import PROJECT_ROOT, TTS_SENTENCE_PROGRESS_RE, store, _request_graceful_tts_stop
        self._check_edit_cancelled(job.id)
        process = subprocess.Popen(command, cwd=str(PROJECT_ROOT), env=os.environ.copy(),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding='utf-8', errors='replace', bufsize=1,
            creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW) if os.name == 'nt' else 0)
        with self._lock:
            self._edit_processes[job.id] = process
            self._set_task(job.id, submitted_to_upstream=True, phase='synthesizing')
            cancelled = self._edit_cancelled(job.id)
        if cancelled:
            _request_graceful_tts_stop(process)
        try:
            assert process.stdout is not None
            for line in process.stdout:
                cleaned = line.strip()
                if not cleaned:
                    continue
                store.log(job, f'[{prefix}] {cleaned}')
                match = TTS_SENTENCE_PROGRESS_RE.search(cleaned)
                if match:
                    done, total = int(match['completed']), max(1, int(match['total']))
                    self._set_task(job.id, completed_segments=done, total_segments=total,
                        progress=min(80, round(done / total * 80)),
                        message=f'候选配音生成中：{done}/{total}；正式配音未变')
            code = process.wait()
            self._check_edit_cancelled(job.id)
            if code:
                raise RuntimeError(f'TTS 子任务退出码 {code}')
        finally:
            if process.stdout:
                process.stdout.close()
            with self._lock:
                self._edit_processes.pop(job.id, None)
