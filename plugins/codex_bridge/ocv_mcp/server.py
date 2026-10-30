"""Official-SDK stdio server with lazy operation discovery and durable submits."""
import argparse
import asyncio
import base64
import json
import logging
from pathlib import Path

from mcp.server.lowlevel import Server
from mcp.server.lowlevel.helper_types import ReadResourceContents
from mcp.server.stdio import stdio_server
from mcp import types

from . import VERSION
from .adapter import Adapter, clean
from .contracts import object_schema, OperationError
from .guidance import BOOTSTRAP, TOPICS, read_guide

# Keep the legacy resource/constant while initialization stays deliberately small.
GUIDE = BOOTSTRAP


def tool(name, description, properties=None, required=None, *, read=True, destructive=False):
    return types.Tool(name=name, description=description,
                      inputSchema=object_schema(properties, required),
                      annotations=types.ToolAnnotations(readOnlyHint=read,
                          destructiveHint=destructive, idempotentHint=read, openWorldHint=True))


STRING = {'type': 'string', 'minLength': 1, 'maxLength': 200}
ARGUMENTS = {'type': 'object', 'description': '严格遵循 ocv_describe 的 input_schema。'}
TOOLS = [
    tool('ocv_guide', '新对话先读 start：面向 AI 的 OCV 能力与操作说明。正文只在 content 文本中返回一次，structuredContent 仅含章节索引等元数据；不连接后端、不改项目。',
         {'topic': {'type': 'string', 'enum': [*TOPICS, 'all'], 'default': 'start'}}),
    tool('ocv_connection', '检查连接、身份、可用权限与版本兼容性；刷新操作目录。初次接手请先读 ocv_guide。'),
    tool('ocv_upload_preflight', '独立 MCP 工具，直接调用，不是 operation。只检查本地文件的上传授权、类型和大小，不上传也不创建计划。',
         {'local_path': {'type': 'string', 'minLength': 1, 'maxLength': 4096}}, ['local_path']),
    tool('ocv_operations', '按制作环节列出可用操作，不一次加载所有契约。',
         {'group': {'type': 'string', 'maxLength': 80}, 'offset': {'type': 'integer', 'minimum': 0},
          'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}),
    tool('ocv_describe', '取得 ocv_operations 列出操作的实时参数 JSON Schema、含义及权限。独立工具（如 ocv_upload_preflight）请直接调用，不传作 operation。', {'operation': STRING}, ['operation']),
    tool('ocv_read', '执行白名单只读/预检操作。素材下载会写入本机 MCP 缓存，不改项目。',
         {'operation': STRING, 'arguments': ARGUMENTS}, ['operation', 'arguments']),
    tool('ocv_prepare', '预览并保存待执行计划，不提交制作操作；同一意图重试必须沿用 request_id。',
         {'operation': STRING, 'arguments': ARGUMENTS,
          'request_id': {'type': 'string', 'pattern': '^[A-Za-z0-9_-]{8,100}$'}},
         ['operation', 'arguments', 'request_id'], read=False),
    tool('ocv_commit', '提交已审核计划；生成/删除需用户明确授权后 confirmed=true。立刻返回，请用 receipt 查结果。',
         {'plan_id': STRING, 'confirmed': {'type': 'boolean', 'default': False}}, ['plan_id'],
         read=False, destructive=True),
    tool('ocv_receipt', '查询同一计划的持久回执，不重复提交；之后查询原生任务进度。', {'plan_id': STRING}, ['plan_id']),
    tool('ocv_discard', '丢弃尚未提交计划；不是停止正在运行的制作任务。', {'plan_id': STRING}, ['plan_id'], read=False),
    tool('ocv_result', '按 JSON pointer 读取大结果；列表按 offset/limit 分页，不丢弃最终文案与时间轴。',
         {'result_id': STRING, 'pointer': {'type': 'string', 'maxLength': 1000},
          'offset': {'type': 'integer', 'minimum': 0}, 'limit': {'type': 'integer', 'minimum': 1, 'maximum': 100}}, ['result_id']),
    tool('ocv_preview_asset', '内联读取已下载的图片/音频（最多 8 MB）；大视频使用下载回执中的文件。',
         {'asset_id': STRING}, ['asset_id']),
]


def create_server(adapter):
    server = Server('ocv-production', version=VERSION, instructions=GUIDE)

    @server.list_tools()
    async def list_tools():
        return TOOLS

    @server.call_tool()
    async def call_tool(name, arguments):
        try:
            if name == 'ocv_guide':
                result = await asyncio.to_thread(read_guide, arguments.get('topic', 'start'))
                # Some clients display both channels. Keep the full Markdown in
                # exactly one text block, not duplicated inside JSON metadata.
                metadata = {key: value for key, value in result.items() if key != 'content'}
                metadata['content_location'] = 'content[0].text'
                return types.CallToolResult(content=[types.TextContent(type='text', text=result['content'])],
                                            structuredContent=metadata)
            if name == 'ocv_preview_asset':
                path, mime = await asyncio.to_thread(adapter.asset, arguments['asset_id'])
                data = base64.b64encode(path.read_bytes()).decode('ascii')
                if mime.startswith('image/'):
                    return types.CallToolResult(content=[types.ImageContent(type='image', data=data, mimeType=mime)])
                if mime.startswith('audio/'):
                    return types.CallToolResult(content=[types.AudioContent(type='audio', data=data, mimeType=mime)])
                return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(
                    {'path': str(path), 'mime_type': mime, 'note': '请使用本地播放器预览'}, ensure_ascii=False))])
            method = {'ocv_connection': adapter.status, 'ocv_operations': adapter.operations,
                      'ocv_upload_preflight': adapter.upload_preflight,
                      'ocv_describe': adapter.describe, 'ocv_read': adapter.read,
                      'ocv_prepare': adapter.prepare, 'ocv_commit': adapter.commit,
                      'ocv_receipt': adapter.receipt, 'ocv_discard': adapter.discard,
                      'ocv_result': adapter.result}.get(name)
            if method is None:
                raise ValueError('未知工具')
            result = await asyncio.to_thread(method, **arguments)
            return types.CallToolResult(content=[types.TextContent(type='text', text=json.dumps(result, ensure_ascii=False))],
                                        structuredContent=result)
        except OperationError as exc:
            return types.CallToolResult(content=[types.TextContent(type='text', text=clean(str(exc)))],
                                        structuredContent={'error': clean(exc.detail)}, isError=True)
        except Exception as exc:
            error = clean(str(exc))
            return types.CallToolResult(content=[types.TextContent(type='text', text=error)], isError=True)

    @server.list_resources()
    async def list_resources():
        chapters = read_guide()['topics']
        return [types.Resource(uri='ocv://guide/workflow', name='OCV 制作流程指南（兼容入口）', mimeType='text/plain'),
                *[types.Resource(uri=item['uri'], name='OCV AI：' + item['title'], mimeType='text/markdown')
                  for item in chapters]]

    @server.read_resource()
    async def read_resource(uri):
        value = str(uri)
        if value == 'ocv://guide/workflow':
            return [ReadResourceContents(content=read_guide('all')['content'], mime_type='text/plain')]
        if value.startswith('ocv://guide/'):
            return [ReadResourceContents(content=read_guide(value.removeprefix('ocv://guide/'))['content'],
                                         mime_type='text/markdown')]
        if value.startswith('ocv://assets/'):
            path, mime = await asyncio.to_thread(adapter.asset, value.removeprefix('ocv://assets/'))
            return [ReadResourceContents(content=path.read_bytes(), mime_type=mime)]
        raise ValueError('未知资源')

    return server


