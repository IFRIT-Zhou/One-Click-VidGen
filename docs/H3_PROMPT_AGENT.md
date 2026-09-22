# H3 提示词转换 Agent

OCV 的分镜 Agent 继续产出与厂商无关的通用视频提示词。用户在动态视频项目中选择“本地 ComfyUI”，并明确开启“使用 H3 提示词转换 Agent”后，系统才在提交本地工作流前执行一次转换。

转换遵循 MiniMax 官方仓库中的 `skills/h3-prompt-writing` 规范，采用单张核心分镜图的 Ref2VA 六段结构：`subject_definitions`、`summary`、`retention_analysis`、`detailed_description`、`overall_soundscape`、`non_diegetic_music`。

转换请求会把已经确认的核心分镜图作为真实多模态图片传给语言模型，而不是只发送图片提示词。图片文件 SHA-256 会进入 H3 缓存指纹；重绘或替换图片后，旧转换结果自动失效。Agent 以图像像素确认主体、构图、空间关系、画风和光影，只在时间轴中重点描述相对核心图发生的变化。

- 通用提示词不会被覆盖。
- H3 专用提示词和来源指纹会保存在镜头记录及本次生成请求快照中。
- 通用提示词、动态表达、核心图提示词或时长变化后，旧的 H3 缓存自动失效。
- 转换失败时不会启动 ComfyUI，不会占用本地显卡任务。
- 默认不虚构人物对白、旁白或 BGM；只保留原提示词明确要求的声音。

规范来源：<https://github.com/MiniMax-AI/MiniMax-H3/tree/main/skills/h3-prompt-writing>
