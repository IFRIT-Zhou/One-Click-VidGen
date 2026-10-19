#!/usr/bin/env python3
"""Fixed H3 execution adapter, invoked over the existing administrative SSH path."""
import base64
from contextlib import contextmanager
import fcntl
import hashlib
import json
import pathlib
import os
import math
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

ROOT = pathlib.Path('/opt/ocv-h3')
URL = 'http://127.0.0.1:18188'
TTS = 'ray-worker-managed'
H3 = 'ocv-h3-comfyui'
SCHEDULER_ROLE = pathlib.Path('/etc/ray-worker/gpu-scheduler-role.json')


def scheduler_owns_gpu():
    if not SCHEDULER_ROLE.exists():
        return False
    try:
        role = json.loads(SCHEDULER_ROLE.read_text())
    except (OSError, ValueError, TypeError):
        raise RuntimeError('SCHEDULER_ROLE_INVALID') from None
    if role != {'schema': 1, 'owner': 'ocv-gpu-scheduler', 'node_ip': '10.250.0.3',
                'primary_role': 'h3', 'tts_container': TTS}:
        raise RuntimeError('SCHEDULER_ROLE_INVALID')
    return True


def require_scheduler_owner():
    if not scheduler_owns_gpu():
        raise RuntimeError('SCHEDULER_ROLE_REQUIRED')


def preserve_scheduler_restart_policy():
    """Scheduler-owned fallback must not revive itself on a Docker/WSL boot."""
    if scheduler_owns_gpu():
        docker('update', '--restart=no', TTS)

# A Windows desktop H3 process is outside Docker's GPU phase lock. Never stop it.
WINDOWS_POWERSHELL = pathlib.Path('/mnt/c/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')
LOCAL_H3_MEMORY_LIMIT = 512 * 1024 * 1024
WINDOWS_H3_PROBE = r"""
$ErrorActionPreference='Stop'
$processes=@(Get-CimInstance Win32_Process -Filter "Name='python.exe' OR Name='pythonw.exe'" | Where-Object {
    $_.Name -match '^python(w)?[.]exe$' -and
    ($_.ExecutablePath -match '(?i)(comfyui|h3[-_])' -or $_.CommandLine -match '(?i)(comfyui|h3[-_])')
})
$items=@()
if ($processes.Count -gt 0) {
    foreach ($process in $processes) {
        $processId=[int]$process.ProcessId
        $dedicated=0; $shared=0; $memoryKnown=$false
        if ($process.CommandLine -match '--port(?:[ =]+)([0-9]+)') {$ports=@([int]$Matches[1])}
        else {$ports=@(Get-NetTCPConnection -OwningProcess $processId -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty LocalPort -Unique | Select-Object -First 4)}
        $queueKnown=$false; $busy=$false
        foreach ($port in $ports) {
            try {
                $queue=Invoke-RestMethod -Uri ('http://127.0.0.1:'+ $port +'/queue') -TimeoutSec 2
                if ($null -ne $queue.queue_running -and $null -ne $queue.queue_pending) {
                    $queueKnown=$true
                    if (@($queue.queue_running).Count -gt 0 -or @($queue.queue_pending).Count -gt 0) {$busy=$true}
                    if (-not $busy) {
                        $stats=Invoke-RestMethod -Uri ('http://127.0.0.1:'+ $port +'/system_stats') -TimeoutSec 2
                        foreach ($device in @($stats.devices)) {
                            if ($device.type -eq 'cuda' -and $null -ne $device.torch_vram_total) {
                                $memoryKnown=$true; $dedicated=[math]::Max($dedicated,[long]$device.torch_vram_total)
                            }
                        }
                    }
                }
            } catch {}
        }
        $items+=@{pid=$processId;ports=$ports;queue_known=$queueKnown;memory_known=$memoryKnown;busy=$busy;dedicated_bytes=[long]$dedicated;shared_bytes=[long]$shared}
    }
}
@{known=$true;source='windows_process_and_queue';processes=$items} | ConvertTo-Json -Depth 5 -Compress
"""


