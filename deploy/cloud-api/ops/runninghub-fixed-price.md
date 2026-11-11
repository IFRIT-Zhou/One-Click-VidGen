# RunningHub 图片固定售价

RunningHub 新图片任务统一售价为每张人民币 0.12 元，与分辨率无关。配置项为 `RUNNINGHUB_IMAGE_PRICE_CNY`，默认 `0.12`；当前尚无后台调价表单。钱包 1 积分对应人民币 1 元。

图片报价、预扣和成功结算使用相同售价。创建图片任务时在 request_json 写入 `runninghub_fixed_credits`；视频任务在提交时冻结 `image_billing_credits`，随后传给图片子任务。价格配置改变不影响已有任务。失败退还预扣，重复结算不会重复扣款。

上线前提交且没有固定售价字段的任务保留原有上游成本加价逻辑。ICAN 计费独立，不受本次变更影响。上游实际成本继续保存供核对，但不改变已冻结的用户售价。

部署需同时更新 config.py、services.py、api.py、worker.py、video_providers.py，并重启加载这些模块的 Cloud API、通用 worker、动态视频 worker，以及存在时的视频 worker。调价前检查活跃任务，避免打断工作。无数据库迁移。

验证：`python -m unittest discover -s tests -p test_runninghub_fixed_price.py -v`。线上 `/api/v1/image-pool/quote` 对 RunningHub 返回 `reserved_credits: 0.12`。
