# 内置 ComfyUI 可选组件

将 ComfyUI 功能包解压到 OCV 根目录，形成 `comfyui/engine` 和 `comfyui/custom_nodes`。
引擎包含独立 Python；拓展节点放在 `custom_nodes/节点名称/`，模型统一放在根目录的 `models/comfyui`。
OCV 工作台会检查组件、模型和工作流节点。安装节点后重新启动内置引擎。
`engine` 内部的版本目录由 OCV 管理，用户无需手动修改。OCV 主程序更新会保留整个组件目录。
