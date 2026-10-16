# .20 TTS 修复记录与 EXE 修订交接

记录日期：2026-10-14，时间均为 UTC+08:00。对象：10.250.0.20 的 RayWorker / TTS 部署程序。

**线上容器已修复并完成真实配音验证；本记录不代表 EXE 已修改、打包或发布。**

## 1. 环境与故障边界

| 项目 | 实际值 |
| --- | --- |
| 节点 / GPU | 10.250.0.20 / RTX 5090，32 GB |
| 容器 / 原镜像标签 | `ray-worker-managed` / `pytorch-env:2.11-tts25` |
| Python | 容器内 `/home/hy/miniconda3/envs/pytorch2.11/bin/python`，3.13 |
| Ray / protobuf | 2.56.0 / 7.35.1 |
| 模型 | IndexTTS 2.5；挂载 `/space/models/IndexTTS2` |
| 实际服务代码来源 | Ray 下发的 `runtime_resources/py_modules_files/_ray_pkg_…/service` |

镜像标签存在、GPU 可用、Ray 节点注册成功，均不能证明模型副本能启动。此次阻塞发生在 Serve 副本初始化阶段，与公网 IP 查询无关。不能只检查旧 `/space/service`，也不能把“没在 working_dir_files 找到代码”当成服务未下发。

## 2. 已确认根因与已执行修复

### A. Ray 与 protobuf 描述符接口不兼容

原始错误：

```text
AttributeError: 'google._upb._message.FieldDescriptor' object has no attribute 'label'
ray/serve/_private/config.py → _proto_to_dict
```

Ray 原代码使用 `field.label == FieldDescriptor.LABEL_REPEATED`，当前 protobuf 实现没有 `label`，但有 `is_repeated`。

已用 [patch_ray_protobuf.py](patch_ray_protobuf.py) 修补：优先使用 `is_repeated`，旧描述符回退 `label`。脚本检查源代码形态、备份原文件，验证失败会回滚；未知源码拒绝盲目替换。没有升级或降级 Ray、torch 或模型依赖。此方案针对已核实的版本组合，不应无条件套用到将来的 Ray 版本。

验证：真实 `DeploymentConfig` 序列化往返、标量/重复/嵌套 protobuf 转换通过；重复执行返回 `changed=False`。

### B. 原镜像缺少当前服务依赖

兼容性修复后暴露 `ModuleNotFoundError: No module named 'minio'`；检查发现还缺少 `redis`。按 `.1` 当前运行版本补齐：

```text
minio==7.2.16
redis==6.2.0
```

清单：[tts-worker-service-requirements.txt](tts-worker-service-requirements.txt)。实际下发服务的 `config/audio/schemas/state/drain/storage/deployments` 导入验证通过。

### C. 修复保存位置与有效范围

- 容器内补丁位置：上述 Python 环境的 `site-packages/ray/serve/_private/config.py`。
- `.20` 宿主留存：`/opt/ray-worker/bin/patch_ray_protobuf.py` 和同目录 requirements 文件。
- 备份：`/opt/ops-backups/repair20-protobuf7-20261014`，另有容器内 `config.py.backup-protobuf7-*`。
- **现有容器 stop/start 保留修复；从原镜像删除后重建会丢失修复。原镜像归档未被改写。**
- 未通过容器快照生成新镜像；未改网络、WG、EFan 或模型文件。

供受控维护复用的命令（在保存脚本的 Linux 宿主目录执行）：

```sh
docker exec -i ray-worker-managed /home/hy/miniconda3/envs/pytorch2.11/bin/python -m pip install --disable-pip-version-check -r /dev/stdin < tts-worker-service-requirements.txt
docker exec -i ray-worker-managed /home/hy/miniconda3/envs/pytorch2.11/bin/python - < patch_ray_protobuf.py
```

以上不是 EXE 的完整实现。安装器应在工作负载接单前执行；已有任务时先按生命周期协议停止接单、等待任务完成。不得为了套用补丁直接删除用户容器。

## 3. 后续 EXE 必须集成的工作（待实现）

当前未定位并修改 EXE 工程。下列入口名来自本对话留存的 `work/pause/worker-agent`，是后续查找线索，必须与 EXE 当前打包的 Agent 源码核对后实现。

