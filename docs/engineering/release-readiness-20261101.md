# 0.5 本地发布准备（2026-11-02 · 最终白名单与归档排除）

- 现场：HEAD `94cb734b`、index 空、分支 `work/rc1-phone-world-body`。`git status -uall` 展开 459 条未提交变化 = include 349 / exclude 105 / defer 5；这是工作树变化白名单，不是整包全文件清单（包内 13112 个文件由 `MANIFEST.json` 描述）。
- 14 项暂缓逐个结论（明细见 `.agents/worker-runs/20261102-release-whitelist.json`）：
  - **纳入 6**：`scripts/history_choice_experiment.py`、`scripts/history_choice_natural_v2.py`（被 `g2_next_offline.py`、`g2_natural_dual_choice_probe.py` 顶层导入，排除即缺 import）、`tests/test_history_choice_experiment.py`、`tests/test_history_choice_natural_v2.py`、`tests/test_g1_batch_c.py`（只依赖已纳入的 `g1_real_adoption`）、未落地恢复权威提案（仅作 proposed 文档保留，不表示已实施）。
  - **暂缓 5**：`scripts/g1_next_live.py`、`scripts/g2_next_live.py` 两个真实模型实验入口，以及顶层依赖它们的 `test_g1_batch_f.py`、`test_g2_live_a.py`、`test_g2_live_b.py`；绝对截止已过，本版不随发行。
  - **排除 3**：`tests/fixtures/direct_world_bridge/journey_submit_port.py`、`frozen_clock.py`、`frozen_site/sitecustomize.py`，属 2026-11-01 测试隔离笔记具名否决的旁路/全局时钟夹具；接线已从全部用例撤销，文件原地保留不入包。
- 依赖核查：include 内 `.py` 无一处 import 被 exclude/defer 的本地模块；被排除的三夹具在 include 集合内零引用（`test_journey_submit_reason.py` 导入的是 `components/world/service/bridge` 的正式 port）。关键新文件 `life_v02_isolated_check.py`、`owned_test_runner.py`、`test.py`、`check_life_v02.py`、两安装器均在 include。
- 归档排除：`.gitattributes` 追加只影响 `git archive` 的 `export-ignore` 窄规则，覆盖已跟踪的 `.memsearch/**`、`.agents/continuity/**`、`.claude/launch.json`，以及 worker-runs、release-id、旧 `dist/**`、事故探针、预算锁、两份 supply 事故副本、误落 guard 结果、三个否决夹具。既有行尾与密封字节规则未动；`git check-attr` 实测受保护路径 `export-ignore: set`，`-text` 密封项仍 `text: unset`。
- 有限敏感审阅（正则 + 文件名/大小普查，非零泄露保证）：include 仅 `.py/.md/.mjs/.sh`，无 DB/日志/二进制/模型输出数据文件；高置信凭证规则零命中；唯一 `assign_secret` 命中是 UI 测试的合成中文常量。`/root/nyairo-*` 等历史实验根出现在工程笔记/文档，未见真实用户主目录。
- 内部网关地址脱敏：24 个公开文档中的真实主机名已机械替换为 `gw.example.invalid`，并在文首标注“内部网关地址已脱敏”，历史观察/失败/预算/截止不改；`scripts/g1_real_adoption.py` 不再内置真实地址，改由运行期 `NYA02_MODEL_BASE_URL` 显式提供，缺失时真实 `prepare`/`run` 在建实例、写窗口或发请求之前拒绝。白名单检查把该主机名列为本版**阻断项**，扫描整个拟归档 tracked 树（扣除 `export-ignore`）加 include，不只扫变化；本轮实测 13338 个拟归档文件命中 0。`.memsearch/`、`.agents/continuity/` 等排除路径下的历史原文不净化，只靠 `export-ignore` 不进归档。`NOW.md` 由父级独占，当前已是占位写法。
- 版本口径：当前**工作树**中 `MANIFEST.json`、`scripts/release.json`、`scripts/bootstrap.sh`、`website/install.sh` 四处对齐 v0.5.1；但安装器三行对齐（bootstrap 默认与帮助、install 帮助）是本轮工作树改动，不等于 HEAD 已含：HEAD 的 bootstrap 仍是 v0.3.0，install 帮助仍是 v0.4.0。本单未改版本、未改 manifest。
- NOT_RUN_BY_SCOPE：commit/tag、新 commit 上的 MANIFEST 重新生成、ZIP 构建、安装验收、上传/推送/发布。MANIFEST 仍描述旧 HEAD 的 13112 个文件，提交新增文件后若不重新生成，`make_release.sh` 的 manifest 门会以 `unexpected_files` 拒绝。
- 已验分段为历史，不重跑：安装器三处版本统一 7 例、life-v02 隔离 83 断言、fast_day 显式 authority 29 例、六模块离线夹具 159 例、Node 12 文件 208 例、共处 58 例、World 27 例、Memory 38+29 例及 MEMORY_CHECK。
- 原失败史保留：单进程 host 3192 例中 108fail/63error/9skip；后按文件隔离曾有 37 文件失败，之后逐段修验，不能写成现仍 37 红，也未重跑当前整树完整测试。V-C 未过、G1 未闭合、G2 比较未开始、24h 未做；脚本替身验收不等整场景自然采用。
- 已跟踪全树 13116 个文件的路径普查：本机数据只集中在 `.memsearch/`、`.agents/continuity/`、`.claude/launch.json`；`vendor/hermes/` 下的 eval 样例 `.jsonl`、插件索引与 3.9MB 图片属上游 vendored 内容，未作语义审计，随 vendor 原样发行。
- 本文件描述发版前置准备，不表示已提交、已打包或已发布。**历史未净化**：本次只处理工作树/拟归档文件，`export-ignore` 只影响 `git archive`，不会清除祖先提交里的真实内部地址；未审计父 commit 历史全文，公开 push 前的历史泄露风险仍由父级另行判断。
