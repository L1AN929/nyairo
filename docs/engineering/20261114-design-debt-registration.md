# 设计债登记 · 2026-11-14（反骚扰数值契约对齐批次）

> 性质：登记文件，不是裁定文件。**登记不等于追认**：下表每一项的"处置"只是当前状态的如实记录；
> 逐条等维护者裁定，或等下一轮契约修订整体收口。
> 治理依据：CHIYO v2.0《10-文档维护与版本治理》§4 功能生命周期——"取消实施仍保留原方案、理由和
> 替代链接"；"更复杂设计不得因为'过度工程化'被无记录删掉。先明确其承担的责任、现阶段代价和将来
> 可能适用的条件，再提出降级实现、延后启用或替代方案。"
> 仓库：Windows `D:/nyairo-rc1-body-backend` ＝ WSL `/root/nyairo-v051`（同批改动，md5 零差异）。

---

## 一、v2.0 五项静默丢弃的补登记（统一处置：暂缓；其中第 1 项已于 2026-11-14 由 T3 批次落地，见下）

以下五项在 v2.0 设计中有完整章节，实施过程中被静默丢弃、从未有登记。本批补登记为"**暂缓**"：
保留原方案出处与最小实现路径，不安排施工，等触发条件出现再评估。

### 1. S07 Affect owner（情绪评价域）
- **v2 出处**：《02-核心子系统与接口设计》域卡 "S07 Affect／情绪评价"（含 §S07 专节）；《17-工程契约
  与证据完整设计》S07 行（"实际处境/身体及认知评价产生的短期状态与消退；不签永久愿望、不证明主观
  体验"）。
- **现状**（2026-11-14 更新）：**最小 owner 已落地**——`components/life/scripts/s07_affect_owner.py`
  （typed 三类封闭枚举 mood_shift/affect_pulse/drive、单 owner 归属 subject_id、强度整数百分位有界
  [0,100] 禁连续浮点、source_refs 必填、线性/整数半衰两档确定性衰减零 RNG、expires_at 必填过期即
  读不到零墓碑、FORBIDDEN 词表拒 intimacy_score/mood_delta 走私、零模型调用）；读面经
  `short_appraisal_view` 的 `affect_records`（owner 版本）挂接，`ShortAppraisalView.build` 新参、
  钉死的模块级 `build_short_appraisal_view` 签名不变，无记录时行为不变；原代码内
  "There is no S07 affect owner" 缺席声明已更新为指向新 owner。
- **处置**：~~暂缓~~ → **最小 owner 已落地（2026-11-14，本批）**。登记触发条件已由维护者选定 T3
  批次满足（摩擦淡去之外的情绪余波需要被结算）。登记仍不等于追认：本条只如实记录落地状态与边界
  （本批不开持久 affect store，记录由调用方在其辖区存储；扩展点见该模块 docstring）。
- **最小实现路径**（已按此落地）：typed 记录 + owner 归属 + 有界强度 + 来源必填 + 确定性衰减 +
  自然过期，读投影挂 short_appraisal_view 读面，不动 Emotion/Mood owner 边界。

### 2. P12 人生叙事 / 自传解释
- **v2 出处**：《15-产品记忆与自主机制完整设计》机制卡清单 ORIGINAL 行：P12 自传解释
  （L5274–L5295）。
- **现状**：无任何实现。记忆侧只有既有的 event/belief 类 owner；无自传叙事层。
- **处置**：暂缓。触发条件：长期记忆出现"跨月一致性"验收需求（同一事件在不同时间被讲成相互矛盾的
  版本且造成实际行为矛盾）时再评估。
- **最小实现路径**：以既有记忆条目为源，加一个只读的"叙事视图"（按人物/主题聚合、带版本时间戳），
  不新建叙事 store。

### 3. Inquiry 生命周期（有界求知）
- **v2 出处**：《15-产品记忆与自主机制完整设计》交付包 8："有界Inquiry：具体问题、候选解释、下份证据、
  预算/停止"（L3935）。
