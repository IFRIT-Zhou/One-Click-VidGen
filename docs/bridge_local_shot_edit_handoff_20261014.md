# 制作桥 1.6：已有分镜局部修改交接

## 目标与范围

目标：读单镜 → 修改 → 一次写回并核查。不自动生成、不调用付费服务、不修改配音/字幕/分组/动静类型/其他镜头。公开通用功能，不含医学私有规则。

## 接口与客户端

| CLI | 接口 | 行为 |
| --- | --- | --- |
| `shot-pack project shot_id` | GET `/api/codex-bridge/projects/{id}/shots/{shot_id}` | 单镜方案、关联字幕时间、当前图片与元数据、选中引用、版本与附图预检。无全片镜头或历史。 |
| `image-upload project file` | POST `/projects/{id}/uploads` | multipart 图片暂存；稳定内容哈希 ID，不写 record、不增加 revision。 |
| `references-preview project shot_id` | GET `/projects/{id}/shots/{shot_id}/references-preview` | 纯读取实际附图顺序、ID、哈希、最终提示词与图号。无阶段、API 配置和生成锁限制。 |
| `patch-validate project json` | 原局部预检 | 可检查候选方案及新图，无项目写入。 |
| `patch-apply project json` | 原局部提交 | 新增 image_upload_id，与提示词、参考绑定、动态方案一起校验、备份、提交、回读。 |

后四个接口路径前缀仍为 `/api/codex-bridge`。现有 API 保持兼容；新客户端在服务声明 patch_verification 时直接一次提交，不再先 validate 再 apply 再拉全包，旧后端保留兼容路径。

初始草稿/音色的旧 `upload` 命令不变；它的 asset.id 不可与项目 image-upload 的 img_ ID 混用。

## 事务与素材保护

- 同一文件在同项目重复上传返回同一 img_ + SHA256，最大30MB，单帧 PNG/JPEG/WebP。鉴权、项目隔离、路径边界及真实解码检查。
- 暂存图片只在引用或替换提交成功时进入项目记录。新增参考不裁掉原有素材库。
- 新成图使用独立文件，不直接引用暂存文件作为可覆盖的当前图片，避免原生重绘反向破坏参考素材。
- 旧图按原生历史归档；新图与方案同一 revision 生效。旧视频/导出按既有局部返修规则失效并保留历史。动态自动推进关闭。
- 一批全部校验通过才写入；冲突、来源配音未同步、运行中/状态不确定任务仍拦截写入。只读资料和预检不拦截。
- 回读不一致或写入失败恢复旧记录；原图片不就地覆盖。上传暂存或未引用的新文件可能保留，不自动清理用户目录。
- 响应丢失原样重试复用回执；同时重新检查配音及当前素材，外部修改后返回冲突。

## 回执

changed/impacts、最终 revision、backup_id、readback_verified；verification 包含 audio/subtitles/narration_groups/timing/other_shots 保护结果、配音哈希、目标图片尺寸/格式/字节数/哈希、实际参考 ID/图号/哈希映射。回执不重复整段提示词。

图号存在不等于人物身份/专业事实正确；visual_review_required 始终保留。参考预检缺图/图号无对应是检查结果，不触发生成。image-validate/image-apply 仍是付费生成的独立流程，未放宽实际生成的阶段及确认条件。

## 修改位置与验证

- 新增 `backend/app/codex_shot_edit.py`，资料读取、暂存上传、图片回执与保护核查。
- 修改 `codex_bridge.py` 的 patch 编译/提交；`codex_image_generation.py` 抽取纯 reference_inputs，保证预检与生成共享附图解析。
- 客户端、公共 Skill、README、plugin.json（1.6.0）同步更新；release_integrity 增加新模块。
- `python -X utf8 -m unittest discover -s tests -p 'test_codex_bridge*.py'`：119 项通过（包括继承的既有用例）。涵盖单镜隔离、鉴权、暂存幂等、完整事务、回滚、重复提交、图片版本、原生重绘隔离和旧客户端兼容。
- Skill quick_validate 通过。未调用真实图片/视频模型，未改变生产项目。

## 本地加载与发布

文件同步到 G 盘本地安装前备份并比较哈希，避免覆盖并发改动；release_integrity 基于 G 盘当前版本单独增量添加新模块，不覆盖其他核心开发增量。

运行中的旧后端不会自动加载新增接口。通过启动器重启后核对 info.plugin_version=1.6.0，以及 shot_pack/project_image_upload/atomic_shot_image_patch/reference_preview/patch_verification 五项 capability。本轮未重启运行中的制作服务、未公开发布或推送更新渠道。
