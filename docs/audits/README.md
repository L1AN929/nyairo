# 历史审计与验收证据归档

本目录归档 2026-10 期间公开预发布（rc1–rc6）的体检、隐私复核、Hermes 补丁与测试证据。
这些文件是**冻结的历史记录**，不代表当前 `v0.7.0` 的验收结果；引用其中的数字前请先核对
对应版本与提交。

| 文件 | 内容 |
| --- | --- |
| [HEALTHCHECK.md](HEALTHCHECK.md) | 2026-10-13 体检与维护修复：rc1 更新元数据错配修正、`release_version_mismatch` 校验。 |
| [HERMES_FIXES.md](HERMES_FIXES.md) | 内置 Hermes 增量补丁清单与运行时 / 测试修复说明。 |
| [HERMES_UPDATE_REVIEW.json](HERMES_UPDATE_REVIEW.json) | 上游 Hermes 0.21.5 升级的源码比较评审（未进入发行）。 |
| [PRIVACY_REVIEW.md](PRIVACY_REVIEW.md) | rc5 隐私修复与旧配置升级步骤。 |
| [PRIVACY_TEST_EVIDENCE.json](PRIVACY_TEST_EVIDENCE.json) | rc5 隐私修复的实际命令、范围与结果。 |
| [REVIEW_REPORT.md](REVIEW_REPORT.md) | R16 / R17 审查过程说明（R16 数字已撤下独立通过认定）。 |
| [TEST_EVIDENCE.json](TEST_EVIDENCE.json) | R16 / R17 宿主与组件测试证据摘要。 |
| [UPDATE_TEST_EVIDENCE.json](UPDATE_TEST_EVIDENCE.json) | rc6 整包更新与失败恢复的测试证据。 |

当前版本的安装、更新与验收说明见仓库根目录的 [INSTALL.md](../../INSTALL.md)、
[UPDATE.md](../../UPDATE.md) 与根目录归档的发行记录。
