# Agent Note: v0.4 来访决定先行与采纳内前台交接

Status: implemented

日期: 2026-11-21
阶段: `NYAIRO-v0.4-来访共处与私人生活-大阶段单.md` §B（写者 B）

## Problem

上一版把 park 放在 Decision 之前，于是拒绝/DEFER/模型失败都会改变私人 Activity（落到
`PARKED_NO_JOINT` 等 expiry/end），与阶段单 §B 35–41「决定优先」相悖；邀请 Decision 也没有走
实例自己的预算扣账；end 之后正常窗口仍可能被判同状态 QUIET；整链没有真正读到下一页。

## Decision

1. **Decision 先**：`begin_from_decision` = `consider()` →（预算门 + 一次 AG-1 Decision）→
   仅当 `START` 才采纳。NO_ACTION / DEFER / WAIT / 拒绝 / 预算耗尽 / 认知抛异常，都在
   **没有任何 Activity 被碰过**的情况下返回（实测断言 status/revision/checkpoint/foreground 全等、
   无 session 文件、无 joint）。
2. **交接是 adoption 自己的第一条命令**：AG-2 `adopt_decision` 新增可选 `handoff_spec`
   （Mapping 或 of-decision 可调用），START 分支在 `start_activity` 之前调
   `_handoff_foreground()`：读 owner 的 ACTIVE 前台、按传入 revision 做 CAS、拒 ATOMIC、
   走 LR-4 `enter_waiting_with_condition(WAITING + EVENT_MATCH(end) + release_foreground)`；
   成功后再 START，且 START 用交接后的最新 revision 做 CAS。**没有**直接清 foreground、
   **没有**覆盖 Decision snapshot、**没有**绕过 freshness/CAS；`validate_decision_for_adoption`
   只在 `handoff_spec` 存在时才放开 `CANNOT_START_WHILE_FOREGROUND_ACTIVE`。
3. **AG-1 的唯一放行**：`RESPOND_USER_REQUEST` + `SOURCE_USER_REQUEST` + target refs 以
   `co_presence:` 起头 + 有 ACTIVE 前台 + 非 ATOMIC ⇒ START 合法；其它候选规则不变。
4. **预算**：决策前读 `cycle.budget.requests.check()`，不允许就 `DEFER` 且零发送；发送前用
   `BudgetedCognitionAdapter`（`reserve` 落盘 → `require_coordinated_send`），admission 只在
   adapter `send_split_capable` 时提供；结束后还原 adapter。
   生产adapter已带同一ledger时复用现有包装，仅计算本次attempt增量；邀请已有模型决定的tick
   不再消费feed/普通窗口，事件保留到下一正常tick，避免同一来访后紧接第二次决定与重复扣账。
5. **指纹**：`life_step_readiness.SITUATION_KEYS` 增加 `resume_eligibility_refs`（必要 Life 辅助），
   cycle 由 LR-4 store 提供——资格刚开就是新处境，不再同状态 QUIET。

## Alternatives considered

- **先park再决定**：最强理由是能直接复用AG1禁止第二前台START的现有规则，不需跨owner交接。
  否决：拒绝/DEFER/失败也会改变私人活动，违背来访不是强制打断的产品边界。
- **在pre_adoption_mutation_hook里park**：最强理由是在已有钩子内复用owner迁移，避免新增契约。
  否决：钩子改变Activity revision后正式freshness校验拒绝STALE_ACTIVITY_REVISION；不能绕过校验。
- **泛化放开START**：最强理由是统一所有显式用户请求的切换路径。否决：会让普通候选也获得第二
  前台入口；现在只对typed共处请求提供受约束的交接，owner仍检查CAS/ACTIVE/非ATOMIC。
- **两次模型调用先问意愿再START**：最强理由是无需扩采纳契约，能保持每次状态快照新鲜。
  否决：同一邀请重复问且可能前后矛盾，消耗双份请求；一次正式Decision已足以表达意愿。

## Consequences

- 收益：拒绝/稍后/失败零副作用；一次邀请一次 Decision 且 `utc_day_calls` 恰好 +1；耗尽时零调用零变化；
  ATOMIC 前台永不被交接；交接失败（start 失败）有 `ForegroundHandoffStranded` → LR-4 事件释放 +
  `PARKED_NO_JOINT` 对账 + 具名 UNKNOWN。
- end经LR4开资格、LR5完成joint，正常资格feed产生新窗口；新Decision之前仍WAITING且page1，
  新鲜RESUME后同Activity/同checkpoint、实际最新有效位置page2。不开自动恢复旁路。
- 验收必须沿同一时间轴；不能只把runner推到未来而事件仍用当前时间。不能先在新状态回答
  NO_ACTION、再绕同状态QUIET强问RESUME。阅读位置用产品read_reading_situation，不能取历史集合首条。
- 代价：一次tick最多两条事件；邀请临时包装共享adapter并finally还原，依赖现有单写者串行执行；
  真模型结果与机制测试分开，不宣称自然社交行为已证明。

验证：父级保留式runner，WSL14项/0skip/exit0；独立正常runner链9checks/实际page2/exit0。
原13绿1红结果保留于交付报告，父级证据纠正LR4归因，未修改LR4规则。
报告：`docs/engineering/v04-social-life-core-result.md`。
生产预包装负控实测一次tick扣3次（双包装2次+普通窗口1次）为红；修后单项与最终14项均exit0。
此处使用同一budget对象身份，不信任仅同名的别处账本；不退款、不改额度。

无committed Decision（例如严格schema失败）现在返回UNKNOWN/DECISION_NOT_COMMITTED，
不能把空decision写成DECLINED。真实国内DeepSeek一次HTTP200/stop但AG1 INVALID_SCHEMA，
这次行为验证失败、请求账本0→1后关闭；历史错误投影保留不改写。合法JSON错误schema的
正式回归验证UNKNOWN及私人活动逐字段不变，WSL单项exit0。
