# 官网云端 API

这里保存官网 `/api/v1` 服务的源码，独立于仓库根目录下供桌面客户端使用的 `backend/app`。本次同步包含线上账户与会话实现，以及邮箱找回密码功能；不要将两套后端相互覆盖。

## 邮箱找回密码

- 官网入口：`/forgot-password/`；桌面端云端登录窗口内提供完整邮箱验证和密码重置表单，通过本机 `/api/cloud/auth/password-reset/*` 转发，不再跳转官网。
- `POST /api/v1/auth/password-reset/request`：`{"email":"user@example.com"}`。
- `POST /api/v1/auth/password-reset/confirm`：`{"email":"user@example.com","code":"123456","password":"new-password"}`。
- 6 位随机验证码，生成后 30 分钟有效，只能使用一次；重发替换旧码。
- 每个邮箱 60 秒内只能发送一次、每小时最多 5 次；每个来源 IP 每小时最多 20 次。
- 每个验证码最多允许 5 次错误验证，验证接口另外限制邮箱和 IP 请求频率。
- 新密码要求 10–200 位；重置成功会废弃旧访问令牌和刷新令牌。
- 注册与未注册邮箱返回相同提示；验证码只保存带服务器密钥的 HMAC 摘要，SMTP 授权码不进入仓库或日志。
- PostgreSQL 使用行锁；SQLite 开发环境使用写事务，避免验证码重复消费。

## 配置与部署

Python 3.12+。参考 `smtp.env.example`，在服务器专用环境文件中设置 SMTP 参数；QQ 邮箱使用 SMTP 授权码，SSL 端口 465。不要填写 QQ 登录密码或把授权码提交到 Git。

安装依赖：

```sh
cd deploy/cloud-api
python -m venv .venv
.venv/bin/pip install -e '.[test]'
```

数据库和服务配置沿用现有 Cloud API 的环境变量，例如 `DATABASE_URL`、`JWT_SECRET`、运行环境及上游服务配置。生产 JWT 密钥必须稳定且保密：修改它会使现有令牌和恢复验证码失效。

新数据库可执行 `python -m app.cli init-db`。现有数据库升级时，加载与 API 相同的环境变量后执行：

```sh
PYTHONPATH=. python migrations/20261008_password_recovery.py
```

迁移只增加缺失的 `users.auth_version` 列，以及验证码与限流表，不修改任何用户密码。先迁移、再更新 API 进程、最后更新 `deploy/nginx/site` 静态文件。API 入口为 `app.main:app`。

使用 systemd 时可添加：

```ini
[Service]
EnvironmentFile=/etc/cloud-api-smtp.env
```

环境文件应由 root 管理，权限为 `0600`，随后重新加载 systemd 并重启 API。Nginx 应传递真实来源地址；Uvicorn 只信任受控反向代理，不应信任来自公网的任意代理头。

发送使用应用后台任务；进程在投递前退出或 SMTP 失败时，用户可在冷却结束后重发。SMTP 失败只记录通用错误，不记录邮箱、验证码、密码或授权码；失败验证码会被废弃。收件箱送达仍受邮箱服务商反垃圾策略影响。

## 验证

```sh
python -m unittest discover -s tests -v
node --test ../nginx/site/tests/password-recovery.test.mjs
```

测试使用隔离数据库和模拟邮件，覆盖正常找回、30 分钟边界、旧码和已用码、错误次数、重发、旧会话失效、管理员重置以及并发单次使用，不会修改真实账户或发送邮件。

官网页面、后台控制台和编辑器的源码也已按当前上线版本同步到 `deploy/nginx/site`。运行完整生成服务仍需现有云端 GPU、存储和模型服务配置。
