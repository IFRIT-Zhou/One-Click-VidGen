# 号池余额邮件提醒

收件人为 `942700596@qq.com`，复用 `/etc/cloud-api-smtp.env` 中现有邮件配置。没有短信、手机号网关依赖。

`cloud-api-balance-monitor.timer` 每次检查结束五分钟后运行独立 oneshot 服务。读取图片号池密钥，不影响生成服务、不调用付费生成接口。每个凭证用 SHA-256 短编号显示，日志和邮件不包含 API Key。

阈值严格小于30。RunningHub要求美元钱包余额；ICAN最初需求是人民币余额。2026-11-13核验 ICAN `/v1/usage` 实际返回USD，须确认币种或明确平台兑换规则后启用。不得将积分、令牌额度或空返回值当作美元钱包余额。现有 RunningHub 密钥九个返回806，另一个共享密钥仅可读取积分，无法监测美元余额；需要支持钱包查询的密钥。

同平台多个key可能属于同一账号，接口未提供可信账号标识时，必须先确认账号对应关系、选择每账号代表key后开启发送，不能按相同余额擅自合并。

## 部署

复制 `app/pool_balance_{alerts,providers,email,runner}.py` 到 `/opt/cloud-api/app/`，两个systemd单元复制到 `/etc/systemd/system/`。建立 `/var/lib/cloud-api/pool-balance` 并授权cloudapi。无需重启Cloud API或GPU工作进程。

新建仅root可读 `/etc/cloud-api-balance-monitor.env`：

```ini
POOL_BALANCE_EMAIL_ENABLED=false
POOL_BALANCE_RECIPIENTS=942700596@qq.com
POOL_BALANCE_STATE_DIR=/var/lib/cloud-api/pool-balance
```

先 `systemctl daemon-reload && systemctl start cloud-api-balance-monitor` 查看状态，后 `systemctl enable --now cloud-api-balance-monitor.timer`。初次保持发送关闭，完成币种与账号映射后再改true；下次运行读取配置。

状态位于 `status.json`；`alerts.sqlite3` 保存提醒记录。查询失败不表示余额不足，也不会清除已有低余额状态。同一低余额周期每个接收者24小时最多一次受理成功提醒；明确发送失败一小时后可重试，充值恢复后下一次低余额可再次提醒。

SMTP受理不等于最终投递。发送中断或超时结果未知时，持久保存unknown阻止盲重发；需根据邮箱记录按Message-ID核实，调用 `BalanceAlerts.resolve_delivery()` 处理。不要凭猜测把unknown改failed。持久状态要保留，删库会丢失防重发记录。

测试：`python -m unittest discover -s tests -p 'test_pool_balance*.py' -v`。
