"""Head-local, drain-gated TTS placement changes for Ray 2.56.0.

Run with /opt/ray-tts/head/.venv/bin/python. This helper never resumes dispatch,
restarts the head, starts/stops containers, or prints deployment/credential data.
The scheduler must serialize lifecycle actions and wait for target readiness.
"""
from __future__ import annotations

import argparse
import contextlib
import copy
import hashlib
import json
import os
from pathlib import Path
import secrets
import tempfile
import time
import urllib.request

NODES = ('10.250.0.3', '10.250.0.20')
APP = 'ray-tts'
WORKER = 'IndexTTS2Worker'
SUPPORTED_RAY = '2.56.0'


class PlacementError(RuntimeError):
    """Only a fixed, non-sensitive code may cross the CLI boundary."""


def validate_target(target):
    if target not in NODES:
        raise PlacementError('INVALID_TARGET')
    return target


def actor_options_target(options):
    pins = [key[5:] for key, value in options.get('resources', {}).items()
            if key.startswith('node:') and float(value) > 0]
    if len(pins) != 1 or pins[0] not in NODES:
        raise PlacementError('UNEXPECTED_WORKER_PIN')
    return pins[0]


def repin_actor_options(options, target):
    validate_target(target)
    actor_options_target(options)
    result = copy.deepcopy(options)
    resources = result['resources']
    for key in list(resources):
        if key.startswith('node:'):
            del resources[key]
    resources['node:' + target] = 0.001
    return result


def update_env_text(text, target):
    validate_target(target)
    lines = text.splitlines()
    positions = [i for i, line in enumerate(lines) if line.startswith('TTS_WORKER_NODE_IP=')]
    if len(positions) > 1:
        raise PlacementError('DUPLICATE_ENV_TARGET')
    value = 'TTS_WORKER_NODE_IP=' + target
    if positions:
        lines[positions[0]] = value
    else:
        lines.append(value)
    return '\n'.join(lines) + '\n'


def node_summary(records):
    result = {ip: {'online': False, 'ray_alive': False, 'gpu': 0.0, 'tts_gpu': 0.0, 'node_ids': []} for ip in NODES}
    for node in records:
        ip = node.get('NodeManagerAddress')
        if ip not in result or node.get('Alive') is not True:
            continue
        entry = result[ip]
        resources = node.get('Resources', {})
        entry['ray_alive'] = True
        entry['gpu'] += float(resources.get('GPU', 0))
        entry['tts_gpu'] += float(resources.get('tts_gpu', 0))
        entry['node_ids'].append(node['NodeID'])
    for entry in result.values():
        entry['online'] = entry['ray_alive'] and entry['gpu'] > 0 and entry['tts_gpu'] > 0
    return result


def _enum(value):
    return str(getattr(value, 'value', value)).split('.')[-1]


def summarize_replicas(details, target):
    replicas = details.get('replicas', [])
    running = [r for r in replicas if _enum(r.get('state')) == 'RUNNING']
    desired = int(details.get('target_num_replicas') or 0)
    nodes = sorted({r.get('node_ip') for r in running if r.get('node_ip')})
    ready = (desired > 0 and len(running) == desired and len(replicas) == desired
             and all(r.get('node_ip') == target for r in running)
             and _enum(details.get('status')) == 'HEALTHY')
    return {'placement_ready': ready, 'worker_nodes': nodes,
            'running_replicas': len(running), 'desired_replicas': desired}


def primary_tts_summary(details, target):
    """Serve lifecycle evidence, independent of queued inference health RPCs."""
    status = _enum(details.get('status'))
    state = summarize_replicas(details, '10.250.0.20')
    if target != '10.250.0.20':
        availability = 'unassigned'
    elif state['placement_ready']:
        availability = 'ready'
    elif status in ('UNHEALTHY', 'DEPLOY_FAILED'):
        availability = 'failed'
    elif status in ('UPDATING', 'UPSCALING', 'DOWNSCALING', 'HEALTHY'):
        availability = 'starting'
    else:
        availability = 'unknown'
    return {'state': availability, 'source': 'serve_controller',
            'deploy_status': status, 'running_replicas': state['running_replicas']}


