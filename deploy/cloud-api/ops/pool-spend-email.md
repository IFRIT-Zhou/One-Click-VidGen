# 每新增20美元上游消费邮件

用户确认：从启用时起，统计所有用户合计产生的 RunningHub＋ICAN 上游实际费用，每新增20 USD向 `942700596@qq.com` 提醒。旧低余额timer已停用，不再按30余额提醒。

`cloud-api-spend-monitor.timer` 每5分钟检查一次。提醒线为20、40、60……美元；同次发现跨多条线只发一封（累计45则说明达到40，余5继续计入），不补发历史邮件。金额以十进制精确计算，数据库幂等，重启保留累计。

## 数据口径和未完成项

- ICAN：`GET /v1/usage` 的 `usage.total.actual_cost`，必须 `unit=USD`。每个key首次成功查询时建立累计基线，不算历史，后续只记增长；若首次查询失败，恢复前的消费无法自动回溯计入，应手工核对。统计回退锁定unknown待核对，不能重置后重复累计。余额和供应商内部 `account_cost` 均不参与计算。
- ICAN统计覆盖所配置key的所有调用。若key还用于其他系统，其他系统费用也会计入；严格仅统计本平台时应使用专用key。
- RunningHub：任务完成时保存白名单 `upstream_usage`，含结算模式及原始费用字段。当前官方示例为POINT，美元单位未确认，不能用现有混合单位的actual_cost_credits或0.12元售价反推。**这部分尚未计入美元累计**，状态显示待核对；待获得账单或明确换算规则再补算，不填零。
- 如后续已有经过确认的任务 `request_json.upstream_actual_cost_usd`，runner会按任务ID幂等纳入总账；该字段绝不能由客户端输入直接信任，须由后台对账或可信适配器写入。
- 邮件遇到来源未核对会明确金额仅包含已确认费用。当前功能不是已完整覆盖两家全部费用。

## 部署与运行

新模块：`pool_spend_alerts/sources/email/runner.py`；同时部署RunningHub adapter和worker的原始费用捕获改动。创建 `/var/lib/cloud-api/pool-spend`，属主cloudapi，目录700。部署systemd两单元。`/etc/cloud-api-spend-monitor.env` 权限600：

```ini
POOL_SPEND_EMAIL_ENABLED=true
POOL_SPEND_RECIPIENT=942700596@qq.com
```

独立监测复用现有SMTP配置。首次运行建立 `baseline.json` 与 `spend.sqlite3`，不可删除或重置；删除会丢失基线、累计与防重发记录。状态看 `status.json`。worker捕获改动需在空闲时重启 `cloud-api-worker`；API与GPU模型不需要重启。

SMTP明确失败默认一小时后重试；提交结果不明持久记unknown，阻止后续提醒直到按Message-ID对账，避免重复邮件。受理成功不等于收件箱实际投递。

验证：`python -m unittest discover -s tests -p 'test_pool_spend*.py' -v`。