def windows_h3_status():
    if not WINDOWS_POWERSHELL.exists():
        return {'known': True, 'source': 'no_windows_host', 'present': False, 'busy': False, 'blocked': False}
    try:
        encoded = base64.b64encode(WINDOWS_H3_PROBE.encode('utf-16le')).decode('ascii')
        result = subprocess.run([str(WINDOWS_POWERSHELL), '-NoProfile', '-NonInteractive',
                                 '-EncodedCommand', encoded],
                                capture_output=True, timeout=30, check=True)
        report = json.loads(result.stdout.decode('utf-8-sig'))
        processes = report['processes']
        if not isinstance(processes, list) or not report.get('known'):
            raise ValueError('INVALID_WINDOWS_PROBE')
        busy = any(item.get('busy') for item in processes)
        unknown_queue = any(not item.get('queue_known') or not (item.get('busy') or item.get('memory_known')) for item in processes)
        resident = any(max(item.get('dedicated_bytes', 0), item.get('shared_bytes', 0)) >= LOCAL_H3_MEMORY_LIMIT for item in processes)
        report.update(present=bool(processes), busy=busy, blocked=busy or unknown_queue or resident)
        return report
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return {'known': False, 'source': 'windows_process_and_queue', 'present': None,
                'busy': None, 'blocked': True, 'error': 'WINDOWS_H3_PROBE_FAILED'}


def guard_windows_h3():
    status = windows_h3_status()
    if not status.get('known'):
        raise RuntimeError('WINDOWS_H3_PROBE_FAILED')
    if status.get('busy'):
        raise RuntimeError('WINDOWS_H3_BUSY')
    if status.get('blocked'):
        raise RuntimeError('WINDOWS_H3_RESOURCE_BLOCKED')


def atomic(path, value):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as handle:
        json.dump(value, handle)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)

def api(path, data=None):
    request = urllib.request.Request(URL + path, data=json.dumps(data).encode() if data is not None else None,
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read()
    return json.loads(raw) if raw else {}

def docker(*args):
    return subprocess.check_output(['docker', *args], text=True).strip()

def running(name):
    return json.loads(docker('inspect', name))[0]['State']['Running']

def drain_status():
    c = json.loads(pathlib.Path('/etc/ray-worker/lifecycle-control.json').read_text())
    req = urllib.request.Request('http://10.250.0.1:8000/api/tts/internal/workers/'+c['node_ip']+'/drain',
        headers={'Authorization': 'Bearer '+c['token'], 'If-Match': str(c['generation'])})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)

TERMINAL = ('succeeded', 'failed', 'cancelled')


def unfinished(except_job=None):
    result=[]
    for file in (ROOT/'jobs').glob('h3job_*.json'):
        item=json.loads(file.read_text())
        if item.get('status') not in TERMINAL and item.get('job_id')!=except_job:
            result.append(item)
    return result


def queue_ids(queue):
    return {row[1] for key in ('queue_running','queue_pending') for row in queue[key]}


def reset_owned(req, path, job):
    """End an uncertain HTTP execution before releasing its durable fence."""
    if unfinished(req['job_id']):
        raise RuntimeError('OTHER_H3_EXECUTION_UNRESOLVED')
    if running(H3):
        queue=api('/queue')
        own={job['prompt_id']} if job and job.get('prompt_id') else set()
        if queue_ids(queue)-own:
            raise RuntimeError('FOREIGN_H3_QUEUE')
        # An empty queue is not proof a timed-out POST cannot still enqueue.
        docker('stop','--time','30',H3)
        if running(H3):raise RuntimeError('H3_STOP_NOT_CONFIRMED')
    job=job or {'job_id':req['job_id'],'attempt_token':req['attempt_token']}
    job.update(status='cancelled',cancel_requested=True,error_code=None,error_message=None)
    path.parent.mkdir(exist_ok=True);atomic(path,job)
    return {'status':'cancelled'}


def output_file(job, output):
    file=(ROOT/'output'/output.get('subfolder','')/output['filename']).resolve()
    expected=(ROOT/'output'/'jobs').resolve()
    if file.parent!=expected or not re.fullmatch(re.escape(job['job_id'])+r'(?:_[A-Za-z0-9_-]+)?\.mp4',file.name) or not file.is_file():
        raise ValueError('INVALID_OUTPUT_PATH')
    return file


def container_path(file):
    info=json.loads(docker('inspect',H3))[0]
    for mount in sorted(info.get('Mounts',[]),key=lambda m:len(m['Source']),reverse=True):
        source=pathlib.Path(mount['Source']).resolve()
        if file.is_relative_to(source):
            return str(pathlib.PurePosixPath(mount['Destination'])/file.relative_to(source).as_posix())
    raise ValueError('OUTPUT_NOT_MOUNTED')


