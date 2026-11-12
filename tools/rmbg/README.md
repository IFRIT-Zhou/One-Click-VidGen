# OCV 原图分层：RMBG 抠图组件

在 OCV 的「视频生成与结果 → 云端 · 原图分层动效」中点生成预览即可。只有分层方案确实需要抠取不透明主体时，OCV 才调用本机 RMBG。不需要打开 ComfyUI，也不需要单独启动抠图服务。

## 安装与更新

1. **程序文件**：通过 OCV Launcher 的「安全更新」更新。此目录中的工作程序、模型结构代码、配置和依赖版本清单是软件的一部分，不需要从模型仓库手工复制 Python 代码。
2. **运行依赖**：在 Launcher 的「状态与快捷入口 → RMBG 抠图组件」检查并安装/更新。依赖安装到 `runtime/rmbg/versions` 专属环境，不再借用 ComfyUI 或主程序的机器学习依赖。首次下载 CUDA/PyTorch 等可能需要数 GB；不下载模型权重。新环境验证通过后才切换激活指针，旧环境保留供运行中的任务使用。Python 标准库由 OCV 便携 Python 提供，不继承其 site-packages；安装不会修改主程序 Python 或 ComfyUI。
3. **模型权重**：单独准备 [BiRefNet-general.safetensors（固定社区转换版本）](https://huggingface.co/1038lab/BiRefNet/resolve/4d000788a9698c7f8d67c8c6ce2b40c768f5b909/BiRefNet-general.safetensors)，通过 Launcher「可选组件与模型 → Vibe 动效 → 检查现有权重 / 导入文件」导入，或放到下方位置。导入会检查大小和 SHA-256，并保留源文件；已有同名文件不覆盖，校验不符时停止。此源由 1038lab 发布，不是原作者账号；当前不提供自动下载，也不将其他作者版本直接改名替换。普通软件更新不会覆盖权重目录。

从 OCV 根目录算起：

```text
tools/rmbg/
  worker.py                          抠图程序
  setup_runtime.py                   Launcher 调用的依赖安装程序
  runtime-spec.json                  支持的依赖版本
  vendor/birefnet/                   固定版本的模型结构、配置和许可
  models/BiRefNet/
    BiRefNet-general.safetensors     用户安装的权重，不随软件更新
```

权重文件大小为 **884,878,856 字节**；SHA-256 为
`77277264c0e8c74149d3ff2fade4fd8176965b7108f3c5fc3b8c9c811edb4519`。
文件校验失败时，请核对版本和完整性；不要用其他模型改名替换。

一次预览中的多个主体共用一次模型加载，结束后工作进程退出。抠图本身不使用云端模型；分层方案设计和另行授权的背景出图仍分别按所选渠道执行。
