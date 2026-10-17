# NyAiro v0.3 官网本地内容更新结果

Status: implemented（本地内容已改完并验证；未提交、未推送、未部署）

## 1. 结论

`website/index.html` 与 `website/docs-data.js` 已在本地完成 v0.3「后端连续生活版」的窄更新：说明**源码已更新到 GitHub main、机制限定验收、安装发行包仍为 v0.2.0-rc3**，并把原“架构规划/生活作息与网页设置/主动发信/天气联动”等未实现内容明确移到未来、不再像本版已实现。安装命令、AI 安装 Prompt、rc3 发行 ZIP 路径全部保持原样；没有出现任何 v0.3 release/下载链接。未改 backend、未 stage/commit/push/tag/打包。

产物（本地最终字节）：

| 文件 | 字节 | sha256 |
|---|---:|---|
| `website/index.html` | 94199 | `459e7d201961fdaaf7425821caa1b0e1eaf2fd7987131a1c80e9217a726d3691` |
| `website/docs-data.js` | 93176 | `ad5bf48ea87a22c9c799d67ecc3929882fdfe71471e94b28cf93766e1a4393f4` |

`git status` 只多出这两个 `M`（其余 `M`/`??` 是本次 v0.3 产品工作原有未提交内容，没有动）。

## 2. 源站基线核对（先核对再改）

只读抓取（`C:\Windows\System32\OpenSSH\scp.exe` BatchMode，未写远端）HK `202.6.205.26:/var/www/nyairo` 到会话 scratch `remote-baseline/`：

| 文件 | 线上（部署版） | 本地 HEAD `7f09d26` 工作树 |
|---|---|---|
| `index.html` | 92701 B · `9729669660da6a73395a23aee9455567701c006b04d1bd62ca90cc6f19b6b4aa` · mtime 2026-10-16 20:18 | 92980 B · `d91db40b1c3e71715a4839caaa2e2b1ac00225ca04ce1ae05c3a8804abeaa2cf` |
| `docs-data.js` | 85928 B · `190a6bc89401662c827262c59fc7b7dc49ae5fa79a054e540b1c95cea14981a7` · mtime 2026-10-15 18:30 | 91483 B · `b82eca9758137211206e8ef686836f0be48a2880c5e0cbef0831fd5e7a924666` |
| `app.js`（未改） | 27644 B · 被线上 index 以 `?v=20261016-navfix-1` 引用 | 27133 B · 旧版，引用串仍是 `?v=20261013-healthcheck-2` |

差异（difflib，非 patch）：`index.html` 400 行、`docs-data.js` 89 行。线上是**更新的一版**——文案做过中性/诚实化（showcase 从“真实交互体验演示”改成“功能示意 · 非真实运行回执”、roadmap 用“当前版本/正在规划”的朴素说法、rc3 ZIP SHA256 为 `31149fb8be…`、app.js 增加移动端侧栏关闭与 `bootstrap()` 结构修复），对应 main 上 5 个“文档/展示”提交；本地工作树落后于这些提交。

**决策：以线上已部署字节为基线再做窄改。** 做法是把线上两份文件按字节复制回 `website/`（复制后本地 sha256 与线上完全一致，已核验），再只做 v0.3 相关窄改。若以本地旧版为基线加 v0.3 说明，父级推 main 时会把线上已发布的诚实文案与移动端导航修复一起回退。

- 备选 A（放弃）：直接在本地旧文案上加 v0.3 段落——实现最快，但会造成线上内容回退，且与 main 现有 5 个提交冲突。
- 备选 B（放弃）：借 v0.3 顺手重做首页/路线图视觉——越权（本次只授权内容窄改），且会破坏既有 inline CSS/DOM 与移动端收敛样式。

线上额外文件（`app.js-backup-*`、`index.html-backup-*`、`docs-data.js-backup-*` 等）是服务器侧历史备份，未读取覆盖、未删除，见第 7 节。

## 3. 实际改动（本地）

### `website/index.html`（12 个 diff hunk，全部为文本/链接，无 CSS、无结构新增）

