# SageAttention 2.2 Linux / RTX4090 构建

使用官方 https://github.com/thu-ml/SageAttention，固定提交 `d1a57a546c3d395b1ffcbeecc66d81db76f3b4b5`。官方 setup.py 允许 `TORCH_CUDA_ARCH_LIST=8.9`，因此编译无需 GPU。没有使用第三方来源的二进制 wheel。

当前实际构建为临时 `ocv-h3-sage-builder` 容器：来自原 `pytorch-env:2.11-tts25-rayfix2`，无 GPU 挂载、无模型挂载，仅挂载 `/opt/ocv-h3`，CPU2、内存6GiB限制，MAX_JOBS=1，EXT_PARALLEL=1，nvcc threads1。TTS容器和原镜像未修改。

Ubuntu24.04编译器依赖：build-essential、cuda-nvcc-13-0、cuda-cudart-dev-13-0。Python3.13，torch2.11/cu130，ninja1.13.2。wheel保存在 `/opt/ocv-h3/build/wheels`，安装到H3专用 `/opt/ocv-h3/venv`。

编译成功后将工具链层保留为独立H3镜像 `ocv-h3-runtime:torch2.11-cu130-sage2.2`，保证Triton运行时也能使用C编译器；模型、输入输出和venv仍保持现有挂载。`runtime-image.json`记录image ID、source commit和wheel SHA256；`compose-before-sage.yaml`保存原配置。Dockerfile提供可重建步骤，需在构建宿主保留对应base镜像。

`finalize_builder.py`只会重建已停止的H3容器，不会启动H3、停止TTS或验证GPU。纯CPU包导入与GPU算子执行是不同验收层级，GPU smoke由主调度流程在TTS已drain并释放资源后进行。
