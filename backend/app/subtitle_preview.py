"""Local, non-billable subtitle frame preview; no generated image or video task."""
import base64
import html
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import require_user
from .subtitle_layout import normalize, css, wrap, write_ass

router = APIRouter()


class PreviewRequest(BaseModel):
    video_orientation: str = 'landscape'
    subtitle_layouts: dict = Field(default_factory=dict)
    video_render_variant: str = 'both'
    text: str = Field(default='从一段文字开始，让每个想法被看见。', max_length=160)


@router.post('/api/subtitle-style-preview')
def preview(payload: PreviewRequest, request: Request):
    require_user(request)
    from module5_video_render import find_portable_hyperframes_browser, require_ffmpeg_binary, _subtitle_filter_path
    layout = normalize(payload.model_dump())
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with tempfile.TemporaryDirectory(prefix='ocv-subtitle-preview-') as directory:
        root = Path(directory)
        output = root / 'preview.png'
        srt = root / 'preview.srt'
        srt.write_text('1\n00:00:00,000 --> 00:00:05,000\n'+payload.text+'\n', encoding='utf-8')
        ass = write_ass(srt, root / 'preview.ass', layout)
        engine = 'FFmpeg / libass'
        try:
            if os.getenv('VIDEO_RENDER_ENGINE', 'ffmpeg').lower() == 'hyperframes' and payload.video_render_variant == 'subtitles':
                raise RuntimeError('browser renderer selected')
            subprocess.run([str(require_ffmpeg_binary()), '-y', '-f', 'lavfi', '-i',
                f"color=c=0x050a12:s={layout['width']}x{layout['height']}:d=1",
                '-vf', f"ass=filename='{_subtitle_filter_path(ass)}'", '-frames:v', '1', str(output)],
                check=True, capture_output=True, timeout=25, creationflags=flags)
        except (RuntimeError, OSError, subprocess.SubprocessError):
            browser = find_portable_hyperframes_browser()
            if not browser:
                raise HTTPException(503, '真实预览需要便携 FFmpeg 或 Chromium 浏览器；上方示意预览仍可使用。')
            engine = 'Hyperframes / Chromium'
            page = root / 'preview.html'
            page.write_text('<!doctype html><html><head><meta charset="utf-8"><style>'
                '*{box-sizing:border-box;margin:0}body{background:#050a12}#subtitle-overlay{position:absolute;display:flex;justify-content:center;text-align:center}.subtitle-inner{font-weight:600;border-radius:8px}'
                +css(layout)+'</style></head><body><div id="subtitle-overlay"><div class="subtitle-inner">'
                +html.escape(wrap(payload.text, layout))+'</div></div></body></html>', encoding='utf-8')
            try:
                project = Path(__file__).resolve().parents[2]
                portable_node = project / 'runtime' / 'node' / ('node.exe' if os.name == 'nt' else 'bin/node')
                node = str(portable_node) if portable_node.is_file() else shutil.which('node')
                if not node:
                    raise OSError('Node not available')
                subprocess.run([node, str(Path(__file__).with_suffix('.cjs')), str(browser),
                    str(page), str(output), str(layout['width']), str(layout['height'])],
                    check=True, capture_output=True, timeout=35, creationflags=flags, cwd=project)
            except (OSError, subprocess.SubprocessError):
                raise HTTPException(503, '浏览器预览未能完成，请运行 Launcher 环境体检。')
        if not output.is_file():
            raise HTTPException(503, '未生成预览帧，请运行环境体检。')
        return {'image': 'data:image/png;base64,'+base64.b64encode(output.read_bytes()).decode(),
                'engine': engine, 'width': layout['width'], 'height': layout['height']}