def idle_h3():
    if unfinished():raise RuntimeError('H3_EXECUTION_UNRESOLVED')
    if running(H3):
        q = api('/queue')
        if q['queue_running'] or q['queue_pending']:
            raise RuntimeError('H3_BUSY')

def stop_h3():
    idle_h3()
    if running(H3):
        docker('stop', '--time', '30', H3)

def stopped_tts():
    # A global .20 dispatcher may remain active while this node serves H3.
    if not running(TTS):
        return
    s = drain_status()
    if not s.get('draining') or not s.get('drained') or s['active_jobs']:
        raise RuntimeError('TTS_NOT_DRAINED')
    if running(TTS):
        docker('stop', '--time', '60', TTS)

def job_path(req):
    import re
    if not re.fullmatch(r'h3job_[0-9a-f]{32}', req['job_id']):
        raise ValueError('INVALID_JOB')
    if not re.fullmatch(r'[0-9a-f]{32,64}', req['attempt_token']):
        raise ValueError('INVALID_TOKEN')
    return ROOT/'jobs'/(req['job_id']+'.json')

def read_job(req):
    p = job_path(req)
    j = json.loads(p.read_text()) if p.exists() else None
    if j is not None and j['attempt_token'] != req['attempt_token']:
        raise RuntimeError('STALE_ATTEMPT')
    return p, j

def boot_id():
    try:
        return pathlib.Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    except OSError:
        return None


def poll(req):
    p, j = read_job(req)
    if j is None:
        return {'status': 'unknown', 'idle': not running(H3) or not any(api('/queue').values())}
    if j['status'] in TERMINAL:
        return {k:v for k,v in j.items() if k not in ('result_path',)}
    if not running(H3):
        j.update(status='failed', error_code='WORKER_LOST', error_message='视频引擎已停止，请手动重试。')
        atomic(p,j)
        return poll(req)
    h = api('/history/'+j['prompt_id']).get(j['prompt_id'])
    if h:
        status = h.get('status',{})
        if status.get('status_str') == 'success':
            try:
                node=h.get('outputs',{}).get('149',{})
                outputs=[v for values in node.values() if isinstance(values,list) for v in values
                         if isinstance(v,dict) and str(v.get('filename','')).endswith('.mp4')]
                if len(outputs)!=1:raise ValueError('RESULT_MISSING_OR_AMBIGUOUS')
                file=output_file(j,outputs[0])
                probe=json.loads(docker('exec',H3,'ffprobe','-v','error','-show_streams','-show_format','-of','json',container_path(file)))
                video=next(stream for stream in probe['streams'] if stream['codec_type']=='video')
                duration=float(probe['format']['duration'])
                expected=j['spec']['num_frames']/j['spec']['fps']
                if video['width']!=j['spec']['width'] or video['height']!=j['spec']['height'] or not math.isfinite(duration) or duration<=0 or abs(duration-expected)>max(.15,2/j['spec']['fps']):
                    raise ValueError('RESULT_MEDIA_MISMATCH')
                j.update(status='succeeded',result_path=str(file),metadata={'width':video['width'],'height':video['height'],'duration':duration})
            except (ValueError,KeyError,StopIteration,OSError,subprocess.SubprocessError) as exc:
                j.update(status='failed',error_code='RESULT_VALIDATION_FAILED',error_message='视频输出校验失败。')
        else:
            cancelled=j.get('cancel_requested') or any(m[0]=='execution_interrupted' for m in status.get('messages',[]))
            j.update(status='cancelled' if cancelled else 'failed',error_code=None if cancelled else 'ENGINE_ERROR',error_message=None if cancelled else 'H3 推理失败，请查看管理员日志。')
        events = status.get('messages', [])
        starts = [e[1].get('timestamp') for e in events if e[0]=='execution_start' and isinstance(e[1].get('timestamp'),(int,float))]
        ends = [e[1].get('timestamp') for e in events if e[0] in ('execution_success','execution_error','execution_interrupted') and isinstance(e[1].get('timestamp'),(int,float))]
        elapsed = (max(ends)-min(starts))/1000 if starts and ends else time.time()-j['submitted_at']
        j['timings']={'execution_seconds':max(0,elapsed)}
        atomic(p,j)
        return poll(req)
    q=api('/queue')
    ids={row[1] for name in ('queue_running','queue_pending') for row in q[name]}
    if j['prompt_id'] not in ids:
        current_boot = boot_id()
        if j.get('boot_id') and current_boot and j['boot_id'] != current_boot:
            # The old kernel cannot enqueue again. Preserve other current work.
            j.update(status='failed', error_code='WORKER_RESTARTED',
                     error_message='GPU 节点或 WSL 已重启，原视频任务中断；请手动重试。')
            atomic(p,j)
            return poll(req)
        return {'status':'unknown','prompt_id':j['prompt_id'],'stage':'reconciling','idle':False}
    return {'status':'running','prompt_id':j['prompt_id'],'stage':'generating'}

