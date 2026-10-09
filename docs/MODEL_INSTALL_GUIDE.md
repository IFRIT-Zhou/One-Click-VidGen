# OCV 本地模型安装

模型是可选的。使用视频 API、外部 ComfyUI 或集群配音时，无须下载相应本地模型。
在 OCV 选择内置 ComfyUI 后，生成设置旁会显示模型状态；点击“模型安装指引”可查看当前工作流的缺项、下载页及准确路径。在本地 TTS 安装窗口也有相同入口。

## 目录

将模型包中的 models 文件夹合并到 OCV 根目录，与 OCV_Launcher.exe 同级：

```
OCV/
  OCV_Launcher.exe
  models/
    comfyui/
      diffusion_models/minimaxH3/
      text_encoders/
      vae/
      loras/minimax-h3/
      latent_upscale_models/
    tts/
      indextts25/
        config.yaml
        gpt.pth
        codec.pth
        s2mel.pth
        qwen0.6bemo4-merge/
        hf_cache/
```

不要解压成 OCV/models/models。所有模型保留原文件名；ComfyUI 下载源内的目录可能与 OCV 工作流不同，请以安装指引显示的目标路径为准。

## 按需选择

- **IndexTTS-2.5**：需要本地配音时下载整个 `tts/indextts25`，包含情绪模型和 `hf_cache` 中的辅助模型。仅下载主权重不能完成离线配音。
- **H3 高速采样**：下载清单中 `ocv-h3-managed-v1` 的文件。
- **H3 aiwood 黑科技**：下载清单中 `ocv-h3-aiwood-v1` 的文件。
- 同时使用两个 H3 工作流时，共用文件只下载一次。

模型文件齐全仅说明文件检查通过，是否能运行还取决于显卡、显存、内存和生成尺寸。请先用较低分辨率、短镜头试跑；无需为了使用 OCV 下载所有模型。

补齐后点击“重新检查”，再开始生成。日常检查文件存在、非空和已知大小。内置 ComfyUI 的“完整校验文件”会在后台核对发布方 SHA-256，适合怀疑下载损坏时使用；暂无校验值的文件会明确标注。校验不会运行模型或占用显存。

## 旧安装兼容

引擎组件位于 `comfyui/engine`，拓展节点位于 `comfyui/custom_nodes`，本地 TTS 组件位于 `tts`。所有配套压缩包统一解压到 OCV 根目录。旧版 `runtime/comfyui/models` 仍会读取；已有外部 ComfyUI 模型目录可在内置引擎的高级设置中复用。
旧版 `tools/IndexTTS25/checkpoints` 继续兼容。若设置了 `INDEXTTS25_MODEL_DIR`，该配置优先；留空则自动识别统一目录或旧目录。搬移整个 OCV 文件夹后，默认模型目录随之变化。
Launcher 更新保留 `models`；运行环境和模型分开维护。

## 维护者制作模型包

`tools/prepare_model_library.py --copy` 会将当前已安装的工作流模型和 TTS 权重复制到根目录 `models`，生成 `model-packs.json`，列出三种安装选择及共用文件。默认不带 `--copy` 时只整理目录和清单。
请在复制完成后上传模型目录；不要上传 `.partial` 文件。可按清单分别压缩，但每份压缩包仍保留 `models/` 这一层，使用户解压到 OCV 根目录即可。
模型下载入口来自各模型发布仓库；对外重新分发时保留各模型许可及署名文件。此工具不进行网络上传。

制作不含这些可选权重的整合包时，使用打包工具的 `--without-models`：保留 TTS 与内置 ComfyUI 引擎环境，排除统一目录及旧目录下的可选权重。字幕识别等基础功能所需模型不在此排除范围内。
