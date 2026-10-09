# OCV 可选组件

所有官方配套包均以 OCV 根目录为解压目标。解压后 `OCV_Launcher.exe` 应与 `comfyui`、`tts`、`models` 同级；不要再额外套一层 OCV 文件夹。

```text
OCV/
  OCV_Launcher.exe
  plugins/                   OCV 插件，例如 Codex 制作桥
  comfyui/
    engine/                  独立 Python、内核和内置工作流（内部按版本管理）
    custom_nodes/            ComfyUI 拓展节点
  tts/
    IndexTTS25/              TTS 引擎、依赖和示例音色
    python/                  TTS 独立 Python
  models/
    comfyui/                 按工作流选择模型
    tts/indextts25/           TTS 主模型及辅助模型
```

基础便携版可不包含 ComfyUI 和本地 TTS，继续使用视频 API、外部 ComfyUI 和线上配音。
需要本地功能时，下载相应引擎包与模型包，解压到同一个 OCV 根目录，再在软件中检查组件状态。
第三方节点包使用 `comfyui/custom_nodes/节点目录` 层级。OCV 插件使用 `plugins/插件目录` 层级。

旧版保留 `runtime/comfyui`、`tools/IndexTTS25` 和 `comfyui_plugins` 识别；维护者可在停止本地引擎后运行 `tools/migrate_optional_components.ps1` 迁移。主程序更新保留组件、模型和用户配置。

## 整合包档位

以实际显存容量、内存、工作流、分辨率和时长标注测试条件，不以“3060 以上”“3080 以上”作为唯一门槛。
建议提供基础版、TTS 版、TTS + ComfyUI 版，并分别列出已验证的配置。3080 的 10GB/12GB 与 3060 的 12GB 不能按型号直接排序。
H3 的部分工作流需要更大显存；本地 24GB 显卡测试通过不等于 10GB 显卡也能运行。发布包应使用已验证的工作流参数。

本轮只调整本地目录和兼容逻辑；用户实测完成后再发布。可选包的压缩与分档将在下一阶段进行。
