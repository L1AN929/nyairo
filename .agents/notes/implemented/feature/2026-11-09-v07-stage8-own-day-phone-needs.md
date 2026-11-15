# 0.7 第八阶段：自己排今天+义务真执行、同作上下文与手机只读今天、线上点心与吃/如厕体感实链

Status: implemented

## Problem

前七阶段把生活链、阅读、购买、布置、睡眠多世界都跑通了，但「她自己排的今天」和「operator 给的外部
义务」在 v0.7 的默认新实例里从未被真实串起来：Decision 提交 `plan_today` 后计划是否真的落成一条只
认引用的记录、义务经 operator 正规入口导入后能否经完整 AG-0→AG-1→AG-2→AR-0 真正续写出回执、完成
结果能不能只从落盘的 Action/Decision/owner 回执重读成 `DONE`（而不是内存 step 猜）、完成后后续候选
是否以 `OBLIGATION_ALREADY_DONE` 扣留——都没有在同一个 v0.7 实例上端到端证明过。

同时三块读面/体感面缺一份真实证据：同一件作品的 `work_context_view` 是否真的把旧版本/来历/Memory
确证结果/证据指回它的做法/本地草稿都从各自 owner 读出来；手机今天的 `arrangements` 与 `timeline`
能否在真实实例上投影出 `SELF_PLANNED` 与已 DONE 义务、把 Activity/计划/World 动作/钱包交易按各自
时域合并且读前后底层零写；以及线上小店买的点心能否经正式 Decision 的 `CONSUME`、家里声明的洗手间
能否经 `TOILET` 让 Body 的饥饿/如厕档位真实下降（而不是 Life 按商品名硬调身体）。

## Decision

接回默认主 demo 的**同一 root、同一本小册子**（晚间 `QUESTION_FROM_MATERIAL` 生出的
`project_artifact`，已续写多版、有 Memory 确证经历和本地分享草稿）把三件事跑完，全部走既有
owner，不新增 owner/store/schema，也不改两个锚点；本段用真实当前业务钟推进今日安排与手机读，
使 Activity/计划/钱包与 World 执行账（owner 用真实 now 落账）落在同一真实日、时间线能如实合并，
主链已发生的 2026-11-10→11-11 跨夜证据不动。单测另留一个自建实例的独立 helper 走同一段真链：

1. **今日自选/义务**：先用正常 `later_item` 造一条真实 `fitem`，再由 `ScriptedDailyCognition` 在正式
   Decision 顶层提交 `plan_today={"item_ref":"fitem:..."}`，经既有提交后 `DayPlan.plan(...)` 落成
   `PLANNED`/`decision_ref` 记录（幂等：同 Decision 不重、新 Decision 同 item 同天不多建）。义务经
   `life_day_plan.import_obligation`（operator 正规写入口，显式输入+`port.resume_point` 现场校验）绑定
   该实例里一件真能被续写的册子 artifact 与同对象完成 basis；未知目标具名拒绝、同 key 重放不新建。
   自选 fitem 绑定这本册子（带 artifact 来源，不再是 objectless 的 FUTURE_ITEM 选项而被既有 dedupe
   折进同一条），且**排在到期之前**先记下 `PLANNED`。义务先做：`project_options()` 真出 `TODAY_OBLIGATION`
   预留位（AG-1 候选仍 ≤16），Decision 正式 START 那条义务 → 册子 AR-0 写出新版本 → 结果只从持久
   Action/Decision/owner 回执重读为 `DONE`/`PERSISTED_ACTION_RECEIPT`；义务完成扣留后再把业务钟推到
   自选到期点，`TODAY_SELF_PLANNED` 预留 `popt` 真正进入那一轮的最终 AG-0 offered（不再只是打印
   `offered=False` 放过），义务以 `OBLIGATION_ALREADY_DONE` 扣留、不重复提供/重复写，自选保持 `PLANNED`。
2. **同作上下文 + 手机只读**：先在同一册子上做多版本真实续写、把它的写作结果经 AG-2 进入 Memory、
   沿既有 `method_proposal` 路径建一条证据指回该作品结果的做法、并用 sharing owner 从它 prepare 一份
   本地 DRAFT；随后 `cycle.work_context_view(booklet)` 报 `state=OK`，旧版本/来历/确证结果/做法/本地草稿
   各来自真实 owner 读口。再在同一实例目录上直接调用真实读入口 `own_day_read.arrangements_envelope`
   与 `day_timeline.timeline_envelope`（`service` 是本实例的 `PhoneService` facade）：前者投影
   `SELF_PLANNED` 与已 DONE 义务，后者把 Activity/计划/World 动作/钱包交易四类真实记录合并，operator
   视图 `skeleton_only` 且无私有正文；两次读前后对状态文件做字节快照，零变化。
