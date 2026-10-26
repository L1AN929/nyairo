# Agent Note: v0.5 第三条 —— 真实作品结果承接到有限本地表达（换版 / 不可读 / 草稿 CAS）

Status: implemented

## Problem

设计 v2 第 18 章的四、五条要求"真实作品结果"能走到"有限本地表达"，并且在她改过那件作品、
或来源读不回来之后**不要误发**。仓库里已经有两块很实的机制：

* B 的 `LifeSharingService`：草稿就是一件 `ManagedArtifact`，发送前核对来源 head、
  CAS 用 owner 的 `expected_head`、正文只在来源此刻可读时投影；
* A 的 `life_sharing_context`：把草稿、来源版本、`source_current{readable,stale,
  matches_recorded}`、扣留事实投影进 Decision prompt。

缺的不是又一条规则，而是**整条正式链的证据**：真实作品版本 → 草稿精确绑定那一版 →
作品续写到下一版 → 下一轮实际请求如实报 stale → 主动发送被 owner 拒 → 新 prepare 绑定
新版本 → 只产生一条本地 receipt；外加"来源不可读"与"草稿被另一个 writer 改过"两条负控。
没有这份证据，就只能靠单测片段推断，分不清"来源换版"和"草稿 CAS"。

## Decision

**不新增业务代码，只补正式贯通证据与负控。** 具名复现后确认：现有 owner 合同已经覆盖
三类拒绝，`life_sharing_context` 也已经把每个事实带进最终请求。交付：

1. `tests/test_v05_result_sharing_continuation.py`（3 例，POSIX 真 owner）与
   `scripts/v05_result_sharing_continuation_smoke.py`（38 项 check）。两者都复用
   `result_method_behavior_smoke` 的隔离装配，用正式 `evaluate_once` + 原始 JSON 决策，
   Decision/内容是脚本替身，作品版本与 receipt 全部由真实 owner 写出。
2. 三类边界在证据里分开断言：
   * 来源换版：`stale_source` + `SOURCE_REVISION_CHANGED`，旧正文仍可读；
   * 来源不可读：`stale_source` + owner 自己的 `SOURCE_GOVERNED`（**不是** `NOT_FOUND`），
     正文扣留，sentinel 不出现在请求/拒绝答案/读回答案；
   * 草稿 CAS：`conflict` + owner 自己的冲突码（不是 `SOURCE_*`），无 `source_current`，
     零新 receipt，下一次正式请求看到新 head。
3. 把两处既有说法改精确（本 note 与 Sharing service note 同步）：
   * `prepare` 的重复意图**不会产生第二份草稿**，但第二次显式 key 的 `create` 会被
     owner 如实拒（`rejected`），不是 `already_applied`，所以调用方拿不到原 `share_id`；
     `send_local` 的 commit 重放仍回 `already_applied`（读侧先报 `SHARE_ALREADY_DELIVERED`
     并回同一条 receipt）。
   * 冲突答案不带 `current_head_version_id`（`report()` 白名单没有它）；下一轮正式请求
     会从 owner 重新读到新 head，因此不扩投影合同。

## Alternatives considered

* **新增一个"草稿来源版本已过期"的持久标记/字段，让 prompt 一眼看出 stale。**
  最强立论：模型不必自己比较版本号。否决：`read_share`/`list_shares` 每次都向 owner
  核当前 head 并给 `source_current`，再加一个落盘标记就是第二个真相源，还会在来源被
  重新写回同版本时骗人。
* **在 cycle 里预检来源换版，把不会再成功的草稿从 offer 里删掉。**
  最强立论：省一次必然失败的 owner 写入。否决：那是 A 替 owner 做发送资格判定，
  模型也就看不到"她写过的这句话为什么现在发不出去"；既有状态门没有"不可发送"这一档，
  删掉等于让草稿静默消失。
* **扩 `report()` 投影，把 `current_head_version_id` 带回来。**
  最强立论：同一回合内就能解释冲突。否决：下一次正式请求本来就会读回新 head；为一个
  回合内的可读性扩既有白名单，收益不足，且会诱使后续代码把这个字段当权威。