async def serve(adapter):
    server = create_server(adapter)
    async with stdio_server() as (reader, writer):
        await server.run(reader, writer, server.create_initialization_options())


def main():
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description='OCV local production MCP (stdio)')
    parser.add_argument('--base-url', default='http://127.0.0.1:8010')
    parser.add_argument('--cookie-file', type=Path)
    parser.add_argument('--state-dir', type=Path, default=root / 'workspace/codex_bridge/mcp')
    parser.add_argument('--allow-upload-root', type=Path, action='append', default=[])
    parser.add_argument('--allow-upload-file', type=Path, action='append', default=[],
                        help='Authorize one user-selected attachment, not its containing directory')
    parser.add_argument('--read-only', action='store_true')
    parser.add_argument('--allow-generation', action='store_true')
    parser.add_argument('--allow-delete', action='store_true')
    parser.add_argument('--check', action='store_true', help='Read-only connectivity check, not stdio service')
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING)  # stderr only; stdout belongs to MCP.
    adapter = Adapter(args.base_url, args.state_dir, cookie_file=args.cookie_file,
                      upload_roots=args.allow_upload_root, upload_files=args.allow_upload_file, read_only=args.read_only,
                      allow_generation=args.allow_generation, allow_delete=args.allow_delete)
    if args.check:
        try:
            result = adapter.status()
        except Exception as exc:
            print(json.dumps({'connected': False, 'error': clean(str(exc)),
                              'advice': '请先启动 OCV 并启用制作桥；账号模式还需指定有效 cookie-file。'},
                             ensure_ascii=False, indent=2))
            raise SystemExit(1)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        asyncio.run(serve(adapter))


if __name__ == '__main__':
    main()
