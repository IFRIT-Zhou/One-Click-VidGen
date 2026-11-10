# 公告发布

管理员进入官网 `/admin/announcements.html`（管理后台 → 公告管理），填写标题和正文，可保存草稿、发布、置顶或归档。正文为纯文本，支持换行；标题最多 160 字，正文最多 10000 字。发布前有预览和确认。

已发布内容通过 `GET /api/v1/announcements?limit=20&offset=0` 同步给官网 `/announcements/` 和客户端的“公告”入口。未登录的官网访问者也能阅读。草稿和归档内容仅管理员可见；将已发布公告保存为草稿或归档即可撤下。

官网横幅和客户端提示可关闭，按公告编号及版本记忆；更新公告后会再次提醒。列表仍可随时打开。公告请求失败不会阻断生成任务。客户端需要更新至包含此功能的版本。

## 安装

1. 部署 `app/announcements.py`，在 Cloud API `app/main.py` 注册 `announcements.router`。
2. 使用 Cloud API 的 Python 和同一数据库配置，设置 `PYTHONPATH` 为 Cloud API 根目录，执行 `python migrations/20261110_announcements.py`。只新增公告表，重复执行安全，不创建公告。用户表、管理员审计表应已存在。
3. 重启 Cloud API，部署网站公告 JS/CSS、两个页面，以及公共头部加载器和后台导航。HTML 引用的共享脚本版本号需要更新，避免浏览器沿用一周缓存。
4. 更新客户端前后端。本地 `/api/announcements` 代理遵循本地登录验证，读取已有 Cloud API 地址，无需新增密钥。

## 接口约定

公开和管理员列表均返回 `{items,total}`，每页 1–100 条。管理员列表可用 `status=draft|published|archived` 筛选。

管理员 `POST /api/v1/admin/announcements` 接收 `title,body,pinned,status,client_id`；相同管理员、相同 client_id 和相同内容重试不重复创建，改内容复用编号返回 409。`PUT /api/v1/admin/announcements/{id}` 接收 `title,body,pinned,status,revision`；版本过期返回 409，核对最新公告后再保存。管理接口要求管理员身份，变更记录进入审计日志。公告的新增和更新都不会扣积分。

初次上线没有默认公告。应由管理员填写并发布实际需要告知用户的调整。
