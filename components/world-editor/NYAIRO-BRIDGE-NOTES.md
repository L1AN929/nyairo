# world-editor 本地改动说明（NyAiro WorldGen v0.2 桥接）

本目录的编辑器来自独立的 nyairo-world-editor v0.4（Apache-2.0）。为接入
WorldGen v0.2 的双向桥接，对 vendored 副本做了以下**最小**改动；除此之外
与上游 v0.4 一致，其自带测试套件（39 项 Python 测试）保持通过。

## 改动清单

1. `server.py` —— 新增 `POST /api/nyairo/export`（mutation，沿用既有的
   `X-Nyairo-Editor: 1` CSRF 标记与 Host/Origin 校验）：

   * 请求：`{"package": <当前草稿 v3>, "original_dir": "<原始 WorldGen 世界包目录（绝对路径）>"}`
   * 行为：调用 `components/worldgen/editor_bridge.import_from_editor`，
     以**原始世界包为基础**做差量回写，输出到
     `<原始包名>-edited`（已存在则 `-edited-2`、`-edited-3`…），原始包永不覆盖。
   * 明确错误（不假装 v3 包含全部世界信息）：
     * 未提供 `original_dir` → 422；
     * 目录不是 WorldGen 世界包（缺 world.json / MANIFEST.json）→ 422，
       并提示"编辑器 v3 文件不包含 NPC、环境事件等完整世界信息"；
     * 草稿 `meta.id` 与原包 world_id 不符 → 422（不是该世界的转换结果）；
     * 回写后世界包结构校验失败（如删除道路导致不连通）→ 422，逐条列出问题。

2. `web/index.html` —— 导航栏新增「导出 NyAiro 世界包 ⤴」按钮。

3. `web/app.js` —— 按钮处理：弹窗输入（并记忆）原始世界包目录，调用上述
   端点，toast 显示回写结果（地点/道路/家具数量、结构校验、是否可交 rc9
   加载、本次增删摘要、室内场景与摆位计数，以及"室内几何与家具交互是
   编辑器本地能力，rc9 不消费"的提示）。

## 与上游的关系

* 该端点只在**本机回环**上工作，且要求仓库目录结构（需要找到
  `components/worldgen`）；脱离仓库单跑编辑器时端点会返回明确错误，
  其余功能不受影响。
* 编辑器自己的格式、运行时、存储、导入导出行为没有任何修改。
