# Codex 制作桥（插件 1.2 / 接口 v1）

可选的 OCV 制作接口插件。宿主提供受控接口，清单启用后才可调用；不执行任意第三方代码。在“插件与设置”停用即可关闭接口，不删除项目。

## 用户流程

1. 启用 **Codex 制作桥**，首页也会出现卡片。
2. 点 **复制给 Codex 的说明**，粘贴到新对话，提供稿件、参考素材和创作要求。说明包含本机通用 Skill 路径；无需先安装 Skill。Codex 保存初始草稿后，首页制作桥点“刷新 → 打开并核查”。
3. 在 OCV 生成并修正配音、字幕，点 **确认配音，交给 Codex 规划**，不必先跑 OCV 自动规划。
4. Codex 导入方案后，重新打开分镜核查，再由你生成图片、视频。

这是通用的动态视频交接插件，适用于故事、科普、产品介绍等领域。专业创作可另加私人 Skill，不捆绑任何人的稿件或私有规则。插件本身不调用外部模型；用户交给 Codex 的资料仍受所用 Codex 服务的数据处理规则约束。

## 随软件附带的 Codex Skill

[打开通用 Skill](skills/ocv-production-bridge/SKILL.md)。OCV 中也可点“使用指南”查看。最简单的用法是让 Codex 按绝对路径完整读取此文件，无需复制到全局目录，每次更新自动沿用软件内新版。

想长期安装到自己的 Codex，可让 Codex 按当前安装规范将 `skills/ocv-production-bridge` 整个目录安装为个人 Skill，已有同名版本须先检查；这份拷贝不会随 OCV 自动更新。Skill 的结构参考 [OpenAI 官方说明](https://learn.chatgpt.com/docs/build-skills)。OCV 不自动改动用户的 Codex 配置。

目前桥接的是动态视频草稿与分镜规划，不是图文视频编辑或一键全自动出片；它也不是 MCP 服务。完整操作契约以随包 Skill 和接口 schema 为准。

## 固定客户端

脚本只依赖 Python 标准库，不导入 OCV 内部模块。全局参数放在子命令之前。

```powershell
& '<OCV>/runtime/python/python.exe' -X utf8 '<OCV>/plugins/codex_bridge/ocv_bridge.py' info
& '<python>' '<client>' guide
& '<python>' '<client>' --out '<档案>/bridge-schema.json' schema
& '<python>' '<client>' projects
& '<python>' '<client>' audio-tasks
& '<python>' '<client>' upload '<照片或参考音色>'
& '<python>' '<client>' draft-save '<draft.json>'
& '<python>' '<client>' --out '<资料包.json>' from-audio '<配音任务ID>' --confirmed
& '<python>' '<client>' --out '<资料包.json>' pack '<动态项目ID>'
& '<python>' '<client>' validate '<动态项目ID>' '<plan.json>'
& '<python>' '<client>' apply '<动态项目ID>' '<plan.json>'
& '<python>' '<client>' patch-validate '<动态项目ID>' '<patch.json>'
& '<python>' '<client>' patch-apply '<动态项目ID>' '<patch.json>'
```

默认地址 `http://127.0.0.1:8010`，用 `--base-url` 修改。需要登录时用用户提供的现有 Netscape cookie 文件 `--cookie-file`，不伪造会话。`--out` 保存全文，只打印小回执，避免重复读取巨大上下文。接口不可用时报告，不退回直接覆盖 record.json。

### 初始草稿

draft.json 顶层为 `id`（新建时生成32位十六进制 UUID，重试复用）、`revision`（新建0，更新用回读值）、`parameters`。
最小参数为 `project_name`、`script`；三项视觉设置为 `visual_style_prompt`、`global_character_prompt`、`story_environment_prompt`；画幅 `video_orientation=landscape|portrait`。其他可用字段见 schema 的 draft_fields。

参考图先 upload，保存 asset.id 与本地文件哈希，续作复用。填写 reference_image_ids（最多6）及按 asset.id 索引的 reference_image_labels/notes/kinds。参考音色用 `tts_voice_id=upload:<asset.id>` 和支持上传音色的 indextts25；集群用明确选定的 cluster_voice_id/type。不要把音色样本当成片旁白，不填写密钥、API地址或全局号池开关。

服务器草稿不等于生成任务。用户“打开并核查”后才载入原生新建页、另存浏览器草稿；已有本机改动时由用户选择，不静默覆盖。不要声称服务器已保存等于浏览器已打开。

### 配音确认后

只有用户确认后才 from-audio：创建或复用分镜项目，并同步已修改的配音；同步可能标记旧画面待检查。普通读取只用 pack，不重复 from-audio。
资料包包含真实字幕、语义段落、参考图、现有方案、revision、timeline_token；不含日志历史或账户配置。参考图需实际查看，不能只看文件名。源材料的事实与身份仍须单独审核。

plan.json 示例（token 替换为资料包原值）：

```json
{
  "revision": 1,
  "timeline_token": "资料包中的64位值",
  "audio_confirmed": true,
  "shots": [{
    "id": "shot01",
    "slide_ids": ["scene_001", "scene_002"],
    "kind": "static",
    "intent": "本镜说明什么",
    "image_prompt": "完整核心图提示词",
    "reference_ids": [],
    "evidence": "来源位置、客户确认或创作依据及表述边界"
  }]
}
```

动态镜头 kind=video，必须按 schema 的 motion_example 填 motion_plan，指定主体、阶段动作、参考构图和文字清单。运镜写在阶段动作里。video_prompt 可省略，服务确定性编译，不调用 LLM；如自行填写则必须与阶段方案一致。

不提交 start/end/duration，不复制整条字幕、日志或账户字段。服务按 slide_ids 计算并校验，动态超过15秒直接返回定位错误，不偷偷改为静态。字幕时间空隙不可作为切镜点：将两侧字幕放在同镜，避免当前合成器丢失这段时长。语义段落是线索，不是“一段必须一镜”。

validate 不落盘；apply 再次校验版本、备份、原子提交并回读，重复请求有去重回执。失败只修指定镜头，不重新遍历源码或编写一次性写回脚本。机械检查不代表事实审核通过。

完整导入仅用于无已生成素材的分镜草案。插件 1.2 提供已有素材的局部返修：`patch.json` 顶层仍为 revision、timeline_token、audio_confirmed:true、shots，但只提交要修改的镜头；每镜只允许 id 和 intent/image_prompt/reference_ids/motion_plan/video_prompt/evidence。省略字段保持原值，null 不合法。保持时间轴、动静类型、单镜生成配置与未指定镜头不变。

返修的图像提示词采用 **reference_ids 选中顺序的局部图号**；不同于完整导入的项目图号。更换引用时必须同时更新图像提示词。修改 motion_plan 而省略 video_prompt 时，确定性重编后者。先看 patch-validate 的 impacts，再 patch-apply；批量全部校验通过才写入，带版本/音轨校验、备份、原子提交与重复请求去重。

旧图保留并提示待核查，受影响的旧视频保留到历史、不再自动参与合成；旧成片文件不删除，但完成状态撤回到审核阶段。接口不自动重绘、生成或扣费。intent/evidence 单独变更不使素材过期。详见随包 Skill 的“已有素材的局部返修”。私人 Skill、项目证据及原始资料不进入公开更新。
