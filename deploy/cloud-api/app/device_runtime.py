"""Per-device service observations; this request path only reads local caches."""
from datetime import datetime, timezone
import ipaddress
import json
import math
import os
from pathlib import Path
import time

NAMES = {'tts': 'ray-worker-managed', 'h3': 'ocv-h3-comfyui'}
KNOWN_NODES = {'10.250.0.3', '10.250.0.20'}


def read_cache(path):
    try:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def _stamp(sample):
    try:
        stamp = float(sample['time'])
        return stamp if math.isfinite(stamp) else None
    except (KeyError, TypeError, ValueError):
        return None


def _fresh(sample, now, ttl):
    stamp = _stamp(sample)
    return stamp is not None and 0 <= now - stamp <= ttl


def _iso(stamp):
    try:
        return datetime.fromtimestamp(stamp, timezone.utc).isoformat() if stamp is not None else None
    except (ValueError, OverflowError, OSError):
        return None


def _blank(kind, node_ip, reason, source, stamp=None):
    return {'container_name': NAMES[kind] if node_ip in KNOWN_NODES else None,
            'container_running': None, 'ray_connected': None, 'ready': None,
            'status': 'unknown', 'reason': reason, 'checked_at': _iso(stamp), 'source': source,
            'control_ready': False}


def device_services(node_ip, *, now=None, node=None, placement=None, root=None):
    """Never infer another node's readiness from global health or its role."""
    now = time.time() if now is None else now
    try:
        node_ip = str(ipaddress.ip_interface(str(node_ip)).ip)
    except ValueError:
        node_ip = None
    root = Path(root or os.environ.get('GPU_SCHEDULER_STATE_DIR', '/var/lib/cloud-api/gpu-scheduler'))
    source = 'scheduler_node_health' if node_ip == '10.250.0.3' else 'device_telemetry'
    if node is None:
        filename = {'10.250.0.3': 'node-health.json', '10.250.0.20': 'node20-health.json'}.get(node_ip)
        node = read_cache(root / filename) if filename else {}
    if placement is None:
        placement = read_cache(root / 'placement-health.json')
    result = {kind: _blank(kind, node_ip, '尚无该节点服务探测记录', source, _stamp(node)) for kind in NAMES}
    if node_ip not in KNOWN_NODES:
        return result
    # .3's old cache is intrinsically bound to its fixed node client. New
    # telemetry is explicitly keyed so data cannot leak between device rows.
    if node.get('node_ip', '10.250.0.3' if node_ip == '10.250.0.3' else None) != node_ip:
        return result
    ttl = 45 if node_ip == '10.250.0.3' else 90
    node_fresh = _fresh(node, now, ttl)
    if not node_fresh or node.get('reachable') is not True:
        reason = '服务探测记录已过期' if node and not node_fresh else '节点服务探测失败或尚未上报'
        for item in result.values():
            item['reason'] = reason
        return result
    for kind, item in result.items():
        observed = (node.get('containers') or {}).get(kind)
        if node_ip == '10.250.0.3':
            running = node.get(kind + '_running')
            observed = {'installed': True, 'running': running} if isinstance(running, bool) else None
            item['control_ready'] = node.get('control_ready') is True if kind == 'h3' else False
        elif node_ip == '10.250.0.20':
            item['control_ready'] = node.get('control_ready') is True if kind == 'tts' else False
        if not isinstance(observed, dict) or not isinstance(observed.get('installed'), bool):
            item['reason'] = '容器状态探测结果缺失'
            continue
        if observed['installed'] is False:
            item.update(container_running=False, ready=False, status='not_installed', reason='未发现该服务容器')
            continue
        running = observed.get('running')
        if not isinstance(running, bool):
            item['reason'] = '容器运行状态未知'
            continue
        item['container_running'] = running
        if kind == 'h3':
            # ComfyUI is not a Ray worker; membership is not applicable.
            if not running:
                item.update(ready=False, status='stopped', reason='H3 容器已停止')
            elif node_ip == '10.250.0.3' and isinstance(node.get('h3_ready'), bool):
                ready = (node['h3_ready'] and node.get('exclusive') is True
                         and not (node.get('local_h3') or {}).get('blocked', False))
                item.update(ready=bool(ready), status='ready' if ready else 'running',
                            reason='H3 接口与 CUDA 正常' if ready else 'H3 容器运行，接口未就绪或显卡资源被占用')
            else:
                item.update(status='running', reason='H3 容器运行，尚无对应接口就绪探测')
            continue
        observed_placement = (_fresh(placement, now, 90) and placement.get('ok') is True)
        if observed_placement:
            ray = (placement.get('nodes') or {}).get(node_ip, {}).get('ray_alive')
            item['ray_connected'] = ray if isinstance(ray, bool) else None
        if not running:
            reason = ('RayWorker 虚拟机已停止，TTS 容器可按需启动'
                      if node.get('state_inferred') is True else 'TTS 容器已停止，可能按需休眠')
            item.update(ready=False, status='stopped', reason=reason)
            continue
        item['source'] += '+serve_placement'
        if not observed_placement:
            item['reason'] = 'TTS 容器运行，但 Ray/模型归属探测失败或已过期'
            continue
        item['checked_at'] = _iso(min(_stamp(node), _stamp(placement)))
        if item['ray_connected'] is None:
            item['reason'] = 'TTS 容器运行，但该节点 Ray 状态未知'
            continue
        if item['ray_connected'] is False:
            item.update(ready=False, status='running', reason='TTS 容器运行，尚未连接 Ray')
            continue
        own = (placement.get('target_node') == node_ip and placement.get('actual_pin') == node_ip
               and placement.get('lifecycle_target') == node_ip and placement.get('transaction_pending') is False)
        ready = own and placement.get('placement_ready') is True and placement.get('ready') is True
        # Controller-ready replicas with a false HTTP health can be a queued
        # probe behind inference. Keep readiness uncertain, not failed.
        readiness = None if own and placement.get('placement_ready') is True and not ready else bool(ready)
        item.update(ready=readiness, status='ready' if ready else 'running',
                    reason='该节点 TTS 模型副本与服务已就绪' if ready else
                    'TTS 副本运行，当前接口就绪探测尚未确认' if readiness is None else
                    'TTS 已连接 Ray，模型或调度尚未就绪' if own else 'TTS 已连接 Ray，当前模型未分配给此节点或正在切换')
    return result
