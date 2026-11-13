"""Local desktop request boundary and private workspace protection."""
import ipaddress
from pathlib import PurePosixPath
from urllib.parse import urlparse

from fastapi import Request
from starlette.responses import JSONResponse
from starlette.staticfiles import StaticFiles

from .auth import local_auth_enabled, require_user


def loopback(host):
    if host == 'localhost':
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except (ValueError, TypeError):
        return False


async def local_request_boundary(request, call_next):
    if local_auth_enabled():
        # Reject LAN/public access and DNS-rebinding hosts even in no-login mode.
        peer = request.client.host if request.client else ''
        if not loopback(peer) or not loopback(request.url.hostname):
            return JSONResponse({'detail': '本地模式只允许本机访问；远程使用请启用账号模式'}, status_code=403)
        origin = request.headers.get('origin')
        if origin and not loopback(urlparse(origin).hostname):
            return JSONResponse({'detail': '不允许外部网页访问本地工作台'}, status_code=403)
        if request.headers.get('sec-fetch-site') == 'cross-site':
            return JSONResponse({'detail': '不允许跨站访问本地工作台'}, status_code=403)
    return await call_next(request)


class WorkspaceMediaFiles(StaticFiles):
    async def __call__(self, scope, receive, send):
        request = Request(scope)
        try:
            user = require_user(request)
        except Exception as exc:
            from fastapi import HTTPException
            if not isinstance(exc, HTTPException):
                raise
            return await JSONResponse({'detail': exc.detail}, status_code=exc.status_code)(scope, receive, send)
        path = PurePosixPath(self.get_path(scope))
        allowed = {'.png', '.jpg', '.jpeg', '.webp', '.gif', '.avif', '.mp4', '.webm', '.mov', '.wav', '.mp3', '.m4a', '.ogg', '.flac', '.srt', '.vtt', '.pdf'}
        if any(p.startswith('.') or p in {'settings', 'codex_bridge_backups'} for p in path.parts) or path.suffix.lower() not in allowed:
            return await JSONResponse({'detail': '工作区内部文件不可直接访问'}, status_code=403)(scope, receive, send)
        # Account mode must not serve another user's studio media.
        if not local_auth_enabled():
            parts = path.parts
            if len(parts) < 3 or parts[0] not in {'video_studio', 'image_studio'} or parts[1] != str(user['id']):
                return await JSONResponse({'detail': '请通过项目授权下载接口访问此文件'}, status_code=403)(scope, receive, send)
        return await super().__call__(scope, receive, send)
