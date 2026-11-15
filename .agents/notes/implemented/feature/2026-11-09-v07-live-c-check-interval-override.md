# Agent Note: Live C 只覆写 check interval，不改日常默认

Status: implemented

## Problem

Live B 的正式窗口是 1800 真实秒 = 1800 逻辑秒（rate=1），跑完「读一页→拿起→放到床面」三个
Activity 后完全沉默。只读 B 根结构化证据后，原因不是候选缺失、不是 QUIET、也不是前台占用：

- 实例 env `CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=30`（`one_day_life.py` 的默认值），
  `AutonomousLifeRunner.due()` 的条件是 `now - last_check_at >= interval_minutes*60`。
- B 的首窗口在 `02:00:06.103` 触发（`NEVER_CHECKED`），`last_check_at` 最后被事件改到
  `02:00:29.134`；下一次自然窗口是 `02:30:29`，已越过 horizon `02:30:00`。
- 计数器 `check_windows=1`、末条 `NOT_DUE`；`candidate_audit_journal` 在 10:00:06/10:00:29 两轮
  都物化了 MOVE/stay/pose/READ 等多类候选，模型只是没选 MOVE。

也就是说，30 分钟周期和 1800 秒窗口同长，整段只会开一次窗口；之后的 Activity 完成只产生
事件唤醒（继续同一件事），不会产生新的模型决策机会。候选刷新没有被观测到，因此没有证据支持
「完成后指纹/候选刷新 bug」。

## Decision

1. 新增 `scripts/v07_live_c.py` 薄封闭入口：复用 `v07_live_a` 的 Identity / prepare / audit /
   run / 一次性标记 / 首次故障停，只固定 C 身份（`/tmp/nyairo-v07-live-20261109-c`，history 566，
   cap 20，UTC 300，总限 1000，1800 秒 rate 1）。
2. **只覆写配置**：prepare 成功后，仅把本实例 `one-day/instance.json` 的
   `CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES` 改成 `5`。`one_day_life.DEFAULT_CHECK_INTERVAL_MINUTES`
   仍是 30，日常实例与既有行为逐字节不变。
3. audit 把 `target_minutes`（入口固定）、`persisted_minutes`（实例落盘）、`runner_minutes`
   （resident 汇总里 child 实际值）分开报，缺失保留 UNKNOWN；run 前若 persisted != target 直接
   `CHECK_INTERVAL_MISMATCH` 拒绝。
4. 新测试覆盖：真实 `due()` 下 30 分钟只有首窗口、5 分钟在同样 1800 秒里多次 due；前台空后
   MOVE 只是 World 真正提供的选项（无目的地时不凭空造 MOVE）；QUIET 门禁合法且有连续上界；
   566/20/300/1000 发前限制；缺失 UNKNOWN 不猜 0。

## Alternatives considered

- **把 `check_interval_minutes` 参数穿透 `fast_day → demo → one_day_life`。** 最强立论：走正式参数
  链，比事后改一个 key 更「干净」。否决原因：`one_day_life.prepare` 的默认值是硬编码，最小写集
  不含它；而 prepare 之后只改本实例的一个既有 key，不改任何全局默认，旧实例字节不变，风险更小。
- **直接把 `DEFAULT_CHECK_INTERVAL_MINUTES` 或 one_day 默认调成 5。** 最强立论：一个常量就够。
  否决原因：会改所有日常实例的节奏，属于改全局默认，用户明确只批准本实验覆写。
- **每完成一个 Activity 就强制调用模型。** 最强立论：立刻不沉默。否决原因：会烧额度、违反
  「不能每完成一件事就强制调用模型」，且把安静和故障混在一起。
- **相信存在候选刷新 bug 并最小修。** 最强立论：B 后半程确实没动。否决原因：没有证据——B 的
  候选账显示自然候选正常物化，缺失的是到期窗口；在世界 revision 不变时去改候选刷新只会制造
  第二套触发，违反「先诊断后修」。
- **清 QUIET 逼出模型调用。** 最强立论：保证每窗口都问。否决原因：QUIET 是承重墙，且
  reconsideration 已有「连续拦截 N 次强制放行一轮」的既有上界，C 不应绕过。

## Consequences

- C 在同样的 1800 秒里拿到 5–6 次自然观察窗口，PLACE 完成、前台空之后仍会在下一个 5 分钟窗口
  被重新考虑；是否真的出门/购物仍取决于模型与候选，不保证。
- 生产代码零改动（只新增入口与测试），日常实例的 30 分钟默认、排序权重、TTL、Memory、预算与
  provider 参数全部冻结。
- 已知代价：5 分钟窗口会更频繁地进入 reconsideration 门禁；重复情形仍由既有上界（最多连续 3 次
  拦截后放行一轮）约束，不会无限追问，但请求消耗可能比 B 高，仍受 20 次新增硬上限。
- 未验证：C 的真实 1800 秒 live 行为要等父级 prepare/audit 后唯一 run 才知道；本单只证明离线
  配置、装配、调度与故障停路径，不声称自然生活成立。
