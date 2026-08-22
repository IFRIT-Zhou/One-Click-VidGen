# OCV 插件目录

这里是 One-Click VidGen 为社区扩展预留的插件目录。

当前版本属于安全框架阶段：OCV 只读取各插件的 `plugin.json` 并展示基本信息，不会导入或执行插件代码。把名为 `disabled` 的文件放在插件目录中，可将该插件标记为停用。

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
