# 一键成片 / One-Click VidGen

一个由 FastAPI 后端和 Vue 前端组成的 AI 视频生产工作台。支持 IndexTTS2 本地 GPU、
集群 GPU 加速、Qwen-TTS 云端配音以及已有音频；流水线包括字幕识别与校对、Agent
0/1/2 全文理解与语义分镜、第三方图像接口生图、画面精修、时序调整、BGM 和视频合成。

## 两种使用方式

- **源码部署版（本仓库）**：适合开发者和协作者。包含完整源码、测试、依赖清单与锁文件，
  依赖通过包管理器安装。
- **Windows 便携整合包**：适合普通用户。额外包含约 17 GB 的 Python/CUDA、Node.js、
  FFmpeg、Chrome Headless Shell、IndexTTS2 模型和其他运行资源，解压后双击启动。

受 GitHub 单文件和仓库存储限制，便携运行时与模型权重不放入源码仓库，请从项目发布页或
项目提供的网盘获取完整整合包。API Key、任务数据、日志和生成媒体同样不会进入版本控制。

## 环境要求

- Python 3.10+
- Node.js 与 npm
- FFmpeg（需要加入 `PATH`）
- 可用的语言模型与图像模型 API Key
- 使用本地 IndexTTS2 时，需要另行准备官方模型权重；没有合适显卡可改用 Qwen-TTS
- 使用集群 GPU 时，需要部署符合《集群云端加速接口文档》的 cloud-api 服务

## 安装

```bash
cp .env.example .env
pip install -r requirements.txt
npm install
cd frontend && npm install && cd ..
```

本地版本默认使用 SQLite，无需安装 MySQL。在 `.env` 中填写 RunningHub 和 Gemini
相关配置；不要把包含真实密钥的 `.env` 提交到 Git。

## 集群 GPU 加速

在 `.env` 中配置集群业务网关，不要填写 Ray Head、Ray Serve 或 MinIO 的内部地址：

```dotenv
CLOUD_API_BASE_URL=https://your-domain/api/v1
CLOUD_API_CONNECT_TIMEOUT=15
CLOUD_API_READ_TIMEOUT=180
CLOUD_API_RETRY_COUNT=2
CLOUD_API_RETRY_DELAY_SECONDS=1
CLOUD_JOB_POLL_INTERVAL=2
CLOUD_JOB_MAX_WAIT_SECONDS=3600
```

启动后在“语音参数”中选择“集群 GPU”，登录云端账户并选择预置音色，或者上传已经获得
合法授权的 WAV、MP3、FLAC 参考音频。浏览器只访问本地 FastAPI；Access Token 和
Refresh Token 只保存在后端进程内存中，后端重启后需要重新登录云端账户。

集群模式只替换模块 1 的 TTS：文案分块提交给 cloud-api，完成后逐块下载 WAV，在本机
合并 `final_output.wav` 和 `final_output.srt`，后续 ASR、Agent、生图和视频渲染仍使用
原有本地流水线。停止本地任务时会同时请求取消对应的云端任务。

## 启动

Windows 双击 `start_windows.bat`；Linux 可运行 `./start.sh`。

注意：`start_windows.bat` 面向包含 `runtime/`、`tools/ffmpeg/` 和 IndexTTS2 模型的便携
整合包。纯 Git 源码部署请分别运行：

```bash
python -m uvicorn backend.app.main:app --host 127.0.0.1 --port 8010
npm --prefix frontend run dev
```

默认前端地址为 `http://127.0.0.1:5173`，后端地址为
`http://127.0.0.1:8010`。

## 仓库说明

仓库包含前后端源码、自动化测试、Python/Node 依赖清单、启动脚本和源码部署辅助脚本。
本地模型、便携运行时、API 密钥、依赖安装目录、音色文件、任务数据、日志和生成媒体不纳入
版本控制。官方 IndexTTS2 项目见 <https://github.com/index-tts/index-tts>。
