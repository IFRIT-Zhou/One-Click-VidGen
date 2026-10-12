# H3 模型加载耗时与 WSL 原生缓存

2026-10-12 只读核实：G: 是 Predator SSD GM7 4TB NVMe 的 NTFS 分区，
不是机械盘。容器当前通过 `/mnt/g` 的 WSL 9p/DrvFS 读取模型，挂载协议
`msize=65536`。RayWorker 的 ext4.vhdx 位于 D:\wsl\ubuntu2404，D: 是另一块
NVMe 盘。WSL ext4 逻辑空闲约886GiB，D: 物理空闲约602GiB。

当前高速工作流只依赖四个文件，共44,398,800,775字节（41.35GiB）：

| 相对路径 | 字节 |
|---|---:|
| diffusion_models/minimaxH3/minimax_h3_fastvideo_vsa_datafree_1300step_4step_int8_convrot.safetensors | 22898594920 |
| text_encoders/qwen3vl_32b_minimax_h3_nvfp4_awq.safetensors | 15687142551 |
| vae/minimax_h3_video_vae_fp16.safetensors | 5207808496 |
| vae/minimax_h3_audio_vae_fp32.safetensors | 605254808 |

首选优化是将这四个文件复制到 `/opt/ocv-h3/models-cache` 原生 ext4。
复制是额外占用约41.35GiB磁盘，原G:文件保留；不是把两套模型常驻内存。

`cache_fast_models.py` 默认只输出计划，`--copy` 才执行。它要求H3容器停止、
没有未解决H3任务；TTS容器可以继续运行。生命周期锁只在初始检查期间持有，
不会因41GiB复制长时间占用。复制标记阻止H3启动，但不阻止TTS启停。
每10秒检查TTS队列和活动任务；一旦TTS有任务或控制面不可用，暂停文件读取，
等TTS空闲再继续。逐文件边复制边计算源SHA256，再独立读取目标核对SHA256，
保存manifest。它不会启动、停止或重建容器。

实际复制优先运行 `python3 /opt/ocv-h3/cache_native_models.py`，同时保留
`cache_fast_models.py` 作为校验和TTS避让工具。原生复制通过Windows FileStream
读取G:，经 `wsl.exe` 标准输入写入ext4，绕过9p文件读取；源端流式SHA256、
接收端SHA256、目标文件独立复读SHA256三者必须一致才改为正式文件。
两端脚本只接受上述固定四个文件。中断遗留 `.partial` 会保留为 `.aborted-*`，
不自动删除原始资料。完成后的manifest可用于跳过已验证文件。

2026-10-12小规模64MiB对照：WSL mmap读取8.03秒、普通read9.77秒，
Windows原生读取0.021秒、原生流转发到WSL0.203秒。这是小块样本，不代表
模型实际加载或全量复制耗时；它说明应先绕过跨系统文件读取路径。

全量实测：原生流方式四文件复制及独立SHA256复核分别用时128.95、88.29、
31.79、5.88秒，合计254.91秒（约4分15秒）。所有四文件校验一致，manifest已保存。
这是一次部署复制耗时，不是每次启动或生成耗时。

完成后生成 `compose.models-cache.yaml`，只替换 `/models` 的来源为原生缓存。
在调度器停止且原H3容器停止后，先检查合并配置，再只创建容器：

```sh
cd /opt/ocv-h3
docker compose -f compose.yaml -f compose.models-cache.yaml config
docker compose -f compose.yaml -f compose.models-cache.yaml create --force-recreate h3
docker inspect ocv-h3-comfyui
```

确认 `/models` 只读指向缓存、GPU和其他绑定保持正确后，才恢复调度器或进行实测。
回滚只需停止空闲H3后用原 `compose.yaml` 重新create；G:源文件没有被改动。
此缓存只有高速版所需模型，不能声称 aiwood 工作流也已缓存；需要aiwood时另行规划。

ComfyUI 0.38.2 当前默认使用动态显存和aimdo safetensors mmap路径。
`--disable-mmap` 仅在非aimdo的备用加载分支生效，不能指望单独添加它解决当前问题。
`--fast-disk` 在9p上强制磁盘加载也未验证，暂不启用。
WSL总内存约31.3GiB，四文件约41.35GiB，因此不能简单启用 `--high-ram` 或
禁用动态显存来强行一次装入内存。

验证应分别记录服务可访问时间、首个视频从提交到成功的总时间、同一容器中
第二次不同提示词的时间，再记录停止重启后的时间。容器启动耗时不等于模型加载耗时；
模型加载改走ext4会减少跨系统读取开销，但未测量前不承诺倍数或秒数。