- **现状**：无实现。interest/好奇主题散落在注意与日程投影里，没有"问题→候选解释→下份证据→预算/停止"
  的生命周期状态机。
- **处置**：暂缓。触发条件：出现真实的反复无结论好奇行为（同一问题反复提起、无证据积累）需要约束时。
- **最小实现路径**：一张 inquiry 状态表（question / candidates / next_evidence / budget / stop_reason），
  挂在既有注意 owner 下，先只读投影后写权。

### 4. R0–R5 反傀儡实验 + Scorecard
- **v2 出处**：《17-工程契约与证据完整设计》"R0–R5必须完整保存的实验路线"专节（R0 盘点动态人物脚本
  ……R5 去多余人物规定）及 Scorecard 五维评定段（事实边界/意愿行动边界/人设删减依赖/模型措辞依赖/
  成长来源；不平均，硬失败至少 HIGH）。
- **现状**：无实现，原项目状态 NOT_EVALUATED（v2 原文自述 rc28 静态审计未跑这些实验）。
- **处置**：暂缓。触发条件：有可复现的对照环境（裸模型 vs 同模型不同历史）且有人力跑完一轮记录时。
- **最小实现路径**：先只做 R0（同种子双环境对照脚本 + 事实边界清单），Scorecard 五维表格手工填一轮，
  不写自动评分器。

### 5. 16 章一年衰败 22 类监测矩阵
- **v2 出处**：《16-生活生态与长程研究完整设计》§7 ORIGINAL "一年衰败与维护：不用生命总分"——完整
  衰败矩阵含 22 类（兴趣趋同、约束冒充偏好、记忆膨胀……修复二次伤害），每类须同时保留信号、长期后果、
  防护恢复及防护副作用（L8025–L8050）。
- **现状**：无监测矩阵实现；仅散见各 owner 的健康自检（如 v02 运行治理的 QUIET_HEALTHY 等健康态）。
- **处置**：暂缓。触发条件：系统连续运行满一个季度且有真实漂移案例需要归档时。
- **最小实现路径**：22 类各一行的人工季度巡检表（信号/是否观察到/防护副作用），先手工后自动化。

---

## 二、数值演化一览表（登记时点状态：2026-11-14）

口径：**"已于 2026-11-14 对齐设计值"＝本批改动 + 配套 reason_code + 测试钉住；"暂缓裁定"＝既成演化，
照实登记，不追认，逐条等维护者裁定或下轮契约修订。**

### 已对齐（本批施工，见施工单 A/B/C）