- 顶部胶囊：`href` 指向 `#changelog-v0-3-0`，chip 改 `v0.3 · 源码`，标题行改“后端连续生活版 · 机制限定验收，发行包仍 v0.2.0-rc3”（L1115、L1119-1120）。
- hero 安装注记（`#nyairo-rc1-release`）：补“v0.3 源码已更新到 GitHub main（机制限定验收，尚未打包发行），安装命令与安装包行为不变，装的仍是 v0.2.0-rc3 发行包”，并加 `#changelog-v0-3-0` 链接（L1188）。
- 路线卡四步：`v0.2 · 当前版本`→`v0.2 · 当前发行包`；`v0.3 · 正在规划 / 生活作息与网页设置`→`v0.3 · 源码已更新 / 后端连续生活版`（L1505、L1513-1514）。
- v0.2 面板状态胶囊 `当前版本`→`当前发行包`（L1573）。
- v0.3 面板：状态胶囊 `正在规划`→`源码已更新`，副标 `v0.3.0-preview`→`v0.3.0 · 未打标签 · 未打包`；标题改“后端连续生活版：出门、等待、消费、留下经历、回家”；摘要写 0.2→0.3 的连续性差别与 51/0 跳过；三张卡改为实际机制（动态地点与真实路线 / 持久多段行程与暂停 / 街区生活与真实后果）；面板末尾新增一段“这一版只到源码 + 验收环境 + 明确还没做”，并链到 `docs/engineering/v03-vs-v02.md`、`docs/engineering/v03-final-acceptance.md`（L1610-1648）。
- `docs-data.js` 引用串 `?v=20261014-healthcheck-3`→`?v=20261019-v03-1`（L1780）。`app.js` 的 `?v=20261016-navfix-1` 保持线上原值，未改。
- 未动：安装命令、Agent 安装 Prompt（仍是“当前公开测试版本：v0.2.0-rc3”）、rc3 ZIP 下载与校验说明、showcase、四大能力卡、v0.1/v1.0 面板、footer、noscript 区块。

### `website/docs-data.js`（8 个 hunk）

- changelog：`summary` 补 v0.3 只更新源码；新增 toc 首项 `changelog-v0-3-0`；概览段改写为“最新发行包仍是 rc3，v0.3 源码已进 main、无标签/无 ZIP/无迁移”；正文新增 `<h2 id="changelog-v0-3-0">` 章节（11 条实际机制 + “验收范围与这版没做什么” callout + 三处文档/main 链接）。
- status-matrix：`summary` 从“nyairo v0.1 · 第一版功能与已知问题”改为“发行包 v0.2.0-rc3，v0.3 只更新了源码”；“可以使用”段补 v0.3 机制清单；“保留的边界”段补 v0.3 未交付（Web 配置台/主动发信与电话/天气/Agenda 到点想起/营养与完整情绪/193 项）；“证据与安装来源”段说明 main 含 v0.2 与 v0.3 源码、安装仍 rc3、验收范围与未覆盖面。
- roadmap：`summary` 补状态；toc `rm-next`→“尚未实现 · 模型与密钥搬进网页”、`rm-p3`→“v0.3 源码已更新 · 后端连续生活版（未打包）”；`rm-next` 正文加“v0.3 没有做这项”；`rm-p3` 正文重写为已交付机制 7 条 + 验收边界 + “下面几条还没做”，让原有“起居作息/深夜自发回家/随笔/主动找你”四条自然落到“后续愿景”列表里，而不是被当成已实现。

未新增 `website/V03_UPDATE.zh-CN.md`：changelog 章节 + 首页注记已经承载完整公开说明，再放一个没有被任何页面链接的 .md 只会成为源站孤立文件。

## 4. 验证（本地，全部通过）

