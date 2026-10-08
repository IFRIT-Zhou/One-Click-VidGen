# 内置 ComfyUI 用户节点

将插件文件夹放在这里，例如 `comfyui_plugins/comfyui-SelfLift/__init__.py`。
避免多套一层压缩包目录。此目录由内置引擎额外加载，OCV 更新保留用户文件。

安装后，在 ComfyUI 工作台关闭并重新启动内置引擎，点击“重新检查节点”。
检查以引擎实际注册的节点为准；文件夹存在不等于插件加载成功。

aiwood 工作流的可选 SelfLift 依赖：
<https://github.com/facok/comfyui-SelfLift>
本机已验证版本：`835c3cf7919410f71f414688f049c6ca497789d0`。
OCV 不捆绑此插件源码。缺少它时，高速采样工作流仍可使用。

第三方节点会执行 Python 代码，请从可信作者获取。不要重复安装内置环境已有的
同名节点。缺依赖时查看工作台日志，并使用当前内置引擎的 Python 安装所需依赖；
不要安装到 OCV 主环境。模型仍放在 `models/comfyui` 中。
