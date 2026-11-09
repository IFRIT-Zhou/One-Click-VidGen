OCV 本地原图定位组件（RapidOCR CPU）

用途
  给 Vibe 创作工具提供原图文字、检测框及识别置信度。不代替文案核对、
  医学数据核对或视觉审核；OCR 检测框不是逐像素字形边缘，也不保证一像素精度。

安装与自检（在 OCV 项目目录运行）
  runtime\python\python.exe tools\vibe_ocr\setup_runtime.py --check
  runtime\python\python.exe tools\vibe_ocr\setup_runtime.py --install

--check 离线，只检查固定版本的独立环境和权重；--install 才会访问官方 PyPI。
程序更新不会偷偷安装模型，推理也不会自行联网、下载字体或选择其他模型。
安装器将 RapidOCR 的固定官方 wheel 做 SHA-256 校验；权重来自该 wheel，
逐个校验后再激活。全部依赖版本记录在 runtime-spec.json。

目录与分发
  tools/vibe_ocr/        本项目源码，随 Launcher 代码更新。
  runtime/vibe_ocr/      私有 Python 环境与激活指针，不上传 Git。
  models/vibe_ocr/       哈希命名的权重目录，不上传 Git。
  每个任务自己的缓存目录 保存绑定原图哈希、模型清单和复核区域的 OCR JSON。
安装不修改 OCV 主 Python、ComfyUI 或其他引擎；旧组件环境保留，避免打断在途工作。
需要随包 Python 3.10，Windows CPU 环境；不需要启动 ComfyUI 或占用视频显存。

坐标契约
  全部坐标在应用 EXIF 方向后的完整原图上，单位为像素，box=[left,top,right,bottom]。
  局部复核 region 使用同一原图的归一化坐标，返回的框仍为完整原图像素坐标。
  confidence 是文字识别置信度，不是定位误差概率；低置信度应复核。
  ready + texts=[] 只表示这次没有检测到文字，不证明原图确实没有文字。
  unavailable 明确表示测量没有完成，不能把它当成内容缺失或审核通过。

上游项目
  RapidOCR: https://github.com/RapidAI/RapidOCR （Apache-2.0）
  ONNX Runtime: https://github.com/microsoft/onnxruntime （MIT）
  本组件不拷贝上游源码；安装分发请保留各依赖附带的许可证与声明。