- `node --check website/docs-data.js`：PASS。
- `website/index.html` 标签配平（python `html.parser` 全量栈匹配）：0 error、0 未闭合。
- 唯一一段 inline script 是 L43 的 `application/ld+json`（1065 字符），`JSON.parse` 通过；其余脚本都是外链（docs-data.js / app.js / CF beacon）。
- vm 加载 `docs-data.js`：`DOCS_TREE` 4 分类 20 项与 `DOCS_CONTENT` 20 篇一一对应、无孤儿；每篇都有 title/summary/content/toc；**所有 toc 锚点都能在对应正文里找到同名 id**；changelog toc 首项是 `changelog-v0-3-0` 且其后紧跟 rc3；index.html 内所有 `#锚点` 都能落到文档 toc 或页面 id（`#home` 由 app.js 特殊处理，另计）。
- 124 项断言脚本（scratch `verify_website2.cjs`）全通过：必需文本（v0.3 / 后端连续生活版 / 源码已更新 / 机制限定 / 51 项 / 两份 docs 链接）；禁止项（无 `releases/download|tag/v0.3`、无“v0.3 已发布/正式版”）；两份文件里所有 `L1AN929/nyairo/releases/**` URL 均不含 v0.3；`curl -fsSL https://nyairo.com/install.sh | bash` 至少两处仍在、Agent Prompt 仍锁 `v0.2.0-rc3`、rc3 ZIP 路径不变；4 个 `stage-step-btn` 与 4 个 `phase-panel` 及 `aria-controls` 对齐。
- 真实渲染（Edge `--headless --dump-dom`，`file://` 本地文件）：
  - 首页：渲染出“后端连续生活版 · 机制限定验收，发行包仍 v0.2.0-rc3”、四步 `v0.1 基础版本 / v0.2 当前发行包 / v0.3 源码已更新 / v1.0 远期构想`、侧栏 20 条链接、全文已无“正在规划”。
  - `#changelog-v0-3-0`：路由进 changelog 并渲染新章节、callout、目录项与两处 docs 链接。
  - `#rm-p3`：渲染新的 v0.3 章节，“还没做/后续愿景”出现在“深夜自发回家”等条目之前（顺序已按字节位置核对）。
  - `#status-matrix-section-0`：渲染新 summary、v0.3 段落与“没有真实模型一天、24 小时长测或生产部署验收”。

按授权未运行任何 backend 测试，也未触及 backend 文件。

## 5. 没验证 / 已知边界

- 未部署，因此未验证真域名 HTTP、Cloudflare 缓存与 `?v=` 版本串在 CDN 上的表现；上述渲染都是本地 `file://`。
- 未做人眼像素核对与移动端截图比对：只确认未改 CSS 与 DOM 结构，新文本都落在既有 `.banner-title` / `.hero-install-note` / `.phase-summary` / `.phase-sub-meta` 容器里，靠既有 `flex-wrap` 与 `max-width` 折行；深色/浅色主题未改样式。
- 仓库内 `website/app.js` 仍是本地旧版（27133 B），而 index 已按线上引用 `?v=20261016-navfix-1`；两者版本串暂时不一致，见第 6 节。
- 线上 `app.js` 等多出的 `*-backup-*` 文件、以及站点其余页面未做任何处理。

## 6. 部署白名单建议（给父级，不是我执行的）

1. 只覆盖两个文件：`website/index.html`、`website/docs-data.js` → `/var/www/nyairo/`。
2. **不要覆盖**线上 `app.js`（27644 B，`navfix-1`，含移动端侧栏关闭与 `bootstrap()` 修复）、`style.css`、`install.sh`、`USER_GUIDE.zh-CN.md`、`AGENT_INSTALL.zh-CN.md`、`sitemap.xml`、`robots.txt`、`assets/`、其他 `*.html`。
3. 若父级顺手把仓库 `website/app.js` 也同步到 main，请确认它就是要部署的 `navfix-1` 版本（远端 27644 B）再发布，否则会回退线上已修的移动端侧栏行为；仓库版 `app.js` 的版本串与 index 引用不一致只是仓库内部短暂不一致，不影响已部署站点。
4. 部署前在 HK 侧先留 `<文件名>-backup-<时间戳>`；不要删除线上既有 `*-backup-*`（不属于本任务清理范围）。
5. 不建议本次创建 v0.3 release/tag/ZIP：官网文案已明确“无标签、无发行包”。

## 7. 备注

- 本次没有写独立的过程 note：决策与备选已在本文件第 2 节按 Problem/Decision/Alternatives/Consequences 形式记录；`docs/engineering/v03-website-update-result.md` 为本次自有结果文档。
- 证据文件都在会话 scratch（`remote-baseline/`、`diff-index.html.diff`、`diff-docs-data.js.diff`、`dom-*.html`、`verify_website2.cjs`、`check_html.py`、`status-matrix.json`），未放进工作区。
