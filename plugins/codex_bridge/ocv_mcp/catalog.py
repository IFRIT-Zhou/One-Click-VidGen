"""Reviewed production allowlist; never an arbitrary HTTP or shell proxy."""
from dataclasses import dataclass


@dataclass(frozen=True)
class Operation:
    name: str
    method: str
    path: str
    access: str
    description: str
    transfer: str = ''


OPERATIONS = []


def group(prefix, base, rows):
    for row in rows.strip().splitlines():
        name, method, suffix, access, description, *transfer = row.split('|')
        OPERATIONS.append(Operation('ocv_' + prefix + name, method, base + suffix,
                                    access, description, *transfer))


group('', '/api', '''
runtime|GET|/runtime-state|read|读取 OCV 当前运行状态。
creation_options|GET|/settings|read|读取可用本地音色、情绪与创作模式；仅返回公开制作选项。
presets|GET|/parameter-presets|read|读取参数预设。
preset_get|GET|/parameter-presets/{name}|read|读取指定参数预设。
preset_save|PUT|/parameter-presets|edit|保存参数预设。
preset_delete|DELETE|/parameter-presets/{name}|delete|删除指定参数预设。
image_profiles|GET|/image-profiles|read|读取已配置的图片模型，密钥不输出。
video_model|GET|/video-model|read|读取视频 API 模型能力和参数，不输出密钥。
comfyui_profiles|GET|/comfyui|read|读取可用 ComfyUI 工作流及连接概要，不启动或停止引擎。
language_presets|GET|/language-presets|read|读取语言模型预设，不输出密钥。
agent_presets|GET|/agent-prompt-presets|read|读取 Agent 提示词预设。
agent_preset_get|GET|/agent-prompt-presets/{name}|read|读取指定 Agent 提示词预设。
agent_preset_save|PUT|/agent-prompt-presets|edit|保存 Agent 提示词预设。
director_profiles|GET|/director-profiles|read|读取导演和内容风格预设。
subtitle_fonts|GET|/subtitle-fonts|read|读取可用字幕字体。
upload|POST|/editor/uploads|edit|上传用户授权文件，返回 asset_id；只允许配置的上传目录。|upload
uploads|GET|/editor/uploads|read|读取已上传素材。
uploaded_file|GET|/editor/uploads/{filename}|read|下载当前用户已上传的素材用于检查。|download
reference_analyze|POST|/editor/reference-analysis/{filename}|generate|调用视觉语言模型分析已上传参考图用途，可能收费。
cloud_voices|GET|/cloud/voices|read|读取集群可用音色。
cloud_voice|GET|/cloud/voices/{voice_id}|read|读取指定集群音色状态。
cloud_voice_audio|GET|/cloud/voices/{voice_id}/audio|read|下载音色参考音频。|download
cloud_voice_create|POST|/cloud/voices|generate|上传用户有权使用的参考音色，可能收费。|upload
cloud_capabilities|GET|/video-studio/cloud/capabilities|read|读取集群视频能力与限制。
cloud_quote|POST|/cloud/quote|read|查询集群报价，不提交生成。
cloud_jobs|GET|/cloud/jobs|read|读取当前用户云端任务。
cloud_job|GET|/cloud/jobs/{cloud_job_id}|read|查询云端任务进度。
cloud_cancel|POST|/cloud/jobs/{cloud_job_id}/cancel|edit|请求取消云端任务；以返回状态为准，不保证上游立即停止。
''')

group('job_', '/api/jobs', '''
preflight|POST|/preflight|read|检查新任务参数和依赖，不开始生成。
create|POST||generate|创建并启动图文或动态任务，可能收费；推荐 step_mode=true 分阶段审核。
list|GET||read|列出当前用户图文和配音任务。
get|GET|/{job_id}|read|读取任务阶段、参数、错误与成果。
cancel|POST|/{job_id}/cancel|edit|请求停止任务；回执不代表上游已停止。
delete|DELETE|/{job_id}|delete|删除指定任务，需明确确认。
resume|POST|/{job_id}/resume|generate|恢复指定任务，可能继续付费生成。
advance|POST|/{job_id}/step-workflow/advance|generate|确认并推进图文/动态阶段；先审核当前成果，可能生成素材。
subtitles|GET|/{job_id}/step-workflow/subtitles|read|读取最终字幕和时间轴。
subtitles_save|POST|/{job_id}/step-workflow/subtitles|edit|保存阶段字幕，沿用原生同步逻辑。
retry_tts|POST|/{job_id}/retry-tts|generate|重试配音阶段，可能收费。
logs|GET|/{job_id}/logs|read|读取日志，核对实际模型渠道、进度与错误。
assets|GET|/{job_id}/assets|read|读取任务素材清单。
artifact|GET|/{job_id}/artifacts/{filename}|read|下载指定成片、字幕等成果到 MCP 缓存。|download
subtitle_video|GET|/{job_id}/subtitle-rendered-video|read|下载字幕成片。|download
subtitle_render|POST|/{job_id}/subtitle-render|generate|按字幕样式与 BGM 渲染字幕视频。
''')