3. **线上点心 + CONSUME/TOILET**：在主链这一实例**初建时**（`Demo.build` 的 opt-in 参数）显式落
   `data/body/lived_needs_profile.json`（hunger=noticeable、thirst=noticeable、toilet=strong，均为**合成
   operator 初值**，明说不是自然积累、不是人体测量；不改 Body state/阈值），并在 World 既有声明文件里给
   `consumable_catalog.virtual_snack_wafer` 绑 `lived_need_effects.hunger` 与
   `lived_need_facilities` 一个家里洗手间。`PhoneShopping.purchase('virtual_snack_wafer', key)` 落成
   一笔 DEBIT+OWNED 并物化 home/bedroom，同 key 重放 `already_applied` 不二扣；点心由正式 Decision 从
   owner 提供的 `object:<instance>` 候选经完整 AG-0→AG-1→AG-2→AR-0→WorldResolver `CONSUME`，洗手间由
   正式 Decision 选 `bodyact:toilet:use:<facility>` 走 `TOILET`；两域各自落 World 执行 receipt 与 Body
   relief receipt，hunger/toilet 档位按 World 显式声明真实下降为 `none`。

演示把这三段接在 `main()` 主链结尾，直接复用主链的 `demo` 与晚间 `project_artifact` 册子
（`_run_stage8_chain(demo, booklet, booklet_bare)`），不另起第三 root、不另造同名册子；与主链、
山地链并存不删旧步骤。单测另留一个传 `demo=None` 的自建实例 helper 走同一段真链；`tests/test_v07_daily_life.py`
新增的真链用例与两条纯域负控/幂等用例保留，原 64 例保持。

## Alternatives considered

- **另起一条独立的默认新实例链（第三个 root），自建一本同名册子跑完三段。** 最强理由：完全复用
  已建好的模板，省掉和主链/山地链里几十轮真实 Decision 的交错，建根最短、三者天然同一个业务日、
  旧 64 例时序最不容易被碰红。否决：任务书与用户明确要求**接回主链同一 root、同一本册子**闭环——
  那本册子的多版本/来历/Memory 确证/做法/本地草稿必须是它自己真实长出来的经历，手机今天读面也要
  读那个**真的执行了计划/义务**的实例；另造一本 seed 册子或用第三 root 拼合，等于用新实例冒充主链
  闭环，即使断言全绿也不算交付。故改为接回主链 `project_artifact`，并让独立 helper 只服务单测。
- **把 Stage8 硬塞进山地链（第二世界）里跑。** 最强理由：山地链也是同一条生成→prepare→Supply/World/
  Body/Life 路径。否决：山地链已经排满睡眠/跨夜/环境事件换日，且它的册子不是晚间 QUESTION 生出的那本；
  硬塞会同时碰两处时序断言。点心体感沿任务允许留在主链这一实例做，不再另取世界。
- **给 World execution ledger / 钱包交易注入 demo 的机械业务钟，让它们也「业务日」。** 最强理由：那样
  时间线可以只有一种时域、排序更「整齐」。否决：Body 的持久时间锚在真实现在，注入一个更早的业务钟
  会让 CONSUME 的回血结算被判成「now 早于 last_persisted_at」而整条体感链失败；而且把不同 owner 自己
  的真实落账时刻伪造成业务钟，正是 `day_timeline` 明确不做的事。改为用真实当天、world/wallet 记
  `system_clock`、Activity/计划记 `business_clock`，`ordering.comparable=false` 如实保留不同时域。
- **在 Life 层按商品名直接调用 `consume`/`toilet` 或直接改 Body state/Power/余额。** 最强理由：一行就能
  让档位下降、最快拿到「有反应」。否决：那等于绕开 AG-0→AG-1→AG-2→AR-0→WorldResolver 与 Body owner，
  把「她选择吃/用设施」伪造成 Life 硬调身体；回血只能来自 World 显式 `lived_need_effects` 声明并由
  Body owner 执行，这是旧铁律。