def _atomic_write(path, text, mode=0o600):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    old = path.stat() if path.exists() else None
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + '.', dir=path.parent)
    try:
        os.fchmod(descriptor, mode if old is None else old.st_mode & 0o777)
        if old is not None and hasattr(os, 'fchown'):
            os.fchown(descriptor, old.st_uid, old.st_gid)
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextlib.contextmanager
def exclusive_lock(path):
    import fcntl
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with path.open('a') as stream:
        os.chmod(path, 0o600)
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise PlacementError('PLACEMENT_BUSY') from None
        yield


def build_deployment_arguments(routes, target, job_id, argument_type, retry_version=None):
    """Transform copies of existing protos; all non-worker bytes stay unchanged."""
    arguments = []
    ingress_count = 0
    for name, route in routes.items():
        info = route.deployment_info
        if not info.deployment_config.version:
            raise PlacementError('MISSING_DEPLOYMENT_VERSION')
        replica = copy.deepcopy(info.replica_config)
        deployment_config = copy.deepcopy(info.deployment_config)
        if name == WORKER:
            if retry_version is not None:
                deployment_config.version = retry_version
            options = repin_actor_options(json.loads(replica.ray_actor_options), target)
            replica.ray_actor_options = json.dumps(options)
        # Routes are assigned only to the ingress by the installed controller.
        ingress = bool(route.route)
        ingress_count += int(ingress)
        values = {'deployment_name': name,
                  'deployment_config': deployment_config.SerializeToString(),
                  'replica_config': replica.SerializeToString(),
                  'deployer_job_id': job_id,
                  'ingress': ingress,
                  'ingress_request_router': info.ingress_request_router}
        if ingress:
            values['route_prefix'] = route.route
        arguments.append(argument_type(**values).SerializeToString())
    if ingress_count != 1:
        raise PlacementError('UNEXPECTED_INGRESS_TOPOLOGY')
    return arguments


