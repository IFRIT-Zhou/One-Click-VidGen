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
        'dedicated': ('ready', '固定节点运行', '.20 常驻配音，.3 独立处理 H3'),
        'dedicated_starting': ('starting', '配音启动中', '正在 .20 加载配音模型，完成后常驻'),
        'dedicated_switching': ('switching', '配音迁移中', '等待已接任务完成后切换配音节点'),
        'primary_waiting': ('waiting', '等待主配音节点', '正在确认 .20 配音状态；有配音任务且确认服务不可用后使用 .3'),
        'fallback_waiting': ('waiting', '等待备用显卡', '等待 .3 视频任务安全结束后承接配音'),
    }
    if (heartbeat or {}).get('policy') == 'dedicated':
        states['idle'] = ('sleeping', '按需休眠', '.20 配音不可用且当前无任务，.3 已释放两个模型，等待新任务')
    healthy = control_ready and ((scheduler_ready and mode in states) if enabled else ray.get('ok') is True)
    if enabled and mode in ('tts', 'dedicated') and not (model_ready and audio_ready):
        healthy = False
    if healthy:
        state, label, detail = states[mode] if enabled else ('ready', '配音就绪', '模型与调度器就绪')
    else:
        state, label = 'error', '需要关注'
        detail = ('控制服务或队列异常' if not control_ready else
                  '调度器心跳过期或未上报' if enabled and not fresh else
                  '配音模型启动超时，请检查节点依赖与模型日志' if fresh and (heartbeat or {}).get('tts_state') == 'unavailable' else
                  '调度器正在恢复或检查失败' if enabled and not scheduler_ready else '模型尚未就绪或状态不一致')
    model_label = '已加载' if model_ready else ('按需休眠' if healthy and mode in ('off','idle','h3','h3_starting') else '启动中' if healthy and mode=='tts_starting' else '未就绪')
    return {'healthy': healthy, 'state': state, 'label': label, 'detail': detail,
            'policy': (heartbeat or {}).get('policy') if fresh else None,
            'tts_node': (heartbeat or {}).get('tts_node') if fresh else None,
            'h3_state': (heartbeat or {}).get('h3_state') if fresh else None,
            'tts_state': (heartbeat or {}).get('tts_state') if fresh else None,
            'tts_blocked_reason': (heartbeat or {}).get('tts_blocked_reason') if fresh else None,
            'blocked_reason': (heartbeat or {}).get('blocked_reason') if fresh else None,
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
    if runtime.get('policy') == 'dedicated' and runtime.get('tts_node') == '10.250.0.20':
        tts_ready = control and model and audio and managed
        unavailable = runtime.get('tts_state') == 'unavailable'
        tts = {'name': 'TTS', 'node_ip': '10.250.0.20', 'state': 'ready' if tts_ready else 'error' if unavailable else 'starting',
               'label': '常驻就绪' if tts_ready else '启动失败' if unavailable else '等待就绪',
               'detail': '配音模型启动超时，请检查节点依赖与模型日志' if unavailable else '.20 固定配音节点',
               'healthy': tts_ready, 'container_running': True if tts_ready else None,
               'ready': tts_ready, 'probe_fresh': runtime.get('heartbeat_fresh') is True}
        local = node.get('local_h3') or {}
        h3_ready = observed and node.get('h3_ready') is True and node.get('exclusive') is True
        blocked = local.get('blocked') is True or runtime.get('h3_state') == 'blocked'
        h3 = {'name': 'H3', 'node_ip': '10.250.0.3',
              'state': 'blocked' if blocked else 'ready' if h3_ready else 'starting' if observed else 'unknown',
              'label': '等待本地任务释放显卡' if blocked else '常驻就绪' if h3_ready else '等待就绪',
              'detail': '.3 固定视频节点；Windows 本地 H3 与集群容器共享显卡',
              'healthy': h3_ready and not blocked, 'container_running': node.get('h3_running') if observed else None,
              'ready': h3_ready and not blocked, 'probe_fresh': fresh}
        return {'tts': tts, 'h3': h3}
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
            elif mode == 'idle' and runtime.get('policy') == 'dedicated' and not running:
                state, label, detail, healthy = 'sleeping', '按需休眠', '备用节点当前无任务，模型已释放；新任务到来后按需启动', True
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