group('tts_', '/api/jobs/{job_id}/tts-editor', '''
get|GET||read|读取逐句配音、显示字幕、朗读文本和编辑版本；两种文本不能混用。
status|GET|/status|read|查询配音编辑和重配进度。
audio|GET|/audio/{index}|read|下载指定句最新音频用于试听。|download
pending_audio|GET|/pending-audio/{index}|read|试听保留但尚未采用的新配音。|download
adopt_estimated|POST|/adopt-estimated|edit|采用保留音频及估算对齐；先说明对齐风险。
regenerate|POST|/regenerate|generate|重配选中句；tts_text_overrides 修正朗读，字幕只按显式 subtitle_text_overrides 修改。
pause|POST|/pause|edit|保存句末额外停顿。
rebuild|POST|/rebuild-audio|edit|复用已有配音重新合成，不调用 TTS；仅同步明确指定的字幕修改。
subtitle_only|POST|/subtitle-only|edit|仅保存显示字幕，保留朗读、音频及其时长。
undo|POST|/undo|edit|撤销配音编辑，按原生规则恢复时间轴。
token_count|POST|/token-count|read|检查朗读文本 token 数，不生成。
suggest_boundaries|POST|/suggest-boundaries|generate|调用断句模型给出建议，不直接重配；可能收费。
resegment|POST|/resegment|generate|调整或新增交界并局部重配；sync_text=true，文字修改同步字幕、文案和时间轴。
''')

group('visual_', '/api/jobs/{job_id}/visual-editor', '''
get|GET||read|读取图文画面、字幕分组和编辑状态。
status|GET|/status|read|查询图文重绘和渲染状态。
redraw|POST|/{macro_id}/redraw|generate|按提示词与参考图重绘单张画面，可能收费。
replace|POST|/{macro_id}/upload|edit|用授权本地图片替换画面。|upload
undo|POST|/{macro_id}/undo|edit|恢复上一版画面。
reset_prompt|POST|/{macro_id}/reset-prompt|edit|恢复初始提示词。
commit|POST|/{macro_id}/commit-baseline|edit|保存该画面和提示词为确认版本。
commit_all|POST|/commit-all-baselines|edit|确认全部图文画面。
timing|POST|/{macro_id}/timing|edit|按字幕单位扩展或收缩画面范围。
timing_reset|POST|/timing/reset|edit|恢复画面时序。
timing_commit|POST|/timing/commit|edit|确认当前画面时序。
timing_history|POST|/timing/history|edit|恢复指定时序历史。
remove|POST|/{macro_id}/timing/remove|delete|移除画面并按原生规则合并字幕归属。
insert|POST|/timing/insert|edit|在字幕交界插入画面。
subtitles|POST|/subtitles|edit|保存图文字幕修改。
subtitle_split|POST|/subtitles/split|edit|按有效时间边界拆分字幕。
subtitle_history|POST|/subtitles/history|edit|恢复字幕历史。
boundary_preview|POST|/subtitles/boundary-preview|read|获取字幕交界试听信息，不修改时间轴。
boundary|POST|/subtitles/boundary|edit|调整字幕时间交界。
subtitle_hide|POST|/subtitles/hide|edit|隐藏或合并字幕显示，保留音频。
subtitle_restore|POST|/subtitles/restore-hidden|edit|恢复隐藏字幕。
audio|GET|/audio|read|下载最新完整配音。|download
render|POST|/render|generate|按现有图片、最终配音、字幕和 BGM 渲染图文成片，不重绘图片。
cancel|POST|/cancel|edit|请求停止图文重新渲染。
''')
group('visual_', '/api', '''
projects|GET|/visual-editor/projects|read|列出可编辑图文项目。
image|GET|/jobs/{job_id}/visual-images/{filename}|read|读取图文画面用于审核。|download
scene_assets|GET|/jobs/{job_id}/scene-assets|read|读取图文场景参考资产。
scene_image|GET|/jobs/{job_id}/scene-reference/{macro_id}|read|读取图文画面的场景参考图。|download
reference_image|GET|/jobs/{job_id}/reference-material/{macro_id}/{index}|read|读取图文画面绑定的单张参考素材。|download
bgm|GET|/jobs/{job_id}/visual-bgm/{filename}|read|下载当前图文任务的背景音乐用于试听。|download
''')