| 条目 | 设计值（出处） | 对齐前 | 对齐后 | 位置 | 拒绝 reason_code |
|---|---|---|---|---|---|
| 桌角便签每周张数 | ≤2 张/周（v3.0 10章 §2.1） | 8（周 8，全渠道） | 2（便签渠道独立周配额） | `components/world/scripts/v03_economy_illness_bridge.py` DEFAULT_DESKTOP_NOTE_WEEKLY_CAP（新常量） | OUTBOUND_QUOTA_EXCEEDED（details 带 desktop_note_weekly_cap） |
| 7 天滚动出站总上限 | 全渠道 ≤3（v3.0 10章 §2.1） | 8 | 3 | 同上 DEFAULT_WEEKLY_PROACTIVE_CAP（:242） | OUTBOUND_QUOTA_EXCEEDED（weekly_proactive_cap） |
| 慢信月配额 | 每月 ≤1 封（v3.0 10章 §2.1） | 4 | 1 | 同上 DEFAULT_MONTHLY_LETTER_CAP（:244） | OUTBOUND_QUOTA_EXCEEDED（monthly_letter_cap） |
| 单次出站最小间隔 | ≥48 小时（v3.0 10章 §2.1；原文写在桌角便签条目下，本批按"出站窄而贵"公理对全部主动出站渠道生效） | 无校验 | 48h（全渠道，含 UNKNOWN 在途记录） | 同上 DEFAULT_MIN_OUTBOUND_INTERVAL_HOURS（新常量）+ attempt_proactive_outbound Guard 6 | OUTBOUND_INTERVAL_TOO_SOON |
| 连发两周第三周归零 | 连续两周发便签后第三周配额归零（v3.0 10章 §2.1 原文） | 无校验 | ISO 日历周判定：W(n-1) 与 W(n-2) 均有便签 → Wn 归零 | 同上 attempt_proactive_outbound Guard 5（`_iso_week_key` 助手） | DESKTOP_NOTE_THIRD_WEEK_ZEROED |
| 存在感表演语气红线 | "快看看我画了什么"类（v3.0 10章 §2.3） | HARASSMENT_TONE_CUES 仅问责/焦虑 7 条 | +7 条存在感表演（催看作品/求关注），封闭清单 | 同上 HARASSMENT_TONE_CUES（:251-274） | HARASSMENT_TONE_FORBIDDEN |
| 慢信起草→寄出冷静期 | ≥3 个日历日（v3.0 10章 §一.2） | 只有发送后 delay_hours 在途延迟，无起草→寄出校验 | drafted_at 字段 + 寄出入口校验 now ≥ drafted_at+72h（缺省视为创建时刻=寄出时刻，见 §三 取舍说明） | `components/phone/window_keepsake_view.py` SLOW_LETTER_COOLING_PERIOD_HOURS=72 + post_slow_letter | SLOW_LETTER_COOLING_PERIOD |
| 摩擦淡去默认时长 | 默认 48h，区间 1–168（v3.0《40-吵架与和好》:78） | 6h | 48h（包级覆盖如 chiyo 72h 不受影响） | `components/phone/emotional_tension_view.py:1337-1339`（temper.get("friction_fade_h", 48)） | （无拒绝语义，默认值） |
| 话题悬挂默认时长 | 默认 20 分钟，区间 5–120（v3.0《41-话题还挂着》:67） | 30 分钟 | 20 分钟 | `components/phone/autonomy_rhythm_view.py:78` DEFAULT_THREAD_HOLD_MIN | （无拒绝语义，默认值） |
| ecology HTTP 白名单放行两旋钮 | 域层 `configure_outbound_quota` 已收录 `desktop_note_weekly_cap`（≤50）/ `min_outbound_interval_hours`（≤720）（T2 批次） | HTTP 面 ACTION_ALLOWED_FIELDS 未放行（域层可配、HTTP 走默认值 2/48） | 白名单放行两键，校验沿用域层 `_non_negative_int`（int、非负、上限 50/720；非法值 400 INVALID_INT:<field>） | `components/phone/v03_ecology_view.py` configure_outbound_quota 白名单（X1 批次） | 域层既有：INVALID_INT:<field>（400）；视图层空值 INVALID_FIELDS（400） |
| 票 est 合计生产接线 + 50k 日 token 预算启用 | 票 est 合计经 `reserve(est_tokens=...)` 进预算；日 token 上限 50000（07 章 §四"24 调用/50k tokens 双上限"） | 能力就绪但生产装配未接线，`daily_token_budget` 生产未启用 | `autonomous_life_assembly.request_budget` 默认启用 50000（`CHIYO_AUTONOMOUS_DAILY_TOKEN_BUDGET` 可调/关/非法拒绝）；每次生产 reserve 注入当期票 `est_tokens_total`（无票/读不到 → 保守常数 2000） | `components/life/scripts/autonomous_life_assembly.py` request_budget + WakeTicketEstimateRequestBudget（X1 批次） | 既有：REQUEST_BUDGET_DAILY_TOKEN_BUDGET（50k 越界）；调用上限仍先绑定（24×2000=48k<50k，启用前后行为一致已测试证明） |

