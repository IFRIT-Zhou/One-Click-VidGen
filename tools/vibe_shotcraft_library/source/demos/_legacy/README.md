# 旧工程兼容实现

此目录冻结 2026-10 镜头库下架的 66 个 demo 及它们使用的共享件，来源版本见 `manifest.json`。
只用于工作台解析旧的 `demo:<Stem>` cardId：不加入素材库、画廊或 Studio 的独立合成列表，
不迁移、替换或改写旧工程中的镜头、时序、变速与属性。

`manifest.json` 保存旧注册表的名称与默认帧数（包含过去没有导出时长的 demo）。
`workbench/scripts/gen-index.mjs` 生成隐藏的兼容索引；旧卡仅进入 `CARDS` 查找表，
不进入供素材库与独立合成注册使用的 `CARD_LIST`。

旧截图从这里的 `_textures/` 经 `workbench/public/legacy-textures` 提供，
与新版 `textures/live/`、以及用户链接的成片素材分开，避免新版截图改变旧镜头的布局。
源文件中将这些 `staticFile` 路径从 `textures/live/` 改为 `legacy-textures/`，
`StreamResponse` 的 JPG 背板也改从此命名空间加载（图片内容不变）。
不要为清理镜头库而删除这里的实现，否则旧 localStorage 存档、导入 JSON 与 CLI 导出会再次缺帧。
