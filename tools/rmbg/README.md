# OCV 原图分层：RMBG 抠图组件

在 OCV 的「视频生成与结果 → 云端 · 原图分层动效」中点生成预览即可。只有分层方案确实需要抠取不透明主体时，OCV 才调用本机 RMBG。不需要打开 ComfyUI，也不需要单独启动抠图服务。

## 安装与更新

1. **程序文件**：通过 OCV Launcher 的「安全更新」更新。此目录中的工作程序、模型结构代码、配置和依赖版本清单是软件的一部分，不需要从模型仓库手工复制 Python 代码。
2. **运行依赖**：在 Launcher 的「状态与快捷入口 → RMBG 抠图组件」检查。若已有兼容的随包 Python 和依赖，会提示无需下载；若缺少依赖或版本不兼容，确认后才下载安装。安装依赖不等于下载模型，也不会启动 ComfyUI。
3. **模型权重**：单独准备 [BiRefNet-general.safetensors（固定版本）](https://huggingface.co/1038lab/BiRefNet/resolve/4d000788a9698c7f8d67c8c6ce2b40c768f5b909/BiRefNet-general.safetensors)，放到下方位置。已有同一文件时无需重新下载。普通软件更新不会覆盖这个目录。

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