注 1：日粒度 `DEFAULT_DAILY_PROACTIVE_QUOTA=2` 未动——v3.0 10章没有日配额设计值；它保留为宿主侧
静音开关（可调 0 全停），在 48h 最小间隔约束下主动出站实际每自然日 ≤1 次。不算"未对齐"，登记为
"设计无此粒度，保留现值"。（语义澄清已按 §三.5 落进 bridge 常量注释：日配额 >1 在 48h 间隔下不可达。）
注 2：~~`desktop_note_weekly_cap` / `min_outbound_interval_hours` 两个宿主可调旋钮已在 bridge 域层
`configure_outbound_quota` 收录；`components/phone/v03_ecology_view.py` 的 ACTION_ALLOWED_FIELDS 白名单
暂未放行~~ → **已于 2026-11-14 X1 批次放行**（上表"ecology HTTP 白名单放行两旋钮"行），HTTP 面配置
生效、非法值 400，测试钉住（tests/test_v03_ecology_view_knob_whitelist.py）。

### 暂缓裁定（既成演化，未对齐，照实登记）

| 条目 | 设计值（出处） | 现状 | 位置 | 说明 |
|---|---|---|---|---|
| 急性期进入阈值 | 病程 s ≥ 0.35 进入急性期（v3.0《05-身体负荷与生病自愈生态-完整设计》:89） | 0.80 | `components/world/scripts/v03_economy_illness_bridge.py:179` ACUTE_STRAIN_THRESHOLD=0.80（PRODROME 0.45 在 :178） | 现值比设计严得多（0.80 才进急性）；未追认，等裁定。涉及全部病程/负荷联动测试基线，改动不是一行常量的事 |
| 急性期退出严重度 | s < 0.25 退出急性期进恢复期（同上 :90） | 0.38 | 同上 :186 ACUTE_EXIT_SEVERITY=0.38 | 同上；与进入阈值成对，须一起裁定 |
| 合并冷却窗 | 900s（合并窗语义，v2.0 契约线） | 300s | `components/life/scripts/v02_runtime_governance_migration.py:48` DEFAULT_COALESCE_WINDOW_SECONDS=300 | 现值合并更激进（窗更短）；影响 wake 节流节奏，等裁定 |
| quiet hours 起始 | 夜间 22:30 后禁止一切向外通知（v3.0 10章 §一.4 与 §2.2 veto 3） | 23:30 | `components/world/scripts/v03_economy_illness_bridge.py:246` DEFAULT_QUIET_START_HHMM="23:30"（终点 07:30 无对应设计值，设计只给了 22:30 一侧） | 比设计晚 1 小时；宿主可调。桌面 Go 侧另有 23:00–08:00 的静默窗（Go 宿主不在本仓库内，本仓库 grep 无 .go 文件，此处按批注如实转记）——两处起点互不一致（23:00 / 23:30），且都晚于设计的 22:30；等统一裁定 |
| 日界统一 +08:00 vs 骨架世界 Tokyo | 世界侧业务日界统一 +08:00（2026-11-14 T1 批次演化；骨架世界身份有意保留 Tokyo） | world_living_skeleton 的 WORLD_TIMEZONE 仍为 Asia/Tokyo | `components/world/scripts/world_living_skeleton.py:43` WORLD_TIMEZONE="Asia/Tokyo" | T1 批次有意保留：骨架世界的"世界身份"（诗穗市世界观）与其余世界侧业务日界是两回事；:1163 有 timezone == WORLD_TIMEZONE 校验，改掉会破坏骨架身份契约。登记为"有意保留的演化"，非遗漏 |
| SlotQuota 四时段槽位 | 四时段槽位配额 morning 6/12500 · afternoon 7/14000 · evening 7/14000 · night 4/9500，carry_ratio=0.5（v3.0 07章 §四 DailyEnvelope.slots） | 未实现 | —（无任何代码实现槽位切分与结转） | **未实现，暂缓裁定**（07 章承诺）。2026-11-14 T3-wave2 只落地了日 token 预算维度（`components/life/scripts/life_request_budget.py` DEFAULT_DAILY_TOKEN_BUDGET=50000，fail-closed，与既有调用上限并行），四时段槽位切分、槽内 calls/tokens 双上限与 carry_ratio 结转均未做，等维护者裁定是否需要 |

---

## 三、本批实现取舍备忘（供裁定参考）

