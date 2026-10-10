"""OCV production bridge client, including explicitly confirmed image redraw."""
import argparse
import hashlib
import http.cookiejar
import json
import mimetypes
from pathlib import Path
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8010')
    parser.add_argument('--cookie-file', help='Existing Netscape cookie file, if login is required')
    parser.add_argument('--out', help='Save JSON to this file; print only a compact receipt')
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('info', 'guide', 'schema', 'projects', 'drafts', 'audio-tasks'):
        sub.add_parser(name)
    for name in ('draft-save', 'upload'):
        sub.add_parser(name).add_argument('file')
    for name in ('draft-get', 'pack', 'scene-assets', 'generation-settings-get'):
        sub.add_parser(name).add_argument('id')
    p = sub.add_parser('image-status'); p.add_argument('id')
    p.add_argument('--request-id'); p.add_argument('--batch-id')
    for name in ('generation-settings-patch', 'image-batch-control'):
        p = sub.add_parser(name); p.add_argument('id'); p.add_argument('file')

    for name in ('draft-archive', 'draft-restore'):
        p = sub.add_parser(name); p.add_argument('id'); p.add_argument('--revision', type=int, required=True)
    p = sub.add_parser('from-audio')
    p.add_argument('job_id'); p.add_argument('--confirmed', action='store_true', required=True)
    for name in ('validate', 'apply', 'patch-validate', 'patch-apply', 'scene-validate', 'scene-apply', 'image-validate', 'image-apply', 'image-batch-validate', 'image-batch-apply'):
        p = sub.add_parser(name); p.add_argument('id'); p.add_argument('file')
        if name in ('scene-apply', 'image-apply', 'image-batch-apply'):

            p.add_argument('--confirmed', action='store_true', required=True,
                           help='Confirm the previously inspected affected-shot list')
    args = parser.parse_args(argv)
    base = args.base_url.rstrip('/')
    parsed = urllib.parse.urlsplit(base)
    if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or parsed.query or parsed.fragment:
        parser.error('base-url must be an HTTP(S) origin without credentials/query')
    jar = http.cookiejar.MozillaCookieJar(args.cookie_file) if args.cookie_file else http.cookiejar.CookieJar()
    if args.cookie_file:
        jar.load(ignore_discard=True)
    handlers = [urllib.request.HTTPCookieProcessor(jar)]
    if parsed.hostname in ('127.0.0.1', 'localhost', '::1'):
        handlers.append(urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)

    def call(path, payload=None, method=None, body=None, headers=None):
        headers = dict(headers or {})
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode('utf-8')
            headers['Content-Type'] = 'application/json'
        req = urllib.request.Request(base + path, data=body, headers=headers, method=method)
        with opener.open(req, timeout=90) as response:
            return json.load(response)

    prefix = '/api/codex-bridge'
    try:
        info = call(prefix + '/info')  # Authenticate/check plugin before uploads too.
        if args.command == 'info':
            result = info
        elif args.command in ('guide', 'schema', 'projects', 'drafts', 'audio-tasks'):
            result = call(prefix + '/' + args.command)
        elif args.command == 'upload':
            path = Path(args.file)
            if not path.is_file() or path.stat().st_size > 100 * 1024 * 1024:
                parser.error('Upload must be an existing file no larger than 100 MB')
            boundary = uuid.uuid4().hex
            filename = 'reference' + path.suffix.lower()
            header = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
                      f'Content-Type: {mimetypes.guess_type(filename)[0] or "application/octet-stream"}\r\n\r\n')
            body = header.encode() + path.read_bytes() + f'\r\n--{boundary}--\r\n'.encode()
            result = call('/api/editor/uploads', method='POST', body=body,
                          headers={'Content-Type': 'multipart/form-data; boundary=' + boundary})
        elif args.command == 'draft-save':
            result = call(prefix + '/drafts', json.loads(Path(args.file).read_text(encoding='utf-8-sig')), 'PUT')
        elif args.command == 'draft-get':
            result = call(prefix + '/drafts/' + urllib.parse.quote(args.id, safe=''))
        elif args.command in ('draft-archive', 'draft-restore'):
            result = call(prefix + '/drafts/' + urllib.parse.quote(args.id, safe='') + '/archive',
                          {'revision': args.revision, 'archived': args.command == 'draft-archive'}, 'PUT')
        elif args.command == 'from-audio':
            result = call(prefix + '/from-audio', {'job_id': args.job_id, 'audio_confirmed': args.confirmed}, 'POST')
        elif args.command == 'pack':
            result = call(prefix + '/projects/' + urllib.parse.quote(args.id, safe='') + '/pack')
        elif args.command == 'scene-assets':
            result = call(prefix + '/projects/' + urllib.parse.quote(args.id, safe='') + '/scene-assets')
        elif args.command == 'image-status':
            query = urllib.parse.urlencode({k: v for k, v in {'request_id': args.request_id, 'batch_id': args.batch_id}.items() if v})
            result = call(prefix + '/projects/' + urllib.parse.quote(args.id, safe='') + '/image-status' + ('?' + query if query else ''))
        elif args.command == 'generation-settings-get':
            result = call(prefix + '/projects/' + urllib.parse.quote(args.id, safe='') + '/generation-settings')
        elif args.command in ('generation-settings-patch', 'image-batch-control'):
            payload = json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
            endpoint = 'generation-settings' if args.command == 'generation-settings-patch' else args.command
            result = call(prefix + '/projects/' + urllib.parse.quote(args.id, safe='') + '/' + endpoint,
                          payload, 'PATCH' if endpoint == 'generation-settings' else 'POST')
        elif args.command in ('image-validate', 'image-apply', 'image-batch-validate', 'image-batch-apply'):
            payload = json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
            path = prefix + '/projects/' + urllib.parse.quote(args.id, safe='')
            if args.command in ('image-apply', 'image-batch-apply'):

                payload['confirmed'] = args.confirmed
            result = call(path + '/' + args.command, payload, 'POST')
        else:
            payload = json.loads(Path(args.file).read_text(encoding='utf-8-sig'))
            path = prefix + '/projects/' + urllib.parse.quote(args.id, safe='')
            operation = 'scene-' if args.command.startswith('scene-') else 'patch-' if args.command.startswith('patch-') else ''
            result = call(path + '/' + operation + 'validate', payload, 'POST')
            if args.command in ('apply', 'patch-apply', 'scene-apply') and result.get('ok'):
                if args.command == 'scene-apply':
                    # Never silently confirm a changed preview on behalf of the user.
                    if payload.get('confirmation_token') != result.get('confirmation_token'):
                        raise RuntimeError('Scene preview changed or token missing; run scene-validate and inspect impacts first')
                    payload['confirmed'] = args.confirmed
                result = call(path + '/' + operation + 'apply', payload, 'POST')
                pack = call(path + '/pack')
                if pack['revision'] != result['revision']:
                    raise RuntimeError('Project changed after import; reread pack')
                if args.command == 'scene-apply':
                    state = hashlib.sha256(json.dumps(pack.get('scene_assets', []), ensure_ascii=False,
                        sort_keys=True, separators=(',', ':')).encode()).hexdigest()
                    if state != result.get('scene_state_token'):
                        raise RuntimeError('Scene readback differs from applied proposal; reread pack')
                result['readback_verified'] = True
        if args.out:
            out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print(json.dumps({'saved': str(out.resolve()), 'ok': result.get('ok', True),
                              'id': result.get('id', result.get('project_id')), 'revision': result.get('revision'),
                              'items': len(result.get('items', [])), 'shots': len(result.get('shots', [])),
                              'changed': result.get('changed', []), 'impacts': result.get('impacts', []),
                              'written': result.get('written', False), 'readback_verified': result.get('readback_verified', False),
                              'errors': result.get('errors', [])}, ensure_ascii=False))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get('ok', True) else 2
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read().decode()).get('detail', f'HTTP {exc.code}')
        except (ValueError, UnicodeError):
            detail = f'HTTP {exc.code}'
        failure = {'ok': False, 'status': exc.code, 'error': detail}
        if args.out:
            out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2
    except (OSError, ValueError, RuntimeError) as exc:
        failure = {'ok': False, 'error': '本地请求失败（' + type(exc).__name__ + '），请检查连接、文件和请求格式。'}
        if args.out:
            out = Path(args.out); out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps(failure, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(failure, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    if hasattr(sys.stdout, 'reconfigure'):
        sys.stdout.reconfigure(encoding='utf-8'); sys.stderr.reconfigure(encoding='utf-8')
    raise SystemExit(main())
