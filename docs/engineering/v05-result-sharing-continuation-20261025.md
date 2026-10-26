# v0.5 结果承接到有限本地表达：换版 / 不可读 / 草稿 CAS

日期：2026-10-25。基线：`1057bad`。范围：**0 业务源码改动**——具名复现后判定
"真实作品结果 → Sharing 草稿 → 换版后的后续选择"在现有 owner 合同下已经接通；
本单交付的是正式的整条贯通证据与正负控（新测试 + 新冒烟 + 文档），不重造
`LifeSharingService` 的写口、受众、状态门或 CAS。

## 这条链（每一步都走正式回合）

```
正式 Decision 写作品 A 的版本 v2（真实 owner，step receipt + artifact@artifactversion 结果身份）
  → 正式 Decision prepare：草稿 D1 精确绑定 v2（D1 是另一件 artifact，A 的 head 不变）
  → 正式 Decision CONTINUE 同一作品：真实写出 v3
  → 下一轮实际请求：D1 的 source_current = readable / stale / matches_recorded=false
      旧正文仍可读（REVISION_CHANGED 不是删除）
  → 正式 Decision send_local(D1)：owner 拒 stale_source，D1 head/state/receipt 零变化
  → 正式 Decision 对当前可读的 A 新 prepare：新草稿 D2 绑定 v3；D1 一字未改
  → 正式 Decision send_local(D2)：恰好 1 条本地 receipt，外部/已读/确认/理解全 false
  → 重复发送 / 同一意图重放：没有第二条 receipt、没有第二份草稿
```

## 三类"不要误发"的边界

| 场景 | owner 的答案 | 零写事实 |
| --- | --- | --- |
| 来源换版（D1 绑定 v2，作品已到 v3） | `status=stale_source`，`reason_code=SOURCE_REVISION_CHANGED`，`source_current.readable=true / stale=true / matches_recorded=false` | 草稿 head / state / receipt 全不变；作品 head 不变 |
| 来源不可读（只对来源读拒绝的端口） | `status=stale_source`，`reason_code` 是 owner 自己的 `SOURCE_GOVERNED`（**不是** `SOURCE_NOT_FOUND`） | 同上；请求里 `text_withheld=true / text_available=false`，sentinel 不出现在请求、拒绝答案或读回答案 |
| 草稿被另一个合法 writer 改过（Decision 读后才 defer） | `status=conflict`，`reason_code` 是 owner 自己的冲突码（不是 `SOURCE_*`），不带 `source_current` | 没有新 receipt；下一次正式请求看到的是那个新 head |

三者的区别是**来源事实**与**草稿 CAS**两件事分开：换版/不可读在 owner 写入前的来源闸门
被拒，草稿 CAS 在 owner 的 `expected_head` 上被拒。两者都不会被投影成"空草稿"或
"已发送"。

## 真实 / 替身边界

- 真实：Life Supply owner（自有 AF_UNIX + 落盘 canonical db）、M0 Memory、ProductionLifeRuntime、
  Sharing / Practice owner、AG-0 候选、AG-1 原始 JSON 校验、AG-2 adoption、AR-0 受管作品适配器。
  作品版本与 receipt 都是 owner 真写出来的，不是只读种子。
- 脚本替身（明示）：Decision 回答与内容回答；"另一个合法 owner writer"也是一次真实的
  owner 写（同一个 `PrivateProjectService`，第二个 service 实例）。
- 故障注入：`SourceRefusingPort` 只对**来源 artifact** 的读取回答 `ok=false` +
  owner 原因码，其余读写照常落到真实 owner。它模拟治理/撤权形状，不声称覆盖完整
  Memory 撤权或跨备份恢复。
- 时钟：每个正式回合自己的观察时刻，步长 400 分钟（既有 6 小时 TTL 之外）。被拒的发送
  不改变任何状态，同一个状态要等 TTL 过完才会被重新考虑——推进的是夹具时钟，不改静默规则。

## 既有合同的两处如实说明

1. **prepare 的重复意图不会产生第二份草稿，但不是 `already_applied`。** 冻结同一时钟的
   两次完全相同 `prepare_share`：第一次 `applied`，第二次被 owner 拒（`rejected`），
   草稿总数只 +1。调用方因此拿不到原 `share_id`。这是现有 owner 的 `create` 合同，
   本单不改 `private_project_service`、也不为它新增读取口；`send_local` 的 commit 重放
   仍由 owner 的 operation 账本回 `already_applied`（重复发送在读侧先报
   `SHARE_ALREADY_DELIVERED` 并回同一条 receipt）。
2. **冲突答案不带 `current_head_version_id`。** `life_sharing_context.report()`
   的有界投影没有这个字段（owner 答案里有）。Decision 下一次正式请求会从 owner 重新
   读到新 head，所以不新增字段、不改投影合同。

## 验收

WSL，`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`：

```
A  -m unittest -q tests.test_v05_result_sharing_continuation \
       tests.test_v04_sharing_life_chain tests.test_situation_expression_behavior
B  scripts/v05_result_sharing_continuation_smoke.py
```

父级独立复跑：A `Ran 15 tests in 38.776s ... OK`（本单 3 例 + 既有 12 例），exit 0；B 38 项 check 全 PASS，exit 0。日志为 `.agents/worker-runs/20261025-v05-result-sharing-parent-a.log` 与 `-parent-b.log`。A 日志还保留了fixture的PASS输出片段，不把最终OK扩大成日志无杂项。
本单没有改 `components/` 下任何文件；既有三块（A43/B37、A44/B36、A67/B24）不重跑。

本链的 prepare/send 可以附在 `NO_ACTION` Decision 上，正式结果为 `SHARE_ACTED`；不能据 Decision 叫 NO_ACTION 就断言零副作用。零分享写入的负控是明确不带 sharing 请求的合法 NO_ACTION。

## 不能宣称

真实模型自然采用这套字段、用户真的收到、外发、G1、24h；也不能把"来源不可读"的端口
故障注入当作完整治理撤权链路。旧实例、默认 gate、Memory 准入门、预算与 usage 存储均未动。