1. **48h 间隔的作用域**：设计原文把"单次间隔 ≥48 小时"写在桌角便签条目下；本批按 10章总公理
   "不对称原则：出站窄而贵"对全部主动出站渠道（desktop_note / slow_mail / cross_channel_ping）生效，
   且把 UNKNOWN（运输结果未知）记录也计入间隔基准（fail-closed：不知道上一封有没有送到，就不在
   48h 内再发）。属"比设计文字略严"的解释扩张，登记于此供追认。
2. **第三周归零的"周"**：按 ISO 日历周（周一起算）判定"连续两周"；数字型周配额（≤2/≤3）仍按既有
   滚动 7 天窗口计。两种"周"语义并存，各有出处（设计 §2.1 原文"每周" vs "滚动 7 天"）。
3. **慢信冷静期是拒绝不是推迟**：`post_slow_letter` 在 `now < drafted_at + 72h` 时抛
   `SLOW_LETTER_COOLING_PERIOD`（与本模块其余契约违规同为 fail-closed ValueError），不静默改写
   deliver_after。当前模块没有独立草稿态；`drafted_at` 缺省视为"起草时刻=寄出时刻"，即默认无冷静期
   债务——在出现真实起草流程之前，该校验由调用方传入 drafted_at 才生效（最小诚实方案）。
4. **顺序**：出站守卫的判定顺序为 回退(backoff) → 静默窗 → 渠道开关 → 日配额 → 滚动7天 → 便签周/第三周
   → 慢信月 → 48h 间隔。既有 reason_code（OUTBOUND_QUOTA_EXCEEDED 等）优先于新增的
   OUTBOUND_INTERVAL_TOO_SOON，使既有"日配额拒绝"审计测试语义保持可复现。
5. **日配额语义**（T2 遗留⑤，2026-11-14 X1 批次澄清；API 兼容：字段名不改、字段不删）：
   日配额（`daily_proactive_quota` 及别名 `daily_max` 等）是**宿主静音开关**——调 0 即全停出站，
   仅此一个方向生效；常规出站节奏由 48h 最小间隔（`DEFAULT_MIN_OUTBOUND_INTERVAL_HOURS`）主导，
   间隔约束下每自然日实际 ≤1 次，故日配额 **>1 的取值不可达**（永不成为实际约束），仅作兼容
   字段保留。该语义已写进 `components/world/scripts/v03_economy_illness_bridge.py` 常量注释
   （仅注释，零行为改动）。

---

## 四、T3-wave2 补登记（2026-11-14 下午批：WakeTicket / 成年亲密接线 / T1 尾巴）

1. **WakeTicket 持久唤醒票（07 章 §二）已落地最小实现**：票账为 `wake_router.wake_tickets`
   有界列表（与 operation_ledger 同风格：state 内随 `_save_state_atomic` 原子落盘、重启由
   `_load_state_readonly` 载入、容量 128 截断防膨胀），字段
   ticket_id/priority/coalesce_key/folded_count/fold_trace/est_tokens/created_at/
   lease_until/status(open/fired/folded_into/expired)/bypass。语义合并：同 coalesce_key
   窗内事件折进既有票（folded_count+1），不新建票；control/foreground 旁路票照记并标注
   bypass；背压延期记 open 延期票（07 章"超限不直接丢弃"），lease 过期未消费的 open 票在
   读投影内就地定期为 expired（只读对账，零写入）。对账读面：runtime-migration 投影
   `wake_coalescer.wake_tickets` + `wake_tickets_summary`（folded 合计、est_tokens 合计）。
   **合并窗仍为 300s**（§二"暂缓裁定"行不动）。