| 阶段 / 源码线索 | 需要加入的行为 | 通过标准 |
| --- | --- | --- |
| 镜像导入、`_load_bundle_image`、安装清单 | 绑定镜像 digest、服务版本、依赖清单和兼容补丁版本；新发行镜像构建时固化修复 | 清洁环境可部署，不依赖旧容器可写层 |
| `prerequisites` / `artifact_status` / `verify_gpu_runtime` | 分别检查 GPU、Ray/protobuf 真实序列化、所需包版本与服务模块导入 | CUDA/Ray 成功不能掩盖 Serve 初始化失败 |
| `start_managed` 及新建/复用/重建容器分支 | 确认使用目标容器 Python；兼容准备通过后才运行 Ray 工作负载；已有容器也检查 | 老安装升级和全新安装均可通过；重复执行不重复修改 |
| join/rejoin 与接单流程 | 模型副本加载、必要探测通过后才报告 TTS 可用；错误保留具体阶段和可操作提示 | 区分“Ray 已连接”“模型加载中”“模型启动失败”“可配音” |
| 修复失败和用户重试 | 有界重试，保留失败原因；失败时不自动 resume，不进入反复重启循环 | 断网、缺包、未知补丁源代码有明确提示与恢复入口 |
| 升级和回滚 | 保存版本与校验摘要；重新探测后再标记通过；打包离线依赖或提供可恢复下载 | 重建、重新导入原包、回滚均不会悄悄依赖旧修复标记 |

建议日志字段：阶段、镜像 ID、Python/Ray/protobuf 版本、服务包版本、补丁版本与校验摘要、检查结果、错误类别。不要记录令牌、完整环境变量或用户文本。可新增 `RAY_PROTOBUF_INCOMPATIBLE`、`TTS_SERVICE_DEPENDENCY_MISSING`、`TTS_MODEL_START_TIMEOUT` 等错误码；前两项是建议名，不是已上线接口承诺。

## 4. 验收结果与后续回归用例

已执行：

- 两个 TTS 副本均运行在 `.20`，实际调度目标与生命周期目标一致。
- 真实短配音任务 `tts_f5345c8681b94328851d`：约 21.89 秒完成（含与其他任务并发时的等待），输出 7.941208 秒、24 kHz WAV，381256 字节。
- 音频 SHA-256：`00d77e4794ae128d0e4ffd913ae885d677bb0841e3b58e915639289662303f66`。
- 两条用户任务分别于 10:05、10:08 完成；后一条完成全部 20 个文本块。
- 10:13:17 健康快照：`ok=true`、`accepting=true`、`draining=false`，两个模型副本就绪。
- 本次修复没有重启 Ray head，控制 actor ID 保持不变。

EXE 发布前尚需验证：全新导入原归档、复用旧容器、容器重建、WSL/Windows 重启、重复安装、无网/依赖安装中断、未知 Ray/protobuf 组合、补丁失败回滚，以及最终真实配音。此次没有为了验收而销毁线上容器，不能把实测成功等同于这些场景已通过。

## 5. 关联问题，避免误归因

1. **繁忙时健康假超时尚未修复**：模型部署 `max_ongoing_requests=1`，健康请求和推理共用队列；dispatcher 健康探测只等 5 秒，但真实单段推理可达 20–32 秒。日志证明超时时仍持续生成，推理后健康请求约 1 毫秒完成。这需要修改 TTS 服务的探测设计，EXE 不能据此自动重启，也不能仅因有 active job 就认定健康。
2. `.3` H3 / `.20` TTS 分工属于云端调度变更，见 [dedicated-gpu.md](dedicated-gpu.md)。2026-10-15 已为 `.3` 现有 Agent 安装调度角色保护；EXE 后续仍需集成，更新 Agent 时不能覆盖掉该保护。`.20` 配音可用时 `.3` 常驻 H3；`.20` 配音明确不可用时 `.3` 按需轮转，空闲释放两个模型。这不代表 EXE 已修订或真实重启/故障切换验收已完成。
3. GPU 常驻与依赖修复是两件事。固化依赖不会自动保证调度策略、生命周期权限或重启顺序正确。

## 6. 证据索引

本对话工作目录 `outputs/` 下保留：

- `repair20-inspect.txt`：原始版本和可复现 protobuf 异常。
- `repair20-patch.txt`、`repair20-persist.txt`：兼容修补、留存与验证。
- `repair20-runtime3.txt`、`repair20-minio.txt`、`repair20-redis.txt`：实际服务加载路径和缺包修复。
- `repair20-performance.txt`、`repair20-progress.txt`、`repair20-healthcode.txt`：实际推理与健康假超时证据。
- `tts-dedicated20-20261014/result.json`、`test.wav`：真实测试结果和音频。
- `dedicated-final-20261014.json`：10:13 最终健康快照。

另保存交接快照 `outputs/worker20-exe-handoff-20261014/`，包含本记录、补丁、依赖清单、关键验收证据及 SHA-256 清单，便于后续 EXE 修订时复用。