def memory_health():
    """Small read-only sample to correlate node pressure with transport stalls."""
    result = {'boot_id': boot_id()}
    try:
        info = dict(line.split(':', 1) for line in pathlib.Path('/proc/meminfo').read_text().splitlines())
        for name in ('MemTotal', 'MemAvailable', 'SwapTotal', 'SwapFree'):
            result[name] = int(info[name].split()[0]) * 1024
    except (OSError, ValueError, KeyError):
        pass
    return result


def service_health():
    tts_live, h3_live = running(TTS), running(H3)
    try:
        control_ready = scheduler_owns_gpu()
    except RuntimeError:
        control_ready = False
    ready, busy = False, False
    if h3_live:
        try:
            with urllib.request.urlopen(URL+'/system_stats', timeout=3) as response:
                stats = json.load(response)
            with urllib.request.urlopen(URL+'/queue', timeout=3) as response:
                queue = json.load(response)
            ready = any(d.get('type')=='cuda' for d in stats.get('devices', []))
            busy = bool(queue.get('queue_running') or queue.get('queue_pending'))
        except (OSError, ValueError, KeyError):
            pass
    return {'tts_running':tts_live, 'h3_running':h3_live, 'h3_ready':ready,
            'h3_busy':busy, 'exclusive':not(tts_live and h3_live),
            'local_h3':windows_h3_status(), 'control_ready':control_ready,
            'checked_at':time.time(), 'memory':memory_health()}