* **用 helper 直接调 `apply_action` 证明整条链。**
  最强立论：快、少依赖。否决：那只能证明接缝可达；本单要的是正式 `evaluate_once` 里
  "请求 → Decision → owner"的同一回合证据。

## Consequences

* 现在能证明：同一作品换版后，旧草稿的发送被 owner 以来源事实拒绝、零写；新草稿绑定
  新版本并只产生一条本地 receipt；来源不可读时正文既不在请求里也不在答案里；草稿 CAS
  冲突与来源换版在投影上是两件不同的事；NO_ACTION 不自动 drop/重试/刷新来源。
* 代价：3 个真 owner 夹具（约 19 秒）加一次 38 项冒烟；每个正式回合需要各自的观察时刻
  （步长 400 分钟，越过既有 6 小时静默 TTL），否则被拒回合之后的下一轮会被
  `SAME_STATE_ALREADY_CONSIDERED` 吃掉。
* 不能宣称：真实模型自然采用这套事实、用户收到、外发、G1、24h；来源不可读是端口级
  故障注入，不等于完整治理撤权或跨备份恢复。
* 证据：`docs/engineering/v05-result-sharing-continuation-20261025.md`、
  `tests/test_v05_result_sharing_continuation.py`、
  `scripts/v05_result_sharing_continuation_smoke.py`。
  相关事实原地同步在
  [本地投递与分享生命周期](2026-11-14-v04-life-sharing-service-local-inbox.md) 与
  [身体参与与有分寸分享](../architecture/2026-11-14-v04-body-participation-and-bounded-sharing.md)。

## 跨进程重开（2026-10-25 晚）

同一块在**两个真正不同 PID** 的同根链里再验一次：P1 写出作品新版本、正式 prepare D1
并合法 defer，然后关闭 runtime、关停自己的 Supply owner 退出（关停走生产顺序：
`shutdown`→`server_close`→有界 `drain`→`prepare_socket_path` 守卫只清自有已停 socket，
不裸 unlink）；P2 重开同一 canonical db，
第一次正式请求纯读（D1 仍是 DEFERRED、无新 receipt、作品 head/版本数不变）。续写下一版后，
D1 在真实请求里如实报 `source_current.stale/matches_recorded=False` 与
`SOURCE_REVISION_CHANGED`，显式 `send_local` 被 owner 以 `stale_source` 拒绝且 head/state/
receipt 零变更；显式 prepare D2 绑定新版本，只产生一条本地 receipt，外部/已读/确认/理解
仍全 false，重复发送仍是同一条。D1 未被自动重绑或发送。证据：
`tests/test_v05_restart_continuation.py`、
`scripts/v05_restart_continuation_smoke.py`；交付说明
[v05 restart continuation](../../../../docs/engineering/v05-restart-continuation-20261025.md)。

## 同一结果的来源损失（2026-10-26 补充）

本块再验一次同一个真实作品 A 的**结果 R** 同时充当 owner 结果身份、M0 可回忆经历
和 Sharing 草稿来源：正式 NO_ACTION + `method_trial` 只用该经历把真实 Practice 推进
TRIAL（`trial.version_id` 是受评方法版本，与作品版本 R 分开）；正式 `prepare` 的
`source.artifact_id`/`version_id` 与 receipt/结果身份/经历 ref 逐一相等；只对 A 的来源读
注入既有故障形状时，D 正文 `SOURCE_GOVERNED` 扣留、发送拒写，而方法域仍 TRIAL、M0
经历仍可读；解除后正文恢复且零自动 send/drop/rebind；同一 owner `archive_artifact` A
后版本/head/正文保留，当前目标 A 在 AG-1 前被 `WORK_NOT_CONTINUABLE` 分层拒绝、不
fallback 可续 B，D 来源 ARCHIVED 仍可读但发送门拒 `SOURCE_ARCHIVED` 零写。它不改变
本篇的草稿/来源事实规则，也不新增撤权或恢复能力。见
[同一结果来源损失](../../../../docs/engineering/v05-same-result-loss-20261026.md)。
