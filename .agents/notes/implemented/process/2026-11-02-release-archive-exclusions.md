# Agent Note: v0.5.1 发行归档排除与白名单边界

Status: implemented

## Problem

v0.5.1 发版前，工作树有 445 个未提交变化，其中 `.memsearch/`、`.agents/continuity/`、
`.claude/launch.json` 三类本地运行数据**早已被 git 跟踪**。它们不在本次白名单里，只靠
「不 `git add`」并不能阻止它们进入发行包：`scripts/make_release.sh` 用
`git archive <commit>` 打包，已跟踪文件的旧版本仍会原样进包。另有旧 `dist/`、事故探针、
预算锁、两份 supply 事故副本、误落 `docs/engineering` 的 guard 结果，以及 2026-11-01
测试隔离笔记具名否决的三个旁路夹具，都不应随源树公开。

## Decision

在 `.gitattributes` 末尾追加一组**只影响 `git archive`** 的 `export-ignore` 规则：

- 已跟踪的本地数据：`/.memsearch/**`、`/.agents/continuity/**`、`/.claude/launch.json`；
- 运行期/事故产物：`/.agents/worker-runs/**`、`/.agents/release-id.txt`、`/dist/**`、
  `/.tmp_probe_e.py`、`/life_request_budget.lock`、两份 `components/supply/lifysupply*/**`、
  两条 `20261101-release-offline-final-guard-result.*`；
- 已否决夹具：`/tests/fixtures/frozen_clock.py`、`/tests/fixtures/frozen_site/**`、
  `/tests/fixtures/direct_world_bridge/**`。

规则窄到具体前缀，不整目录封 `.agents/notes/`——维护笔记是源码树文档，继续随包。
既有行尾与密封字节规则（`-text` 的 `readonly_recall_adapter.py`、`MANIFEST.json` 等）
一行未动。提交本身仍走显式白名单，`.gitattributes` 只负责把已跟踪的本地数据从归档里剔除。

## Alternatives considered

**`git rm --cached` 把本地数据从索引里摘掉。** 最彻底，包和索引都干净。但会改变跟踪状态，
下一次工作树状态、其他 worktree 和别人的 checkout 都会感知到删除；对一个只做发行前置的
工单来说破坏性过大，且用户明确要求不删文件、不动索引。否决。

**把它们加进 `.gitignore`。** 对已跟踪文件无效——git 继续跟踪，`git archive` 照样打包；
对未跟踪的事故副本有效，但无法解决核心的 `.memsearch`/`continuity`/`launch.json` 问题。否决。

**把所有 `.agents/notes/**` 整目录 export-ignore。** 能省掉逐条列规则，但会把已实现决策
笔记一起从发行包剔除，而这些是源码树的一部分。否决，只排具体本地数据路径。

**改 `make_release.sh` 用白名单文件筛选归档。** 能让包更精确，但把「提交白名单」和
「归档白名单」两份东西塞进同一脚本，且脚本当前以 `verify_manifest.py` 为门；改脚本属于
发行工具链改动，不在本单范围。否决，保留 `export-ignore` 这一 git 原生机制。

## Consequences

- `git archive` 不再带本机索引、检查点、启动配置、旧包和事故副本；checkout 字节、索引状态、
  行尾与密封 sha256 都不变。
- `export-ignore` 不阻止提交：真正决定提交范围的是 `20261102-release-whitelist.json` 的
  include 集合，两者必须同时成立。
- `MANIFEST.json` 仍描述旧 HEAD 的 13112 个文件；提交新增文件后必须在新 commit 的 LF 树上
  重新生成，否则 `make_release.sh` 的 manifest 门会以 `unexpected_files` 拒绝。这一步不在本单执行。
- 归档排除只覆盖本次已识别的路径；未审计父 commit 历史，不能当作历史提交零泄露的保证。

## 脱敏补充（2026-11-02，同版）

- **范围**：24 个已 tracked 或本次新增的公开文档把真实内部网关主机名机械替换为
  `gw.example.invalid`，并在文首标注“内部网关地址已脱敏”；历史观察、失败、预算与截止
  一字不改。`scripts/g1_real_adoption.py` 的 `MODEL_BASE_URL` 改为空默认，真实地址只在
  运行期由 `NYA02_MODEL_BASE_URL` 显式提供；缺地址时真实 `prepare`/`run` 在建实例、写窗口
  或发请求前 fail-closed，离线夹具仍用 `offline.invalid` 的合成值。
- **门禁**：白名单检查把真实内部网关主机名（规则在检查脚本里，不在此复写）从 warning 升为阻断项，扫描“已 tracked 树扣除
  export-ignore，再并上 include”，不只看本轮变化；命中即非零退出，输出只给路径行号。
- **历史限制**：本次不重写 git 历史，也没有净化 `.memsearch/`、`.agents/continuity/`
  下的原始记录；`export-ignore` 只影响 `git archive`，不保护祖先提交。不能据此宣称旧公开
  记忆或历史提交已收回，公开 push 前的历史风险仍需父级单独判断。
