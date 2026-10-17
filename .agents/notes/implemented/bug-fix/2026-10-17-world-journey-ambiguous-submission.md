# Agent Note: 模糊提交不是拒绝——owner 已提交时的 UNKNOWN 必须走对账而非终态 blocked

Status: implemented

## Problem

一次真实的计时出发被世界引擎写成了**终态** `blocked`，而 owner 自己的账本里它已经
`committed / move_departed`、travel 记录 `IN_PROGRESS`。原始 fixture
`/tmp/nyaw05r.jdK3q/w05inst-fgon6ibm`（只读）留下的时间线只有一次提交：
授权 `issued 07:00:13.254Z → consumed 07:00:13.262Z`，客户端在 ≈`07:00:15.26` 读超时，
owner 在 `07:00:15Z` 落账。

三处叠加：

1. `world_body_client.submit_action` 用**读**超时（2.0s）做唯一一次提交，且此后从不回读
   owner 的判决。2.0s 比 owner 自己的 `_commit_lock`（10s）还短，等于把"忙但健康"定义成
   传输故障。
2. 端口如实把 `transport_fault:TIMEOUT` 报成 `status="uncertain"`。
3. `world_journey._submit_segment` 对 `owner_status != "applied"` 一律
   `_fail(..., STATUS_BLOCKED, ...)`：`uncertain` 与 `rejected` 同等对待，也没走模块自己
   docstring 里写好的"按 execution_id 认领 travel 记录"。`blocked` 是终态，
   `advance_journey` 从此直接返回投影，永不自我修复 —— 一次已发生的出发被永久锁死。

## Decision

**模糊提交（`uncertain`）进入非终态的"待对账"状态，只由 owner 的证据收尾。**

1. 世界客户端把读和提交分开计时：读 2.0s（`DEFAULT_READ_TIMEOUT`），提交 15.0s
   （`DEFAULT_MUTATION_TIMEOUT`，覆盖 owner 的 10s commit lock）。提交仍只发一次
   （`retries=0`，同一个 execution id）。
2. 提交遇到 transport fault 后，客户端**只读一次** `get_action_result(execution_id)`：
   `known=true` 且 `verdict=SUCCESS|FAILURE` 才把疑问升级成判决；`UNKNOWN` / `NOT_FOUND` /
   不可读 / 超时一律保持 `UNKNOWN`。不重发、不铸新 id。
3. `ClientResult` 带 `failure_class` 与 `detail`（`transport_fault:TIMEOUT`），不再只剩一个
   笼统的 UNKNOWN。
4. 世界引擎里 `owner_status == "uncertain"` 改走 `_uncertain_submission()`：先 `_reconcile`
   ——同 execution id、origin/destination 都匹配的 `IN_PROGRESS` 记录 → 认领为 `started`
   （`travel_id`/时间全部取自 owner 记录，**绝不直接认到达**）；`arrived` → 交回 `_step`
   确认；证据真矛盾（外来在途/结算失败）→ 仍然 `blocked`；没有记录/读不到 → 旅程
   `unknown`（非终态）+ `reason_code=SEGMENT_SUBMIT_UNCERTAIN:<failure detail>`。
5. 只要当前段带这个标记，后续 `advance` **只对账、不提交**；只有 owner 的证据能推进它。
   暂停（pause）不阻止认领已出发的段，但仍不允许启动新段。
6. 真拒绝/关闭/构建失败（`rejected`/`disabled`/`failed`/`noop`）保持原语义
   （`blocked` / `unknown`）。端口证明"没越过 owner 边界"（`INTENT_SUBMIT_UNAVAILABLE`、
   `INTENT_BUILD_FAILED`）用另一个标记 `SEGMENT_SUBMIT_NOT_SENT`，不阻断下一次推进。

状态与归因：

| 端口 `status` | 归因 | 旅程状态 |
|---|---|---|
| `applied` | 已提交 | 段 `started`，旅程 `in_transit` |
| `uncertain`（可能越过边界） | 先对账；有匹配证据 → 认领；否则保持疑虑 | `started/in_transit` 或 `unknown` |
| `uncertain` + 预提交原因 | 没越过边界 | `unknown`，下次可重试 |
| `rejected` / `disabled` / `failed` | 真拒绝 | `blocked`（终态） |