group('video_', '/api/video-studio', '''
list|GET||read|列出动态项目。
create|POST||edit|从字幕创建动态草案，不自动生成。
sources|GET|/sources|read|列出可导入的配音来源。
from_project|POST|/from-project|edit|从既有配音任务创建动态项目。
by_audio|GET|/by-audio-task/{job_id}|read|按配音任务查询动态项目。
get|GET|/{identity}|read|读取动态项目、镜头、最终字幕、任务状态和 revision。
edit|PUT|/{identity}|edit|保存完整镜头方案；局部修改优先使用 bridge_patch_apply。
settings|POST|/{identity}/settings|edit|保存本项目设置，不改全局默认。
image_route|POST|/{identity}/image-route|edit|切换本项目图片渠道，之后重试使用新渠道。
language_route|POST|/{identity}/language-route|edit|切换本项目规划语言模型渠道。
sync_audio|POST|/{identity}/sync-audio|edit|同步最终配音字幕时间轴；保留素材并标记受影响镜头。
confirm_audio_timing|POST|/{identity}/shots/{shot_id}/confirm-audio-timing|edit|审核后确认受配音变更影响的画面。
audio|GET|/{identity}/audio|read|下载当前项目完整配音。|download
structure|POST|/{identity}/structure|edit|沿字幕边界拆合、插入或调整分镜。
auto_split|POST|/{identity}/shots/{shot_id}/auto-split|generate|用 Agent 1C 自动拆分，可包含相邻镜头；不出图/视频，可能调用付费语言模型。
structure_undo|POST|/{identity}/structure/undo|edit|恢复拆分前结构与保留素材。
design_confirm|POST|/{identity}/design/confirm|edit|确认分镜设计修改。
review|POST|/{identity}/review|generate|审核提交分镜方案，可能开始核心图生成；先核对方案。
images_generate|POST|/{identity}/images/generate|generate|生成核心分镜图，可能收费。
images_stop|POST|/{identity}/images/stop|edit|请求停止核心图生成，上游任务可能仍运行。
scene_generate|POST|/{identity}/scene-references/generate|generate|生成所需场景参考图，可能收费。
image|GET|/{identity}/images/{shot_id}|read|读取核心分镜图。|download
reference_upload|POST|/{identity}/redraw-references|edit|上传额外分镜参考图。|upload
reference_image|GET|/{identity}/redraw-references/{reference_id}|read|读取新增的动态分镜参考图。|download
reference_remove|DELETE|/{identity}/redraw-references/{reference_id}|delete|移除指定参考图，不绕过真实引用校验。
reference_clear|DELETE|/{identity}/redraw-references|delete|清除新增参考图，需确认范围。
redraw|POST|/{identity}/images/{shot_id}/redraw|generate|重绘指定核心图，可能收费。
image_replace|POST|/{identity}/images/{shot_id}/upload|edit|用授权图片替换核心图。|upload
image_undo|POST|/{identity}/images/{shot_id}/undo|edit|恢复上一版核心图。
reset_prompt|POST|/{identity}/images/{shot_id}/reset-prompt|edit|恢复初始提示词。
motion|POST|/{identity}/shots/{shot_id}/motion|edit|切换动静类型或修改动态方案。
refresh_prompts|POST|/{identity}/shots/{shot_id}/refresh-prompts|generate|按当前字幕重新规划单镜提示词，可能收费。
reopen|POST|/{identity}/shots/{shot_id}/reopen|edit|打开单镜返修。
images_confirm|POST|/{identity}/images/confirm|edit|用户审图后确认核心图并进入动态制作。
plan|POST|/{identity}/plan|generate|按最终配音文案时间轴重新规划画面，可能收费。
stop|POST|/{identity}/stop|edit|请求停止当前规划。
options_shot|PUT|/{identity}/shots/{shot_id}/generation-options|edit|保存单镜渠道、工作流、尺寸和生成参数。
options_all|PUT|/{identity}/generation-options|edit|批量应用动态镜头参数，不自动生成。
generate|POST|/{identity}/videos/generate|generate|提交动态视频生成，可能收费。
queue_resume|POST|/{identity}/videos/queue/resume|generate|恢复动态队列，可能收费。
queue_cancel|POST|/{identity}/videos/{shot_id}/cancel-queue|edit|取消指定镜头排队。
generation_stop|POST|/{identity}/videos/stop|edit|请求停止动态生成；查询原生状态确认停止结果。
clip|GET|/{identity}/videos/{shot_id}|read|下载当前镜头视频用于预览。|download
history_clip|GET|/{identity}/videos/{shot_id}/history/{index}|read|下载历史镜头视频。|download
history_adopt|POST|/{identity}/videos/{shot_id}/history/{index}/adopt|edit|采用历史视频版本。
history_delete|DELETE|/{identity}/videos/{shot_id}/history/{index}|delete|删除指定视频历史版本。
clip_replace|POST|/{identity}/videos/{shot_id}/upload|edit|用授权本地视频替换镜头。|upload
layout_preview|POST|/{identity}/layout-preview|read|获取字幕布局预览。|download
export|POST|/{identity}/export|generate|按最终时间轴合成现有素材，裁切或减速，不重新生成动态镜头。
export_file|GET|/{identity}/export/{variant}|read|下载 raw 或 subtitles 成片。|download
''')

