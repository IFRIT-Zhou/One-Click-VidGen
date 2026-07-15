# 口播视频生成台

一个由 FastAPI 后端和 Vue 前端组成的口播视频自动生产工具。支持在线配音，也支持
上传已有配音与文案；流水线包括字幕识别与校对、长文主题分段、Gemini 画面规划、
RunningHub GPT-image-2 生图、HTML 画面编排和视频合成。

## 环境要求

- Python 3.10+
- Node.js 与 npm
- FFmpeg（需要加入 `PATH`）
- 可用的 RunningHub 和 Gemini API Key

## 安装

```bash
cp .env.example .env
pip install -r requirements.txt
npm install
cd frontend && npm install && cd ..
```

本地版本默认使用 SQLite，无需安装 MySQL。在 `.env` 中填写 RunningHub 和 Gemini
相关配置；不要把包含真实密钥的 `.env` 提交到 Git。

## 启动

Windows 双击 `start_windows.bat`；Linux 可运行 `./start.sh`。

默认前端地址为 `http://127.0.0.1:5173`，后端地址为
`http://127.0.0.1:8010`。

## 仓库说明

仓库只包含前后端源码及运行所需配置。本地模型、API 密钥、依赖目录、音色文件、
任务数据、日志和生成媒体均不会纳入版本控制。