def dispatch(req):
    action=req['action']
    if action in ('start_tts', 'stop_tts', 'start_h3', 'prepare_dedicated_h3', 'stop_all', 'stop_h3'):
        scheduler_owns_gpu()  # Reject a corrupt ownership marker before mutation.
    if action=='status':
        live=running(H3)
        q=api('/queue') if live else {'queue_running':[],'queue_pending':[]}
        return {'tts_running':running(TTS),'h3_running':live,'ready':live,
                'busy':bool(q['queue_running'] or q['queue_pending'] or unfinished())}
    if action=='stop_tts':
        stopped_tts()
        preserve_scheduler_restart_policy()
        return {'tts_running':running(TTS)}
    if action == 'stop_h3':
        # This narrow action is used by the scheduler's dedicated-phase
        # recovery. It never drains or mutates the TTS container.
        require_scheduler_owner()
        idle_h3()
        if running(H3):
            docker('stop', '--time', '30', H3)
        return {'h3_running': running(H3)}
    if action=='prepare_dedicated_h3':
        action='start_h3'
    if action in ('start_tts','start_h3','stop_all'):
        if action == 'start_h3' and (ROOT / 'model-cache-copy.active').exists():
            raise RuntimeError('MODEL_CACHE_COPY_IN_PROGRESS')
        if action in ('start_tts', 'start_h3'):
            guard_windows_h3()
        started=time.monotonic()
        if action=='start_tts':
            stop_h3()
            preserve_scheduler_restart_policy()
            if not running(TTS):docker('start',TTS)
        else:
            stopped_tts()
            preserve_scheduler_restart_policy()
            if action=='stop_all':stop_h3()
            else:
                if not running(H3):docker('start',H3)
                deadline=time.monotonic()+120
                while True:
                    try:
                        stats=api('/system_stats')
                        assert any(d['type']=='cuda' for d in stats['devices'])
                        break
                    except (OSError,AssertionError):
                        if time.monotonic()>deadline:raise RuntimeError('H3_START_TIMEOUT')
                        time.sleep(2)
        return {'startup_seconds':time.monotonic()-started}
    if action=='submit':
        p,j=read_job(req)
        if j:return poll(req)
        guard_windows_h3()
        spec=req['spec']
        if (type(spec.get('width')) is not int or type(spec.get('height')) is not int
                or spec['width'] <= 0 or spec['height'] <= 0
                or spec['width'] * spec['height'] > 1664 * 936):
            raise ValueError('H3_OUTPUT_PIXEL_LIMIT_EXCEEDED')
        # 14 requested seconds may align upward to 345 frames (17n+5).
        seconds = spec.get('duration_seconds')
        if (type(seconds) not in (int, float) or not 0 < seconds <= 14
                or type(spec.get('num_frames')) is not int or not 0 < spec['num_frames'] <= 345):
            raise ValueError('H3_REQUEST_DURATION_LIMIT_14_SECONDS')
        if running(TTS):raise RuntimeError('TTS_STILL_RUNNING')
        idle_h3()
        spec=req['spec'];raw=base64.b64decode(req['image_b64'],validate=True)
        if len(raw)>64*1024*1024:raise ValueError('IMAGE_TOO_LARGE')
        image=req['job_id']+'.png';(ROOT/'input'/image).write_bytes(raw)
        g=json.loads((ROOT/'profiles-linux.json').read_text())[0]['workflow']
        g['137']['inputs']['image']=image;g['157']['inputs']['value']=spec['prompt']
        g['131']['inputs']['expression']='round(a)';g['131']['inputs']['values.a']=spec['num_frames']
        g['181']['inputs']['width']=spec['width'];g['181']['inputs']['height']=spec['height']
        g['129']['inputs']['noise_seed']=spec['seed'];g['149']['inputs']['filename_prefix']='jobs/'+req['job_id']
        j={'job_id':req['job_id'],'attempt_token':req['attempt_token'],'prompt_id':str(uuid.uuid4()),'status':'submitting','submitted_at':time.time(),'spec':spec,'boot_id':boot_id()}
        p.parent.mkdir(exist_ok=True);atomic(p,j)
        try:
            accepted=api('/prompt',{'prompt':g,'prompt_id':j['prompt_id'],'client_id':req['job_id']})
        except urllib.error.HTTPError as exc:
            if exc.code!=400:raise
            j.update(status='failed',error_code='INVALID_WORKFLOW',error_message='工作流校验失败。');atomic(p,j);return poll(req)
        returned=accepted.get('prompt_id')
        if not isinstance(returned,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}',returned):
            raise RuntimeError('SUBMIT_ID_UNKNOWN')
        j.update(prompt_id=returned,status='running');atomic(p,j)
        return {'status':'running','prompt_id':j['prompt_id']}
    if action=='poll':return poll(req)
    if action=='cancel':
        p,j=read_job(req)
        if not j:return reset_owned(req,p,j)
        if j['status'] in TERMINAL:return poll(req)
        j['cancel_requested']=True;atomic(p,j)
        if running(H3):
            queue=api('/queue')
            if queue_ids(queue)-{j['prompt_id']}:
                raise RuntimeError('FOREIGN_H3_QUEUE')
            if any(row[1]==j['prompt_id'] for row in queue['queue_pending']):
                api('/queue',{'delete':[j['prompt_id']]})
            if any(row[1]==j['prompt_id'] for row in queue['queue_running']):
                api('/interrupt',{'prompt_id':j['prompt_id']})
        report=poll(req)
        return reset_owned(req,p,j) if report['status']=='unknown' else report
    if action=='result':
        p,j=read_job(req)
        if not j or j['status']!='succeeded':raise RuntimeError('RESULT_NOT_READY')
        file=pathlib.Path(j['result_path'])
        verified=output_file(j,{'subfolder':'jobs','filename':file.name})
        if verified!=file.resolve():raise RuntimeError('INVALID_OUTPUT_PATH')
        if verified.stat().st_size>250*1024*1024:raise RuntimeError('RESULT_TOO_LARGE')
        raw=verified.read_bytes()
        return {'data_b64':base64.b64encode(raw).decode(),'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'metadata':j.get('metadata',{})}
    raise ValueError('UNKNOWN_ACTION')

@contextmanager
def phase_lock(path='/run/lock/ocv-gpu-phase.lock', timeout=30):
    """Bound waiting before any side effect; never steal another operation's lock."""
    with open(path, 'a') as lock:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError('NODE_PHASE_BUSY') from None
                time.sleep(0.1)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


if __name__=='__main__':
    request=json.load(sys.stdin)
    if request.get('action')=='health':
        try:print(json.dumps({'ok':True,'result':service_health()}))
        except Exception:print(json.dumps({'ok':False,'error':'HEALTH_PROBE_FAILED'}));sys.exit(1)
        sys.exit(0)
    try:
        with phase_lock():
            result=dispatch(request)
        print(json.dumps({'ok':True,'result':result}))
    except Exception as exc:
        print(json.dumps({'ok':False,'error':str(exc)[:300]}));sys.exit(1)
