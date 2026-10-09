# 内置 ComfyUI 旧版用户节点目录

新安装请放在 `comfyui/custom_nodes`，例如 `comfyui/custom_nodes/comfyui-SelfLift/__init__.py`。
本目录仅保留旧版兼容；已有节点仍会加载，OCV 更新保留用户文件。

安装后，在 ComfyUI 工作台关闭并重新启动内置引擎，点击“重新检查节点”。
检查以引擎实际注册的节点为准；文件夹存在不等于插件加载成功。

aiwood 工作流的可选 SelfLift 依赖：
<https://github.com/facok/comfyui-SelfLift>
本机已验证版本：`835c3cf7919410f71f414688f049c6ca497789d0`。
OCV 不捆绑此插件源码。缺少它时，高速采样工作流仍可使用。

第三方节点会执行 Python 代码，请从可信作者获取。不要重复安装内置环境已有的
同名节点。缺依赖时查看工作台日志，并使用当前内置引擎的 Python 安装所需依赖；
不要安装到 OCV 主环境。模型仍放在 `models/comfyui` 中。
# 旧版节点目录

新安装请使用 `OCV/comfyui/custom_nodes`。本目录仅为旧版兼容保留，OCV 仍能读取此前安装的节点。
