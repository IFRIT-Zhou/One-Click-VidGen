# OCV 插件目录

已接入的可选工具：[Codex 制作桥](codex_bridge/README.md)。`production_bridge` 类型目前只支持该受控宿主扩展，提供草稿和分镜交接接口，不开放任意代码执行。

这里是 One-Click VidGen 为社区扩展预留的插件目录。

OCV 默认只读取各插件的 `plugin.json` 并展示基本信息，不会导入或执行插件代码。已接入的导演配置槽支持 `director_profile` 类型的纯 JSON 配置；尚不支持任意自定义模式 ID 或可执行入口。把名为 `disabled` 的文件放在插件目录中，可将该插件停用。

导演配置由当前版本的 `backend/app/director_extensions.py` 校验。启用后，相应入口才会出现在画面表达选择器中；禁用不删除已有素材，旧任务需要该配置重新规划时会明确提示，不会悄悄切换规划方式。插件开关修改后刷新页面即可更新入口。

维护者的本地扩展目录使用 `private_` 前缀，不纳入公开整合包；发布工具也会拒绝包含这些目录的提交。不要将私有论文、客户资料或密钥放入可分发插件。

## 最小目录结构

```text
plugins/
└─ your_plugin/
   ├─ plugin.json
   ├─ README.md
   └─ disabled       # 可选；存在时表示停用
```

`plugin.json` 示例见 `example_plugin/plugin.json`。插件 ID 和文件夹名称建议保持一致，只使用字母、数字、点、下划线和连字符。

`manifest_version` 当前固定为 `1`。未知字段会被忽略，便于后续在保持兼容的前提下扩展清单。

后续计划逐步开放图像模型（包括 ComfyUI）、TTS、Agent、提示词处理与渲染后处理等扩展点。在正式执行接口发布前，请不要依赖未公开的 OCV 内部模块。

第三方插件不代表 OCV 官方审核、担保或授权。插件作者应自行说明依赖、权限、许可证与数据处理行为。
