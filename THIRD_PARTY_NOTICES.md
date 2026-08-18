# 第三方软件与资源说明

One-Click VidGen 自研代码使用 `AGPL-3.0-only`。项目依赖的第三方软件、模型、字体、
媒体和在线服务不因本项目许可证而改变其原有权利和许可条件。

本清单用于帮助用户定位主要上游项目，不替代上游许可证正文。实际安装的完整依赖和
版本以 `requirements.txt`、`package-lock.json`、`frontend/package-lock.json` 及发行包
内随附的许可证为准。

## 主要运行组件

| 组件 | 上游地址 | 许可说明 |
| --- | --- | --- |
| HyperFrames | https://github.com/heygen-com/hyperframes | Apache License 2.0 |
| Vue | https://github.com/vuejs/core | MIT License |
| Vite | https://github.com/vitejs/vite | MIT License |
| FastAPI | https://github.com/fastapi/fastapi | 依上游许可证 |
| PyTorch / Torchaudio | https://github.com/pytorch/pytorch | 依上游许可证 |
| Faster-Whisper | https://github.com/SYSTRAN/faster-whisper | 依上游许可证 |
| CTranslate2 | https://github.com/OpenNMT/CTranslate2 | 依上游许可证 |
| Transformers | https://github.com/huggingface/transformers | 依上游许可证 |
| IndexTTS / IndexTTS-2.5 | https://github.com/index-tts/index-tts | 代码与模型权重分别依上游条款 |
| FFmpeg | https://ffmpeg.org/legal.html | 具体为 LGPL/GPL 取决于构建配置 |

其余 Python 和 Node.js 依赖分别遵守各自发布包内的许可证。再分发二进制整合包时，
发布者必须同时核对并附带该发行包实际包含的依赖许可证和必要 Notice。

## 模型权重、字体和媒体

- IndexTTS-2.5、Whisper 或其他模型权重不由本项目重新授权；模型权重是否允许下载、
  再分发和商业使用，以对应模型发布页的条款为准。
- 用户自行添加的字体、音色、图片、音乐和视频不属于 OCV 项目内容，用户应自行确认
  拥有必要权利。
- `tts_voices/legacy_import/sample_library/avatars/` 中的示例头像只用于本地样例库兼容；
  正式发行前应确认素材来源和再分发权，来源不明时不得作为官方演示素材分发。

## 第三方在线服务

RunningHub、Gemini、OpenAI、DeepSeek、Kimi、GLM、DashScope 及其他兼容接口是独立的
第三方服务。OCV 对这些服务的适配不代表隶属、授权或背书。用户需要自行遵守相应服务
条款、内容政策和计费规则。

## 许可问题反馈

如果发现遗漏的版权或许可证信息，请通过官方仓库提交 Issue：
https://github.com/IFRIT-Zhou/One-Click-VidGen/issues
