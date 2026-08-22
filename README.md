# 一键成片 / One-Click VidGen

[![License: AGPL v3](https://img.shields.io/badge/License-AGPL_v3-blue.svg)](./LICENSE)

一个由 FastAPI 后端和 Vue 前端组成的 AI 视频生产工作台。支持 IndexTTS-2.5 本地 GPU、
集群 GPU 加速、Qwen-TTS 云端配音以及已有音频；流水线包括字幕识别与校对、Agent
0/1/2 全文理解与语义分镜、第三方图像接口生图、画面精修、时序调整、BGM 和视频合成。

## 两种使用方式

- **源码部署版（本仓库）**：适合开发者和协作者。包含完整源码、测试、依赖清单与锁文件，
  依赖通过包管理器安装。
- **Windows 便携整合包**：适合普通用户。额外包含约 17 GB 的 Python/CUDA、Node.js、
  FFmpeg、Chrome Headless Shell、IndexTTS-2.5 模型和其他运行资源，解压后双击启动。

受 GitHub 单文件和仓库存储限制，便携运行时与模型权重不放入源码仓库，请从项目发布页或
项目提供的网盘获取完整整合包。API Key、任务数据、日志和生成媒体同样不会进入版本控制。

## 环境要求

- Python 3.10+
- Node.js 与 npm
- FFmpeg（需要加入 `PATH`）
- 可用的语言模型与图像模型 API Key
- 使用本地 IndexTTS-2.5 时，需要另行准备官方模型权重；没有合适显卡可改用 Qwen-TTS
- 使用集群 GPU 时，需要部署符合《集群云端加速接口文档》的 cloud-api 服务

集群服务端文档：

- [服务设计](./docs/cluster/集群云端加速服务设计文档.md)
- [接口契约](./docs/cluster/集群云端加速接口文档.md)
- [服务端待完善清单](./docs/cluster/集群端待完善清单_2026-08-01.md)

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

客户端未设置 `CLOUD_API_BASE_URL` 时会自动使用正式业务网关
`https://oneclickvidgen.com/api/v1`，同步源码后无需手工填写服务器地址。私有部署或本地
模拟环境可以在 `.env` 中覆盖该地址；不要填写 Ray Head、Ray Serve 或 MinIO 的内部地址：

```dotenv
CLOUD_API_BASE_URL=https://oneclickvidgen.com/api/v1
CLOUD_API_CONNECT_TIMEOUT=15
CLOUD_API_READ_TIMEOUT=180
CLOUD_API_RETRY_COUNT=2
CLOUD_API_RETRY_DELAY_SECONDS=1
CLOUD_JOB_POLL_INTERVAL=2
CLOUD_JOB_MAX_WAIT_SECONDS=3600
```

正式业务网关为 `https://oneclickvidgen.com/api/v1`。域名备案和 HTTPS 已于
2026-08-04 完成，HTTP 会强制跳转到 HTTPS。公网客户端只能配置 cloud-api 业务网关，
不能直接访问 Ray 的 `/api/tts` 或内部端口。

启动后在“语音参数”中选择“集群 GPU”，登录云端账户后可以选择云端默认音色，或者
上传 WAV、MP3、FLAC 参考音频。音色保持“不选择”也可以直接提交，
此时自动使用集群 `/cloud/voices` 接口返回的第一个默认音色；前端不会自行假设集群
支持的音色。用户上传音色需要云端部署
`/cloud/voices` 查询、上传和删除接口。浏览器只访问本地 FastAPI；Access Token 和
Refresh Token 只保存在后端进程内存中，后端重启后需要重新登录云端账户。

登录集群账户后可点击账户栏中的“支付宝充值”。桌面客户端通过本机 FastAPI 代理读取
`/recharge/products` 套餐、创建充值订单并在新窗口打开支付宝官方收银台；随后轮询订单
状态，确认 `paid` 后自动刷新积分。客户端不会接收支付宝密码、银行卡信息或支付回调，
也不会根据浏览器返回页面自行增加积分。支付宝跳转地址只允许官方生产或沙箱网关域名。
未完成订单会在本机浏览器存储中短暂保留，客户端刷新后仍可继续查询。

集群模式只替换模块 1 的 TTS：文案分块提交给 cloud-api，完成后逐块下载 WAV，在本机
合并 `final_output.wav` 和 `final_output.srt`，后续 ASR、Agent、生图和视频渲染仍使用
原有本地流水线。停止本地任务时会同时请求取消对应的云端任务。

界面的“并发 0/2”表示当前账户占用的云端任务槽数/账户上限，不表示 GPU 数量，也不
表示单任务文本分块数。当前服务端并发领取、任务崩溃恢复和 Ray 全局容量对齐仍需按
[服务端待完善清单](./docs/cluster/集群端待完善清单_2026-08-01.md) 完成后，才可视为
生产级并发控制。

## 启动

Windows 双击 `start_windows.bat`；Linux 可运行 `./start.sh`。

注意：`start_windows.bat` 面向包含 `runtime/`、`tools/ffmpeg/` 和 IndexTTS-2.5 模型的便携
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
版本控制。官方 IndexTTS-2.5 项目见 <https://github.com/index-tts/index-tts>。

## 开源许可证

OCV 自研代码依据 [GNU Affero General Public License Version 3 only](./LICENSE)
（`AGPL-3.0-only`）发布。你可以使用、研究、修改、再分发和商业使用本项目，但必须遵守
AGPL-3.0-only 的全部条件。修改后通过网络向用户提供服务时，应按照许可证要求向这些
用户提供运行版本的对应源代码。

对于周若雨、何允拥有版权的 OCV 原创部分，另适用严格限定在 AGPL 第 7(b)、7(c) 和
7(e) 条允许范围内的[附加条款](./ADDITIONAL_TERMS.md)：保留合理作者归属、清楚标记
修改版本并保留商标权。该条款不禁止商业使用，也不适用于第三方组件、模型和媒体。

官方源码仓库：<https://github.com/IFRIT-Zhou/One-Click-VidGen>

> **官方声明：截至 2026 年 8 月 22 日，OCV 未授权任何培训机构、付费课程、代运营机构或
> 软件售卖方以“OCV 官方”“官方授权”“官方合作”等名义招生、收费或提供售后。**
> 第三方可以在遵守 AGPL-3.0-only 和品牌使用规则的前提下自行提供收费服务，但这不代表
> OCV 官方授权、认证或背书，其收费、交付和售后责任均由该第三方自行承担。遇到相关宣传时，
> 请以本仓库和 <https://oneclickvidgen.com/> 公布的信息为准。

软件按“原样”提供，不附带任何明示或默示担保。版权与贡献说明见 [NOTICE](./NOTICE) 和
[AUTHORS.md](./AUTHORS.md)。
One-Click VidGen、OCV、一键成片、润物工坊及官方 Logo 不随软件许可证授权，衍生版本
不得冒充官方；详见 [品牌使用规则](./TRADEMARKS.md)。第三方组件、模型权重、字体和媒体
仍遵守各自条款，详见 [第三方软件与资源说明](./THIRD_PARTY_NOTICES.md)。
