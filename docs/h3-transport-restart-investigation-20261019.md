# H3 长时间等待与 Windows 重启：深入核查及修复

所有现场时间为北京时间（UTC+8）。本次核查对象为 2026-10-18 23:50:45 创建的 `h3job_622d53ed370d4542b67f563dc7696da4`，请求 1280×720、11 秒、277 帧。

## 两个问题的结论

1. 创建到认领相隔 553.118 秒：23:50:45.987 创建，23:59:59.105 认领，00:00:04.495 ComfyUI 收到 prompt。同时间窗没有查到其他重叠的云端 H3 任务；WireGuard 心跳持续成功。原调度代码丢弃后台操作的具体异常，journal 没有保存该窗口逐次检查结果，因此不能事后确定这 9 分钟具体卡在哪个调用，也不能说是排在其他用户任务之后。
2. Windows 整机确实重启：00:02:07.476 User32 1074 记录 StartMenuExperienceHost.exe 代表 Administrator 请求重启；00:02:24 关闭、00:02:41 启动。用户确认无人操作。进程名只能确认系统记录的请求入口，不能证明是谁或哪个软件触发了入口。

未发现同窗口的蓝屏、异常断电或 Windows Update 重启证据。检查了现行保活/启动包装脚本、NoMachine、AweSun、EFan 相关日志；未取得这些程序发起 Windows 重启的证据。NoMachine 在 00:02:08 的登录记录晚于 1074，不能据此倒置因果。Windows 本地 ComfyUI 曾启动，但保留日志没有对应生成请求，不能认定它与这次云端任务争抢显卡。

## 已证实的内存和通信故障

00:00:29，WSL 内核在 `vmbus_alloc_ring -> vmbus_open -> hvs_probe` 路径出现 order:7 分配失败（约 512 KiB 连续内存）；free 约 162 MiB、匿名内存约 28.37 GiB，swap 尚有空间。00:01:29 WSL Relay 记录 `UtilAcceptVsock` 等待异常。该事件发生在任务被认领之后，不能直接解释之前的 9 分钟等待。

代码检查发现云端 SSH 已进入 Linux，却每次再调用 Windows 的 `wsl.exe` 返回同一 WSL 执行节点脚本。提交、轮询、结果读取因此额外依赖 Windows/WSL interop。加上节点阶段锁原来无限等待，通信或锁持有者异常时有长时间卡住的风险。这些是本次修复的明确设计问题，但没有历史证据证明它们就是 553 秒等待的唯一原因。

WSL 可用上限约 31.26 GiB，独立 11 秒测试期间容器最高约 29.27 GiB，内存余量偏小。没有证据建立“内存压力触发 StartMenuExperienceHost 整机重启”的因果链；本次未调整 WSL 内存、ComfyUI 内存策略或重启机器。

## 本次改动

- `Node3Client` 改用已建立的 Linux SSH 通道，执行固定命令 `sudo -n -- /usr/bin/python3 -I /opt/ocv-h3/node3_agent.py`。请求通过 stdin JSON 传递，业务字段不进入 shell 命令。
- `install_native_transport.py` 仅授权这一固定脚本及固定参数；校验脚本路径 root 所有、不可由普通用户写入、无符号链接，使用 visudo 验证后安装独立 sudoers 文件。任意 Python `-c` 命令实际测试被拒绝。
- 节点阶段锁最多等待 30 秒，超时返回 `NODE_PHASE_BUSY`；不抢锁，也不执行原请求的副作用。health 继续独立读取。
- 调度器记录 health、prepare、claim_execute 阶段及耗时；传输记录 action、关联 ID、耗时、白名单错误。日志不输出提示词、JSON 请求、原始异常、stdout/stderr 或凭据。
- 健康采样记录 boot_id、内存和 swap 数据；journal 保留每次结果，避免只有被覆盖的最新状态文件。
- Windows 启用进程创建成功审计（4688），Security 日志从 20 MiB 增至 128 MiB，保持循环覆盖。未启用命令行内容审计。实际核验已有新事件，命令行字段为空。此设置改善未来取证，不能补回故障时段的进程链，也不保证所有 UI 重启操作都能据此定位。

## 部署顺序与回滚

新安装必须先把节点适配器部署到 `.3` 的 `/opt/ocv-h3/node3_agent.py`，确保 `/opt`、`/opt/ocv-h3` 和脚本均由 root 控制，再以 root 运行 `install_native_transport.py`。先用固定命令分别请求 health/status 成功，再更新 `.1` 的 Node3Client。未安装授权时直接失败，不回退到 Windows 绕行。

本次只在 H3 队列无未完成任务时重载 `ocv-gpu-scheduler`；未重启 Windows、WSL、GPU 容器、Ray 或 TTS，也未重提客户任务。线上存在其他版本差异，因此先取现场源文件定点合入本次逻辑，未用仓库整文件覆盖其他功能。

- `.3` 原适配器：`/opt/ocv-h3/backups/deep19-20261019T021317Z/node3_agent.py`。
- `.1` 调度器及第一版传输备份：`/opt/cloud-api/backups/deep19-20261019T021349Z/`；健康循环：`/opt/cloud-api/app/backups/deep19-20261019T021349Z/cloud_gpu_scheduler.py`。
- Linux 直连切换前传输：`/opt/cloud-api/backups/deep19-20261019T022441Z/node3_client.py`。回滚直连时先在无任务窗口恢复此文件并重载调度器，再移除 `/etc/sudoers.d/ocv-h3-agent`，用 visudo 检查。
- Windows 审计与日志容量的修改前备份、回滚脚本：`C:\ProgramData\RayWorkerReliability\restart-evidence-20261019`。回滚脚本分别恢复原先审计关闭状态和日志容量。

## 验证

节点测试 29 项、专用调度器 39 项、传输 5 项、GPU 调度器 8 项通过；真实 Linux 锁竞争在约 0.301 秒的测试期限返回，未进入受保护操作。生产候选版本的调度器测试另行验证，保留未上线功能差异。

管理员独立测试任务 `h3job_7282bf9ccfa54003a89a7920247f9a26`：10:15:50 创建、10:15:55 认领（5.043 秒）、10:18:43 成功。ffprobe：1280×720、24 fps、11.541667 秒。期间未发现新的对应内核分配错误，节点 boot 和容器启动时间未变化。此样本在诊断及锁修复后、直连传输切换前完成。

直连后的公网测试 `h3job_1faa0d075bd849b1b1e4d9bda2ed0276`：10:25:23.140 创建、10:25:25.811 认领（2.671 秒）、10:28:08.831 成功。ffprobe：1280×720、24 fps、11.541667 秒、12,781,644 字节。提交、轮询、结果均通过新路径。结束后 boot 仍为 `47e265b8-aa80-4205-9763-4de4e23e64d6`，容器 StartedAt 仍为 `2026-10-18T16:05:26.408862998Z`，OOMKilled=false；TTS ready=true、队列和执行数均为 0，cloud-api active。

这些结果证明修复后的链路可工作，不能据此宣称 Windows 自发重启根因已根治，也不能保证所有分辨率和全部客户镜头均通过。