- **用 `_FakeActionRead`/`_FakeService`/手拼 arrangements 或 timeline 来验手机读面。** 最强理由：省掉
  真实 Supply/World/gateway 依赖，测试更快更稳。否决：那证明不了真实入口在真实实例上零写、也证明不了
  四类真来源能合并；改为在本实例上直接调用真实 `own_day_read`/`day_timeline` 入口与 `PhoneService`
  facade，并对状态文件做字节快照证明读零写。

## Consequences

- 接在主链同一册子上的真链现在同时证明：`plan_today` 幂等落成计划、operator 义务经正规导入后被完整
  决策链真实执行且只从持久回执确认 `DONE`、完成后以 `OBLIGATION_ALREADY_DONE` 扣留；义务完成后
  `TODAY_SELF_PLANNED` 预留 `popt` **真的进入**当轮最终 AG-0 offered（不再被截没、不再只打印 False）；
  同作上下文五类来源（版本/来历/Memory 结果/做法/本地草稿）都是真实 owner 引用；手机
  `arrangements`/`timeline` 真读入口在本实例上骨架只读、四类时域保留、读前后 47 个状态文件字节零变；
  线上点心购买重放不二扣、`CONSUME`/`TOILET` 让饥饿/如厕档位按 World 显式声明降到 `none`。
- 代价：主 demo 现在更长（多十几轮真实 Decision + 真实 owner 往返），且 `Demo.build` 增加一个
  `enable_lived_needs` opt-in（默认关，只有 `main()` 打开，旧测试完全不受影响）；Stage8 段用真实当前
  业务钟，跨日与主链固定 11-10→11-11 证据不同域、各自 time_basis 如实保留。
- 边界：初值 hunger/thirst/toilet 是显式合成 operator 参数（noticeable/noticeable/strong），不是自然
  积累、不是人体测量；业务钟是注入机械推进；Decision/内容/阅读响应都是脚本替身，不代表真实模型会
  这样选。主链晚间 `QUESTION_FROM_MATERIAL` 创建小册子时，`autonomous_life_cycle.py` 沿既有 owner
  创建路径接入真实 QUESTION 来源；同一本册子的 `work_context_view` provenance 现在为 `OK`，不是
  给旧记录补造来历。父级完整 demo 读到旧版 5、来历 OK、Memory 确证结果 5、关联做法 1、本地草稿 3。
  手机读前先 `runtime.flush_all()` 把在途写 settle，让独立只读重开到与 journal 一致的盘面
  （不是读口补写/伪造）；不是 G1/24h/V-C。本阶段改动涉及 `autonomous_life_cycle.py`、
  `scripts/v07_daily_life_demo.py` 与 `tests/test_v07_daily_life.py`（原 64 例保留、新增 3 例）；两个锚点
  `autonomous_life_assembly.py`/`life_sharing_service.py` 字节未变（父级相对 Stage8 baseline 的 SHA256 相等）。
- 父级最终验收（2026-11-10）：`tests.test_v07_daily_life` 串行连续三次各 67 例 OK/exit0，耗时
  56.873s / 56.689s / 55.582s；完整 `scripts/v07_daily_life_demo.py` exit0，默认主链、山地第二世界链、
  Stage8 同 root 同册子均跑完，手机四类来源齐全且 47 个状态文件读前后字节不变。日志为
  `.agents/worker-runs/20261110-v07-stage8-final-parent-unit-{1,2,3}.log` 与同前缀 `-demo.log`。
  早期 timeline 缺 World/钱包条目的红史保留；终稿本次四轮均未复现，不据连续绿宣称原故障根因已完全确证。
  昨晚 worker exit4 的机器休眠解释来自用户，父级最终验收不依赖该解释。产品模型请求 0，无 commit、发布或旧实例改动。

相关：日计划/义务 [v0.5 她自己排一天](../architecture/2026-10-26-life-own-day-plan.md)、手机时间线
[手机只读时间线](2026-10-27-phone-day-timeline.md)、可选体感
[虚拟身体需求](2026-10-27-body-lived-needs.md)、同作上下文转发
[writer work_context](../bug-fix/2026-11-07-writer-work-context-forwarding.md)、多世界日常链
[v0.7 第七阶段](2026-11-09-v07-stage7-multiworld-daily-life.md)。