失败归因写进账本而不是只进日志：`world_journey.json` 的 `error.reason_code` 现在是
`SEGMENT_SUBMIT_UNCERTAIN:transport_fault:TIMEOUT`，`observe_journey()` 也照实返回，
不再是一个无法定位的 `UNKNOWN`。

## Alternatives considered

- **把 2.0s 直接调大（比如 10s/30s）**：能少踩几次，但只是把竞态窗口往后推 ——
  owner 忙起来仍然会超时，而且盲增所有读的预算是拿交互延迟换运气；超时之后依旧没有
  "谁说了算"，`blocked` 照样写死。否决原因不是慢，而是它**不产生证据**。
- **UNKNOWN 一律当成功（或一律当失败）**：最省事，也最危险 —— 前者会把一次从未发生的
  出发记成已出发，后者正是本次缺陷。世界引擎的位置权威在 owner 手里，引擎自己不许替它
  宣布结果。
- **提交失败后在同一个调用里立刻重发同一 execution id**：看起来能"补上"，但回执丢失意味着
  它**可能已经提交**；重发是在赌 owner 的幂等去重一定覆盖这条路径。真正需要的是先读证据，
  再决定是否还有必要提交。本批采取更严格的"先对账，有标记期间绝不提交"。
- **把线上已经 `blocked` 的旧账本统一解冻（thaw）**：旧记录里没有模糊标记，无法把
  "回执丢了"和"真拒绝"分开；一律解冻会把真拒绝也放行。因此不做批量迁移 ——
  旧账本的恢复只能靠 owner 自身的持久证据人工确认。
- **让 Life 端口（`world_journey_action_adapter`）自己承担对账**：那会把 owner 证据的解读
  复制到第二个模块，且该文件有独占写入者。世界引擎已经有 `_reconcile`，复用它即可。

## Consequences

- **收益**：一次已提交的出发不再被自己写成终态；账本能自愈到 `in_transit` 并继续结算。
  疑问与判决分开记录，`transport_fault:TIMEOUT` 这类归因可以直接在
  `world_journey.json`/AR-0 detail 里看到。
- **代价**：提交的 socket deadline 从 2s 变成 15s（只在真正提交时）；一个"回执丢了且
  owner 也没记录"的段会停在 `unknown` 等人工/后续证据 —— 这是有意的 fail-closed，
  不是自动重试。想解除只能由 owner 的证据（同 execution id 的 travel 记录）推进。
- **上限**：真实旧 fixture 的 `blocked` 账本没有自动迁移；本批只保证新发生的模糊提交不再
  落成终态。直接证据见 `docs/engineering/world-daily-owner-unknown-result.md` 与定向测试
  `tests/test_world_journey_ambiguous_submission.py`、`tests/test_world_body_slow_owner_verdict.py`。

## 检索与分类（写本篇前的当场审计）

按 `world_journey` / `UNKNOWN` / `uncertain` 检索 `.agents/notes/` 活跃笔记（排除 archived）：

- `implemented/architecture/2026-10-17-world-daily-continuous-stay-and-plan-change.md`：
  **部分重叠** —— 它在 §问题清单里记下"World 偶发把真实出发答成 UNKNOWN、journey 落成
  blocked"，但明确"本批不修 W/F"，演练用 `settle_world_journey` 先对账绕开。本篇吸收
  其中"待 owner 侧定点修复"的条目，未改动该文件（同一批次有独占写入者，避免并发写冲突）。
- `implemented/architecture/2026-10-17-world-consume-placement-receipt.md`、
  `...-life-consume-owner-path.md`、`...-economy-facts-failclosed.md`：
  **无关** —— 它们处理 CONSUME 的回执与归因，同一个"不把缺席当证据"的原则，机制不同。
- `implemented/architecture/2026-10-17-world-daily-opportunities.md`：**无关**（机会投影）。
