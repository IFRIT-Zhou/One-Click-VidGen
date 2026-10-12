"""Administrative health distinguishes execution readiness from managed sleep."""
import json
import os
import time
from pathlib import Path


def cluster_runtime(ray, *, now=None, heartbeat=None):
    dispatcher = ray.get('dispatcher') or {}
    deps = dispatcher.get('dependencies') or {}
    model_ready = (deps.get('model_worker') or {}).get('ready') is True
    audio_ready = (deps.get('audio_finalizer') or {}).get('ready') is True
    control_ready = dispatcher.get('consumer_alive') is True and dispatcher.get('redis_ready') is True
    enabled = os.environ.get('GPU_SCHEDULER_ENABLED', '0') == '1'
    mode, fresh, scheduler_ready = None, False, False
    if enabled:
        try:
            if heartbeat is None:
                path = Path(os.environ.get('GPU_SCHEDULER_STATE_DIR', '/var/lib/cloud-api/gpu-scheduler')) / 'heartbeat.json'
                heartbeat = json.loads(path.read_text())
            age = (time.time() if now is None else now) - float(heartbeat['time'])
            fresh = 0 <= age <= 15
            mode = heartbeat.get('mode') if fresh else None
            scheduler_ready = fresh and heartbeat.get('scheduler_ready') is True and heartbeat.get('queue_ready') is True
        except (OSError, ValueError, TypeError, KeyError):
            pass
    states = {
        'off': ('sleeping', '按需休眠', '模型已释放，有任务时自动启动'),
        'idle': ('switching', '等待调度', '正在检查队列并安排 GPU 阶段'),
        'tts_starting': ('starting', '配音启动中', '正在加载配音模型，任务可排队等待'),
        'tts_draining': ('switching', '阶段切换中', '等待已接配音任务结束后释放资源'),
        'h3_starting': ('starting', '视频启动中', '正在启动 H3 引擎，配音任务可排队等待'),
        'h3': ('video', 'H3 处理中', 'GPU 分配给 H3；配音模型按需休眠'),
        'tts': ('ready', '配音就绪', '配音模型已加载，可执行任务'),
    }
    healthy = control_ready and ((scheduler_ready and mode in states) if enabled else ray.get('ok') is True)
    if enabled and mode == 'tts' and not (model_ready and audio_ready):
        healthy = False
    if healthy:
        state, label, detail = states[mode] if enabled else ('ready', '配音就绪', '模型与调度器就绪')
    else:
        state, label = 'error', '需要关注'
        detail = ('控制服务或队列异常' if not control_ready else
                  '调度器心跳过期或未上报' if enabled and not fresh else
                  '调度器正在恢复或检查失败' if enabled and not scheduler_ready else '模型尚未就绪或状态不一致')
    model_label = '已加载' if model_ready else ('按需休眠' if healthy and mode in ('off','idle','h3','h3_starting') else '启动中' if healthy and mode=='tts_starting' else '未就绪')
    return {'healthy': healthy, 'state': state, 'label': label, 'detail': detail,
            'on_demand': enabled, 'mode': mode, 'heartbeat_fresh': fresh,
            'scheduler_ready': scheduler_ready if enabled else dispatcher.get('ready') is True,
            'model_ready': model_ready, 'model_label': model_label,
            'audio_ready': audio_ready}


def service_statuses(ray, runtime, *, now=None, node=None):
    now = time.time() if now is None else now
    if node is None:
        try:
            root = Path(os.environ.get('GPU_SCHEDULER_STATE_DIR', '/var/lib/cloud-api/gpu-scheduler'))
            node = json.loads((root/'node-health.json').read_text())
        except (OSError, ValueError):
            node = {}
    try:
        fresh = 0 <= now-float(node['time']) <= 45
    except (KeyError, ValueError, TypeError):
        fresh = False
    dis = ray.get('dispatcher') or {}
    deps = dis.get('dependencies') or {}
    model = (deps.get('model_worker') or {}).get('ready') is True
    audio = (deps.get('audio_finalizer') or {}).get('ready') is True
    mode = runtime.get('mode')
    observed = fresh and node.get('reachable') is True
    managed = runtime.get('on_demand') and runtime.get('heartbeat_fresh') and runtime.get('scheduler_ready')
    control = dis.get('consumer_alive') is True and dis.get('redis_ready') is True
    def status(name, running, ready, busy, own, other):
        state, label, detail = 'unknown', '无法确认', '节点探测失败或结果已过期'
        healthy = False
        if observed:
            if node.get('exclusive') is not True:
                state, label, detail = 'error', '异常', '两个 GPU 容器同时运行，请检查资源互斥'
            elif not managed or not control:
                state, label, detail = 'error', '需要关注', '调度器或队列异常；容器状态仅供诊断'
            elif running and ready:
                state, label, detail, healthy = ('busy' if busy else 'ready'), ('执行中' if busy else '就绪'), ('模型已就绪' if name=='TTS' else 'H3 接口与 CUDA 正常；模型随任务加载'), True
            elif mode == own+'_starting':
                state, label, detail, healthy = 'starting', '启动中', '正在启动容器并准备服务', True
            elif mode in ('idle','tts_draining'):
                state, label, detail, healthy = 'switching', '切换中', '正在完成上一阶段并切换资源', True
            elif not running and mode in ('off', other, other+'_starting'):
                state, label, detail, healthy = 'sleeping', '按需休眠', '有任务时由调度器自动启动', True
            else:
                state, label, detail = 'error', '异常', '实际容器或服务状态与调度阶段不一致'
        return {'name':name, 'state':state, 'label':label, 'detail':detail,
                'healthy':healthy, 'container_running':running if observed else None,
                'ready':ready if observed else False, 'probe_fresh':fresh}
    return {
        'tts': status('TTS',node.get('tts_running') is True,model and audio,
                      int(dis.get('active_jobs') or 0)>0,'tts','h3'),
        'h3': status('H3',node.get('h3_running') is True,node.get('h3_ready') is True,
                     node.get('h3_busy') is True,'h3','tts')}