2. **日 token 预算维度已落地（07 章 §四 tokens_max=50000）**：
   `life_request_budget.LifeRequestBudget(daily_token_budget=...)` 与既有 24 次调用上限
   并行，fail-closed、reserve 先于 HTTP、无退款、损坏即拒、回拨不重置（既有铁律未动）；
   无票 est 时按保守常数 DEFAULT_CONSERVATIVE_TOKEN_RESERVATION=2000（50000/24≈2083，
   取 2000 使 24 次保守常数计仍低于 50k，调用上限先绑定）。`daily_token_budget=None`
   （未配置）语义与 utc_day_max=None 一致：不设该维度上限——既有 utc_day_max=300 的
   实验流（history_choice 等）不受影响。**生产接线已落地（2026-11-14 X1 批次）+ 已启用
   50000**：`autonomous_life_assembly.request_budget`（grep 确认的生产装配点，resident
   经此建账）①默认携带 `daily_token_budget=DEFAULT_DAILY_TOKEN_BUDGET`（50000；宿主旋钮
   `CHIYO_AUTONOMOUS_DAILY_TOKEN_BUDGET` 可调低/关闭，非法值拒绝启动——绝不静默变"没上限"）；
   ②返回值经 `WakeTicketEstimateRequestBudget` 包装，每一次生产 reserve（BudgetedCognition
   Adapter / BudgetedModelClient 的 HTTP 边界一跳）注入当期票账
   `wake_tickets_summary.est_tokens_total`（对账投影同源、零写入；无票/票账不可读/无 phone
   目录 → 保守常数 2000；调用方显式 est 不被覆盖；包装器对其余属性/方法全部转发，含
   resident 段上限的属性写）。**行为影响证明**：既有 24 次调用上限 × 保守 2000 = 48k < 50k，
   启用前后在调用上限内逐次记账一致、第 25 次同由 REQUEST_BUDGET_UTC_DAY_LIMIT 拒绝
   （tests/test_autonomous_life_budget_wiring.py 钉住），启用本身零新拒绝。接缝测试
   （tests/test_v02_wake_tickets_budget.py）保持全绿。
3. **成年亲密与共在 owner 接线已落地（36B-4 §9 / 36B §5.4 / ADR-047）**：
   `life_co_presence` 新增封闭模式 `PRIVATE_INTIMATE`（CopresenceMode 提议值）与 owner 侧
   公开登记 API（register/degrade/close/read，co_presence.json 同一单槽位记账，单前台沿用
   既有语义：槽位 OPEN 时 consider() 依旧 DEFER）；`adult_intimacy_view` permit ACTIVE 时
   经该 API 登记（fail-open：owner 故障/槽位被占只记 `co_presence_binding`，绝不阻断场景）、
   PAUSED/AFTERCARE 降级、终态关闭；入场断点（36A §11.1）存 permit 记录 `checkpoint`
   （activity_ref/saved_at）——本引擎读不到 Life 活动，activity_ref=None/saved=False 是诚实
   "未保存"，AFTERCARE/STOPPED 标记 resumable=True；过夜重决定（36B §5.4
   declared_seconds=86400）在 advance_beat 写入边界强制暂停
   （pause_reason=OVERNIGHT_RECONFIRM_REQUIRED），既有双方 confirm_resume 即"重新决定"。
   共在 root 取 `life_state_root` 布局（NYA02_RESIDENT_STATE 优先，默认
   `<root>/one-day/life/state`）——36B-4 §9.1"接在哪里"本就是缺口补全待决项，登记为
   按实例默认布局接线的既成事实，等裁定。停止优先铁律未动：ADR-044:34 的 in-flight 闸
   只限 PAUSE，停止/撤回照旧不受限（36B 铁律确认成立，未新增对冲逻辑）。
4. **恢复执行（断点续跑）不在本批**：checkpoint.resumable 只做标记，排队/重启任何活动
   的执行路径留待后续批次。
5. **修复既成缺陷一处**：`components/phone/adult_intimacy_view.py` 的
   `ROUTE_PROBE_FRESHNESS_SEC`（X15，14 天路由探针新鲜度）在接线前世代即只有使用处、
   缺定义（任何 register_render_route 后的投影 NameError，85 例亲密测试全数失败）。
   按 2026-11-14 08:40 世代 pyc 证据恢复原值 `14 * 86400` 与原位置；登记为既成缺陷修复，
   非本批新引入。
