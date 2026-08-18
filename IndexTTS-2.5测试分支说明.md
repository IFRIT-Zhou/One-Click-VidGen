# IndexTTS-2.5 测试分支说明

当前测试分支：`codex/indextts25-integration`

## 隔离边界

- 2.5 官方源码：`tools/IndexTTS25`
- 2.5 官方权重：`tools/IndexTTS25/checkpoints`
- 2.5 参考音色：`tools/IndexTTS25/examples`
- 2.5 专用依赖覆盖层：`tools/IndexTTS25/python_packages`
- 2.5 缓存与临时文件：`runtime/data/indextts25`
- IndexTTS-2.5 已升级为 OCV 默认本地引擎；旧版 2.0 源码、权重与启动入口已移除。

## UI 使用

在“语音参数 → 执行方式”选择“本地 GPU · IndexTTS-2.5（测试）”。测试阶段建议先使用并行数 1；确认单进程稳定和显存余量后再测试并行数 2。

2.5 当前支持中文、英文、日语、西班牙语和阿拉伯语。OCV 本轮先默认使用中文 `ZH`，可通过 `.env` 的 `INDEXTTS25_LANGUAGE` 修改。

## 当前接入内容

- 使用官方 `indextts.infer_v2_5.IndexTTS2`，不借用 2.0 推理类。
- 使用 2.5 原生 BF16 和 `duration_factor` 语速控制。
- 支持 OCV 既有参考音色上传、情绪向量、音量、音调、逐句归档、字幕时间轴和单句重配。
- 2.5 与 2.0 在不同子进程导入环境中运行，避免 Python 包互相覆盖。
- IndexTTS-2.5 使用独立的 token 计数、配音断句 Agent 与 Python 安全上限保护。
- IndexTTS-2.5 当前处于基础性能测试模式：OCV 不做断句，整段文案作为一个任务交给官方 2.5；官方实现内部的原生文本处理仍保留，作为基准性能的一部分。

## 后续专项测试

1. 比较 2.0 / 2.5 的首句加载时间、单句推理速度、总耗时、峰值显存和音质。
2. 测试并行 1、2，以及 4090 上的安全显存余量；并行 3 暂不作为默认建议。
3. 使用模块 1“仅配音”测量 2.5 对不同长度中文文本的音质、速度与显存，再设计独立于 2.0 的专属断句规则。
4. 评估并打包 Windows 版 `flash-attn` / `triton-windows` 后，再开启 `INDEXTTS25_ACCEL` 或 `INDEXTTS25_TORCH_COMPILE`。

## 重装命令

在 OCV 根目录运行：

```powershell
powershell -ExecutionPolicy Bypass -File tools\deploy_indextts25.ps1
```

脚本会从官方 GitHub 和 `IndexTeam/IndexTTS-2.5` ModelScope 仓库部署，不会复用旧版 2.0 权重。
