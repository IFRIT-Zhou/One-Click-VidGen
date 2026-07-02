# 口播视频生成台

一个由 FastAPI 后端和 Vue 前端组成的文案转口播视频项目。配音使用 RunningHub
`minimax/speech-2.8-hd`，流水线包括文案处理、语音生成、字幕、HTML 画面生成和视频合成。

## 环境要求

- Python 3.10+
- Node.js 与 npm
- MySQL
- FFmpeg（需要加入 `PATH`）
- 可用的 RunningHub 和 Gemini API Key

## 安装

```bash
cp .env.example .env
pip install -r requirements.txt
npm install
cd frontend && npm install && cd ..
```

在 `.env` 中填写数据库连接、`RUNNINGHUB_API_KEY` 和 `GEMINI_API_KEY`。

## 启动

```bash
./start.sh
```

默认前端地址为 `http://127.0.0.1:5173`，后端地址为
`http://127.0.0.1:8010`。

## 仓库说明

仓库只包含前后端源码及运行所需配置。本地模型、API 密钥、依赖目录、音色文件、
任务数据、日志和生成媒体均不会纳入版本控制。