class RayPlacement:
    """Version-pinned adapter; no service.app import or serve.run call."""

    def __init__(self):
        os.environ['PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION'] = 'python'
        import ray
        if ray.__version__ != SUPPORTED_RAY:
            raise PlacementError('UNSUPPORTED_RAY_VERSION')
        from ray.serve.context import _get_global_client
        from ray.serve.generated.serve_pb2 import DeploymentRoute, DeploymentArgs, ApplicationArgs
        self.ray = ray
        self.DeploymentRoute, self.DeploymentArgs, self.ApplicationArgs = DeploymentRoute, DeploymentArgs, ApplicationArgs
        ray.init(address='127.0.0.1:6379', namespace='ray-tts', logging_level='ERROR', log_to_driver=False)
        self.controller = _get_global_client()._controller

    def _get(self, reference):
        return self.ray.get(reference, timeout=30)

    def capture(self):
        details = self._get(self.controller.get_serve_instance_details.remote())
        app = details.get('applications', {}).get(APP)
        if not app or not {WORKER, 'TTSDispatcher', 'AudioFinalizer', 'TTSIngress'}.issubset(app['deployments']):
            raise PlacementError('UNEXPECTED_APP_TOPOLOGY')
        routes = {name: self.DeploymentRoute.FromString(self._get(
            self.controller.get_deployment_info.remote(name, APP))) for name in app['deployments']}
        return app, routes

    @staticmethod
    def target(routes):
        return actor_options_target(json.loads(routes[WORKER].deployment_info.replica_config.ray_actor_options))

    @staticmethod
    def control_digest(routes):
        # Exclude mutable timestamps. Bytes stay in process; only hashes persist.
        digest = hashlib.sha256()
        for name in sorted(routes):
            if name == WORKER:
                continue
            route = routes[name]
            digest.update(name.encode())
            digest.update(route.deployment_info.deployment_config.SerializeToString())
            digest.update(route.deployment_info.replica_config.SerializeToString())
            digest.update(route.route.encode())
        return digest.hexdigest()

    def snapshot(self):
        app, routes = self.capture()
        target = self.target(routes)
        state = summarize_replicas(app['deployments'][WORKER], target)
        control_ids = {name: sorted(r.get('actor_id', '') for r in item.get('replicas', [])
                                   if _enum(r.get('state')) == 'RUNNING')
                       for name, item in app['deployments'].items() if name != WORKER}
        return {'target_node': target, 'actual_pin': target, 'ray_version': SUPPORTED_RAY,
                'nodes': node_summary(self.ray.nodes()), 'control_actor_ids': control_ids,
                'primary_tts': primary_tts_summary(app['deployments'][WORKER], target), **state}

    def repin(self, target, expected_digest, retry_version=None):
        app, routes = self.capture()
        if self.control_digest(routes) != expected_digest:
            raise PlacementError('CONTROL_DEPLOYMENT_CHANGED')
        if (self.target(routes) == target and (retry_version is None or
                routes[WORKER].deployment_info.deployment_config.version == retry_version)):
            return
        arguments = build_deployment_arguments(
            routes, target, self.ray.get_runtime_context().get_job_id(), self.DeploymentArgs, retry_version)
        application = self.ApplicationArgs(external_scaler_enabled=bool(app.get('external_scaler_enabled')))
        self._get(self.controller.deploy_applications.remote(
            {APP: arguments}, {APP: application.SerializeToString()}))
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            _, actual = self.capture()
            if self.control_digest(actual) != expected_digest:
                raise PlacementError('CONTROL_DEPLOYMENT_CHANGED')
            if (self.target(actual) == target and (retry_version is None or
                    actual[WORKER].deployment_info.deployment_config.version == retry_version)):
                return
            time.sleep(0.5)
        raise PlacementError('PLACEMENT_UPDATE_PENDING')