6. **冻结锚点重钉（X1 批次，装配文件有意变更的既定协议）**：07 章 §四生产接线必须修改
   `autonomous_life_assembly.py`，其 SHA-256 被冻结锚点钉在 18 处：1 处源文件
   （`v02_v03_daily_loop_bridge.py` 的 `FROZEN_ANCHOR_SHA256`，经 `verify_frozen_anchors`
   被 soak 套件校验）+ 17 处测试文件硬编码（test_v02_phone_http_surfaces /
   test_autonomy_rhythm_view / test_v03_clock_evaporation_stance_world /
   test_v04_neighborhood_living / test_v02_v03_causal_counterfactual_soak /
   test_hermes_phone_brain_wiring / test_hermes_hot_reconnect_and_surfaces /
   test_sensory_character_view / test_long_companionship_view / test_outing_places_view /
   test_somatic_comfort_view / test_v02_residual_and_ch19_closure /
   test_v02_hermes_and_bridge_wiring / test_v03_http_routes / test_v04_http_and_hermes_wiring /
   test_v04_world_builder_trylive / test_wardrobe_pocket_view）。全部 18 处已随
   76b4c7cb… → 5f51c9e8… 重钉（历史先例：锚点曾由 8174be 重钉至 76b4c7cb），受影响 18 套件
   重跑全绿。**有意不动的旧摘要**（非测试门禁，属发布时元数据/历史证据）：
   `MANIFEST.json`（今日 T1/T2/wave1/wave2 各批改动文件本就未刷新，刷新属发布批次职责）、
   `.agents/progress/current.json`（连续性快照，内部另有"不重写旧摘要"约束）、
   `docs/engineering/v02-v03-causal-loop-acceptance.md` / `v02-v03-full-closure-acceptance-20261114.md` /
   `v03-clock-stance-containment-api.md`（验收时点历史证据，不追溯改写）。发布前须整体
   重算 MANIFEST。

---

## 五、v0.7.0 发布登记（2026-11-15）

- **发布范围**：本登记文件所列全部批次（T1、T2、T3-wave1、T3-wave2、X1、X2）随 **v0.7.0 收敛版**发布（含 2026-11-14/15 全部修复与新机制；v0.8.0 版本号保留未用）。
  施工与验收记录见 `docs/engineering/release-readiness-20261115.md`（发布就绪文档，含变更清单、
  MANIFEST 刷新记录、逐套件实测证据与发布步骤）。
- **暂缓裁定项随版本带入，不因发布视为追认**：§二"暂缓裁定"表 6 项（急性期进入阈值 0.80／
  退出严重度 0.38、合并冷却窗 300s、quiet hours 起始 23:30、骨架世界 WORLD_TIMEZONE=Asia/Tokyo、
  SlotQuota 四时段槽位未实现）在 v0.7.0 中原样生效，逐条仍等维护者裁定或下轮契约修订收口；
  发布动作本身不构成对上述既成演化的追认，§一"登记不等于追认"的文件级声明继续适用。
- **§一.1（S07 Affect owner）随版本发布**：该条"最小 owner 已落地"状态随 v0.7.0 进入发行树；
  其边界（不开持久 affect store、记录由调用方辖区存储）同步生效，仍不等于追认扩展。
- **发布时就绪职责的履约**：§四.6 之"发布前须整体重算 MANIFEST"已于 2026-11-15 由发布收尾
  批次履行（归档树语义 staging 重生成，终态 13630 文件，version 0.7.0，staging `verify_manifest.py`
  valid=true；算法与数字见 release-readiness-20261115.md §二）。`scripts/release.json` 同步升至
  `v0.7.0`（会话中途曾按补丁单标 0.8.0，2026-11-15 用户裁定改道 v0.7.0 后回落）。安装器两处版本串
  （`scripts/bootstrap.sh:6/:23`、`website/install.sh:6/:23`）初版收尾单无权限未动、如实登记为对齐项；
  **已于 2026-11-15 收尾补丁单按协调者明示指令升起，并随用户裁定定版为 `v0.7.0`**
  （各文件内 `uv >= 0.6` 为 uv 工具版本提示，非产品版本串，不动），四处版本口径自此对齐。
- 本节只是发布登记，不改 §一～§四任何一条的登记语义。