group('bridge_', '/api/codex-bridge', '''
info|GET|/info|read|查询旧制作桥能力和版本。
drafts|GET|/drafts|read|列出制作桥草稿。
draft|GET|/drafts/{identity}|read|读取指定草稿。
draft_save|PUT|/drafts|edit|填写或修改初始草稿。
draft_archive|PUT|/drafts/{identity}/archive|edit|归档或恢复草稿。
audio_tasks|GET|/audio-tasks|read|读取可交接配音任务。
from_audio|POST|/from-audio|edit|从用户已确认配音导入制作桥。
pack|GET|/projects/{identity}/pack|read|读取紧凑规划包、最终字幕、参考素材及版本。
shot|GET|/projects/{identity}/shots/{shot_id}|read|只读取单镜资料，减少上下文。
references|GET|/projects/{identity}/shots/{shot_id}/references-preview|read|核查实际附图顺序、素材身份和提示词图号。
reference_image|GET|/projects/{identity}/references/{reference_id}|read|读取绑定参考图用于审核。|download
shot_image|GET|/projects/{identity}/shots/{shot_id}/image|read|读取单镜当前图片用于审核。|download
scene_image|GET|/projects/{identity}/scene-assets/{scene_id}/image|read|读取指定场景参考图。|download
uploaded_image|GET|/projects/{identity}/uploads/{upload_id}|read|读取项目暂存图片。|download
upload|POST|/projects/{identity}/uploads|edit|上传局部返修图片，返回稳定素材身份。|upload
validate|POST|/projects/{identity}/validate|read|预检完整分镜导入，不写入。
apply|POST|/projects/{identity}/apply|edit|导入完整分镜方案，不生成素材。
patch_validate|POST|/projects/{identity}/patch-validate|read|预检局部返修及素材映射。
patch_apply|POST|/projects/{identity}/patch-apply|edit|原子提交局部提示词、参考绑定与图片替换，备份并回读验证。
scenes|GET|/projects/{identity}/scene-assets|read|读取场景参考资产。
scene_validate|POST|/projects/{identity}/scene-validate|read|预检场景替换/解绑/恢复影响，取得确认令牌。
scene_apply|POST|/projects/{identity}/scene-apply|edit|提交已确认场景修改，不绕过原生确认令牌。
image_validate|POST|/projects/{identity}/image-validate|read|预检单张图片生成。
image_apply|POST|/projects/{identity}/image-apply|generate|提交已确认单张图片生成，可能收费。
batch_validate|POST|/projects/{identity}/image-batch-validate|read|预检批量图片生成。
batch_apply|POST|/projects/{identity}/image-batch-apply|generate|提交已确认图片批次，可能收费。
image_settings|GET|/projects/{identity}/generation-settings|read|读取项目实际图片渠道和参数。
image_settings_save|PATCH|/projects/{identity}/generation-settings|edit|修改本项目图片生成参数。
batch_control|POST|/projects/{identity}/image-batch-control|edit|暂停图片批次或取消未提交任务，不伪称上游已停止。
image_status|GET|/projects/{identity}/image-status|read|读取图片批次与单张任务状态。
''')

group('image_studio_', '/api/image-studio', '''
list|GET||read|读取图片工作室任务。
create|POST||generate|创建图片工作室生成任务，可能收费。
resume|POST|/{identity}/resume|generate|恢复图片工作室任务，可能收费。
cancel|POST|/{identity}/cancel-queue|edit|取消图片工作室排队任务。
file|GET|/{identity}/files/{name}|read|下载工作室生成图片。|download
''')

group('editor_', '/api/editor/jobs', '''
create|POST||generate|用已上传视频、配音和字幕进行裁剪、混音与字幕合成，不调用生成模型。
list|GET||read|列出素材合成任务。
get|GET|/{job_id}|read|读取素材合成进度与日志。
assets|GET|/{job_id}/assets|read|读取素材合成任务成果清单。
artifact|GET|/{job_id}/artifacts/{filename}|read|下载素材合成结果。|download
''')