class PlacementManager:
    def __init__(self, ray, *, lifecycle='/etc/ray-tts/worker-lifecycle.json',
                 env='/opt/ray-tts/head/.env', state='/var/lib/cloud-api/tts-placement',
                 base_url='http://127.0.0.1:8000/api/tts', http=None):
        self.ray = ray
        self.lifecycle, self.env, self.state = Path(lifecycle), Path(env), Path(state)
        self.base_url = base_url
        self.http = http or self._http

    def _http(self, url, method='GET', headers=None):
        request = urllib.request.Request(url, method=method, headers=headers or {})
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.load(response)

    def config(self):
        config = json.loads(self.lifecycle.read_text())
        validate_target(config.get('dispatch_node_ip'))
        return config

    def drain_status(self, config):
        current = config['dispatch_node_ip']
        peer = config['workers'][current]
        # Read-only check: caller initiates drain, this helper never implicitly pauses work.
        status = self.http(self.base_url + '/internal/workers/' + current + '/drain',
                           headers={'Authorization': 'Bearer ' + peer['token'],
                                    'If-Match': str(peer['generation'])})
        if (status.get('draining') is not True or status.get('drained') is not True
                or status.get('active_jobs') != 0):
            raise PlacementError('DISPATCH_NOT_DRAINED')
        return status

    @property
    def transaction_path(self):
        return self.state / 'transaction.json'

    def transaction(self):
        if self.transaction_path.exists():
            return json.loads(self.transaction_path.read_text())
        return None

    def snapshot(self):
        result = self.ray.snapshot()
        config = self.config()
        transaction = self.transaction()
        health_ready = False
        try:
            health = self.http(self.base_url + '/v1/health')
            dependencies = health.get('dispatcher', {}).get('dependencies', {})
            health_ready = (health.get('ok') is True
                            and dependencies.get('model_worker', {}).get('ready') is True
                            and dependencies.get('audio_finalizer', {}).get('ready') is True)
        except Exception:
            pass
        return {'ok': True, **result, 'lifecycle_target': config['dispatch_node_ip'],
                'ready': bool(result['placement_ready'] and health_ready
                              and config['dispatch_node_ip'] == result['target_node']
                              and transaction is None),
                'transaction_pending': transaction is not None,
                'transaction_target': transaction.get('target') if transaction else None}

    def select(self, target, retry_worker=False):
        validate_target(target)
        with exclusive_lock(self.state / 'placement.lock'):
            config = self.config()
            transaction = self.transaction()
            if transaction and transaction['target'] != target:
                raise PlacementError('OTHER_TRANSITION_PENDING')
            app, routes = self.ray.capture()
            actual = self.ray.target(routes)
            if retry_worker and (actual != target or config['dispatch_node_ip'] != target):
                raise PlacementError('RETRY_REQUIRES_CURRENT_TARGET')
            current_env = self.env.read_text()
            desired_env = update_env_text(current_env, target)
            if (not retry_worker and not transaction and actual == target and config['dispatch_node_ip'] == target
                    and desired_env == current_env):
                return {**self.snapshot(), 'changed': False}
            self.drain_status(config)
            if transaction is None:
                if actual != config['dispatch_node_ip']:
                    raise PlacementError('UNTRACKED_TARGET_MISMATCH')
                transaction = {'old': actual, 'target': target, 'phase': 'prepared',
                               'control_digest': self.ray.control_digest(routes),
                               'created_at': time.time()}
                if retry_worker:
                    transaction['retry_version'] = 'placement-retry-' + secrets.token_hex(12)
                _atomic_write(self.transaction_path, json.dumps(transaction))
            elif actual not in (transaction['old'], target):
                raise PlacementError('UNEXPECTED_TRANSITION_TARGET')
            if self.ray.control_digest(routes) != transaction['control_digest']:
                raise PlacementError('CONTROL_DEPLOYMENT_CHANGED')
            self.ray.repin(target, transaction['control_digest'], transaction.get('retry_version'))
            transaction['phase'] = 'placement_applied'
            _atomic_write(self.transaction_path, json.dumps(transaction))
            # Recheck admission immediately before persisting the auth target.
            config = self.config()
            self.drain_status(config)
            if config['dispatch_node_ip'] not in (transaction['old'], target):
                raise PlacementError('LIFECYCLE_TARGET_CHANGED')
            if target not in config['workers']:
                if target != '10.250.0.20':
                    raise PlacementError('MISSING_FALLBACK_CREDENTIAL')
                config['workers'][target] = {'generation': 1, 'token': secrets.token_urlsafe(48)}
            _atomic_write(self.env, update_env_text(self.env.read_text(), target))
            config['dispatch_node_ip'] = target
            _atomic_write(self.lifecycle, json.dumps(config, indent=2) + '\n')
            transaction['phase'] = 'committed'
            _atomic_write(self.transaction_path, json.dumps(transaction))
            # The transaction contains no secrets; retain last transition for auditing.
            _atomic_write(self.state / 'last-transition.json', json.dumps(transaction))
            self.transaction_path.unlink()
            return {**self.snapshot(), 'changed': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('snapshot')
    for command in ('select', 'retry-worker'):
        selector = commands.add_parser(command)
        selector.add_argument('--target', choices=NODES, required=True)
    args = parser.parse_args(argv)
    try:
        manager = PlacementManager(RayPlacement())
        result = manager.snapshot() if args.command == 'snapshot' else manager.select(args.target, retry_worker=args.command == 'retry-worker')
    except Exception as exc:
        code = str(exc) if isinstance(exc, PlacementError) else 'PLACEMENT_OPERATION_FAILED'
        print(json.dumps({'ok': False, 'error': code}))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
