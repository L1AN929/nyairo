# Agent Note: v0.3.0 发行版本指针更新（取代源码-only限制）

Status: implemented

## Problem

v0.3 上一轮只推源码，[源码推送边界](2026-10-17-v03-source-publication-boundary.md)要求安装入口继续固定 v0.2.0-rc3。现在要把 v0.3.0 做成 GitHub prerelease 发行，安装器、官网与文档里的“当前版本”必须同时指向 v0.3.0；但不能把隔离环境里的新装与升级验证写成对现有实例或生产的升级验收，不能删除旧资产，也不能改写 v0.2 的真实历史测试记录。

## Decision

只改版本指针与发行说明：`scripts/release.json`、`scripts/bootstrap.sh` 与 `website/install.sh`（两份逐字节一致）、`README.md`/`INSTALL.md`/`UPDATE.md`/`KNOWN_ISSUES.md`/`docs/USER_GUIDE.zh-CN.md`、`website/README.md`/`website/USER_GUIDE.zh-CN.md`/`website/AGENT_INSTALL.zh-CN.md`/`website/index.html`/`website/docs-data.js`。产品代码、`website/app.js`、`website/style.css` 不动，`MANIFEST.json` 由发行流程最后生成。v0.3.0 一律标注 GitHub prerelease：不声明稳定，也不声称对现有实例或生产做过升级、真实模型一天或 24 小时稳定验收；隔离新装与 rc3 升级/回滚链的结果单独记在下面的发布回执里。旧 v0.2.0-rc3 标签、ZIP、下载页与历史验收段落保持原样。

本笔记只取代[源码推送边界](2026-10-17-v03-source-publication-boundary.md)中“未制作 v0.3 安装包、官网安装入口保持 rc3”这一条；[按停止线收束](2026-10-19-v03-bounded-feature-freeze.md)不变，产品仍冻结，不重开 Future/Agenda/UI 施工。

### 既有笔记检索

- [源码推送边界](2026-10-17-v03-source-publication-boundary.md)：部分重叠；白名单发布、单写者隔离与路径脱敏继续适用，只解除源码-only那一条。
- [版本停止线](2026-10-19-v03-bounded-feature-freeze.md)：继续适用；发行版本指针不重开功能施工，验收范围不变。

## Alternatives considered

- **继续只发源码、安装入口留在 rc3**：风险最低，不用处理安装实测、附件哈希与官网口径；但用户要的就是可下载的 v0.3 后端连续生活版，继续留 rc3 等于发行名不副实，否决。
- **宣布 v0.3.0 为稳定版并宣称已通过生产升级验收**：文案最省事，下载页也“看起来完整”；但那等于把隔离 uid 环境里的新装与升级链结果冒充成对现有实例的生产升级与稳定性验收，否决。
- **照抄整套 v0.2 安装说明再并列一份 v0.3 段落**：历史证据能全留；但同一文档出现两套“当前版本”，读者分不清装到哪一版，改为改指针并保留旧历史段落。

## Consequences

- 收益：仓库、官网、安装脚本与更新器对“当前版本”的说法一致；旧版本资产与历史验收记录没有被改动或删除。
- 代价：现有实例与生产环境的升级、真实模型一天与 24 小时长测仍未验收；发行说明必须继续写着，不能因为隔离链跑通就升级成“已稳定”。
- 本笔记记录的是候选树里的版本指针改动与隔离验证回执，不代表最终重建包已复验，也不代表远端已发行。

## 独立发布回执：隔离新装与 rc3 升级/回滚链

以下结果来自父级在隔离环境（普通账号 uid 1000）里的一次真实执行，不是对现有实例或生产的操作，也不代表最终重建包已复验：

- 隔离新装：普通账号、全新 profile，用真实 uv 锁定依赖完成安装，并执行 `scripts/check_update_runtime.py`，通过。
- 升级链：用真实公开 v0.2.0-rc3 包建立的 profile，经 `Updater.activate`（含实际 `install_runtime`）切换到新包；备份目录权限 0600，7 个个人文件逐字节保留。
- 回滚链：升级后新增一条 SQLite 聊天记录，再执行 `rollback`，个人数据与新增聊天记录均保留。
- 测试：3 项 metadata、2 项 profile、13 项 update 通过。7 项 bootstrap 中原有 1 项报红——新文案行尾双反斜杠导致 `--help` 检查失败；父级已修好 `scripts/bootstrap.sh` 与 `website/install.sh` 的 L20，最终包待重建后复验。
- 仍未验收：现有实例升级、生产环境升级、真实模型一天、24 小时稳定性，以及最终重建包的完整复验。
