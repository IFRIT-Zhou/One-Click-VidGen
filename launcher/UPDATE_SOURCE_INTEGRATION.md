# OCV Launcher 国内更新源接入说明

这份说明用于服务端或发布维护者接入官网、OSS、CDN、ModelScope、Gitee 等更新源。Launcher 核心代码不需要因供应商变化而修改。

## 当前行为

Launcher 按 `launcher/update-sources.json` 中 `channel_urls` 的顺序检查更新：

1. `https://download.oneclickvidgen.com/launcher/update-channel.json`
2. GitHub 官方仓库的 `update-channel.json`

国内地址尚未部署时，首个请求使用短超时，失败、404、返回非法 JSON 或连接异常后会立即尝试 GitHub。不会阻断现有用户更新。

## 服务端只需要提供两个静态文件

1. 更新清单：`update-channel.json`
2. 更新包：ZIP 文件

它们可以放在官网静态目录、对象存储、CDN、ModelScope 或 Gitee，只要能够通过无需登录的 HTTPS 直链下载。

国内源启用前，应当先上传更新包，再上传更新清单，避免客户端读到清单后下载不到对应包。

## update-sources.json

该文件属于 Launcher 本地配置，控制“去哪里寻找更新清单”以及允许下载更新包的域名。

```json
{
  "channel_urls": [
    "https://download.oneclickvidgen.com/launcher/update-channel.json",
    "https://raw.githubusercontent.com/IFRIT-Zhou/One-Click-VidGen/main/launcher/update-channel.json"
  ],
  "allowed_download_hosts": [
    "*.oneclickvidgen.com",
    "github.com",
    "*.githubusercontent.com",
    "*.aliyuncs.com"
  ],
  "primary_channel_timeout_ms": 2500,
  "fallback_channel_timeout_ms": 20000,
  "primary_download_timeout_ms": 5000,
  "fallback_download_timeout_ms": 30000,
  "download_read_write_timeout_ms": 120000
}
```

如果最终使用 ModelScope 或 Gitee，只需把其更新清单直链放在 `channel_urls` 第一项，并把下载域名加入 `allowed_download_hosts`。通配符仅支持 `*.example.com` 形式。

## update-channel.json 新字段

```json
{
  "release_id": "2026.08.26.1",
  "release_order": 2026082601,
  "display_version": "显示给用户的版本名",
  "portable_overlay_safe": true,
  "portable_overlay_min_order": 2026081801,
  "archive_url": "https://github.com/.../main.zip",
  "archive_urls": [
    "https://download.oneclickvidgen.com/releases/2026.08.26.1/One-Click-VidGen-main.zip",
    "https://github.com/.../main.zip"
  ],
  "archive_sha256": "",
  "content_fingerprint": "..."
}
```

- `archive_url`：旧 Launcher 使用，发布期内必须保留 GitHub 地址。
- `archive_urls`：新 Launcher 按顺序尝试，国内源放第一项，GitHub 放最后一项。
- `archive_sha256`：可选。填写时所有镜像必须提供完全相同的 ZIP 字节；否则留空，安装后仍会执行关键文件指纹校验和失败回滚。
- `content_fingerprint`：运行 `python tools/release_integrity.py --write-channel` 生成，不要手工填写。

## 推荐发布顺序

1. 生成一次固定 ZIP，并计算 SHA-256（如果计划启用包哈希）。
2. 把完全相同的 ZIP 上传到国内源。
3. 把新的 `update-channel.json` 上传到国内源。
4. 提交并推送 GitHub，使 GitHub 通道与国内通道内容一致。
5. 用一台不启用 VPN 的 Windows 电脑测试“检查更新”和“安全更新”。

## 安全约束

- 仅接受 HTTPS 更新清单和下载地址。
- 下载域名必须匹配 `allowed_download_hosts`。
- 可选校验整个 ZIP 的 SHA-256。
- 安装后校验 OCV 关键文件指纹。
- 用户 `.env`、模型、运行环境、输出、工作区、日志和插件数据继续受现有备份与回滚机制保护。

## Git 仓库用户

当前多源框架主要解决便携版用户更新。带 `.git` 的开发者工作区仍使用 `origin/main` 的安全快进更新；若未来需要服务无 VPN 的 Git 用户，可以再配置一个同步到 Gitee 的只读 Git 镜像。
