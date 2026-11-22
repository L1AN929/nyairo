# v0.8 生成式机制总览：四层组合如何互相加固

日期：2026-11-16 · 零产品 LLM 调用（全链路 `llm_calls == 0`） · 零付费模型依赖

> 这是一张**工程地图**，不是新契约。细节条文与验收证据在下列分册里，本文只讲四层如何拼成
> 一个「不用给每个场景写专属动作」的生成式底座：
>
> - `docs/engineering/v08-universal-matter-and-open-act.md`（物质底座与七原语）
> - `docs/engineering/v08-emergent-echoes-and-focused-situation.md`（五个涌现原语与专注情境）
> - `docs/engineering/v08-open-living-traces.md`（生活痕迹与开放动作写面）
>
> 可执行的不变量由 `tests/test_v08_antipuppet_emergent_audit.py` 跨引擎守住（详见文末）。

## 0. 一句话

少数**小、可组合、无剧本**的机制，加上 WorldGen / World-Editor 携带的物理元数据，让
「在窗边坐一会儿」「把纸片淋湿字晕开」「把番茄和乌冬煮成一碗自己命名的面」这类场景
都由同一组原语自然长出来，而不是每条写一个 `do_xxx` 作弊键。

四层从上到下是：**作者层（WorldGen / World-Editor） → 物理层（Universal Matter） →
时间与关系层（Emergent Echoes） → 情境与呈现层（Focused Situation / 桌面伴侣）**。
上层只声明「世界是什么样」与「发生过的客观事实」，下层只做确定性推演与有界投影，
没有任何一层需要认识具体剧情。

---

## 1. WorldGen + World-Editor → Universal Matter：`matter_profile` 统一

**作者层不带行为，只带物理元数据。** 房间、家电、物件在 WorldGen 生成或 World-Editor
编辑时，通过可选的 `matter_profile` 声明一件东西的物理属性，键集合封闭为七项
（`components/worldgen/schema.py::MATTER_PROFILE_KEYS`）：

| 字段 | 含义 | 谁来推演 |
| --- | --- | --- |
| `ambient_zone` | 所处环境（room / cold / heating …） | 随时间改变冷却/冷藏/干燥速率 |
| `water_source` | 是否是水源（水槽、龙头） | 决定清洗/接水等原语可用性 |
| `capacity_slots` | 台面/容器槽位数 | 放置与容量闸门 |
| `item_kind` | 物品品类（食材 / 成品食物 / 器皿 / 纸 / 工具 …） | 决定可执行的原语分支 |
| `paper_like` | 是否纸类 | 受潮晕字、脏台面拒放 |
| `shelf_life_hours` | 保鲜时长 | 新鲜度随钟表推进而衰变 |
| `portions` | 份数 | 吃一半留一半、拼盘分份 |

**推断与校验是纯函数，且不写回行为。** WorldGen 用
`components/worldgen/generator.py::infer_matter_profile` 按品类/名称/备注给出确定的
物质档；World-Editor 桥 `components/worldgen/editor_bridge.py` 用
`_object_matter_profile` / `_edited_matter_profile` 把它读入、经
`schema.normalize_matter_profile` 严格校验后写回世界包（未知键、类型错误、越界值一律报错，
不静默纠正、不补默认值——老包与只声明部分字段的包保持原样）。

**物理层自动执行，不需要场景代码。** `components/world/scripts/v08_universal_matter.py`
把作者声明的区域/物件在内存里具现为统一物理状态（热态、熟度、干湿、洁净、新鲜、份数、
纸感、借出、可见性），再用**七个开放微原语**任意组合成 1–6 步行动
（`OPEN_PRIMITIVES`）：

```
PLACE_OR_MOVE · INTRODUCE_ITEM · TRANSFORM_CREATE · CONSUME_PORTION
CLEAN_SURFACE_OR_VESSEL · DISCARD_ITEM · OBSERVE_OBJECT
```

读侧在深拷贝上按钟表推演：室温冷却、冷藏保鲜、湿纸干燥、过期变质；纸上的字在
`note_legibility` 里由**实时干湿**客观推导——湿/潮时读作 `smudged`，房内空气把它重新
晾干后又读回 `clear`（`_inscription_text` 记录的是题字本身，不是改名）。因此
「拿画笔插进杯子」「把菜煮成一碗」「纸被水汽晕字」都不必新增动词，只需原语组合 +
时间推进。

---

## 2. 五个涌现时间与关系回响原语（`v08_emergent_echoes`）

引擎：`components/life/scripts/v08_emergent_echoes.py`；HTTP 闸门：
`components/phone/v08_emergent_echoes_view.py`。五个板块都是「长期过日子自然长出来」
的小状态，**没有每日提醒债、没有情景脚本、没有亲密度/服从度之类的分数**：

1. **`future_self_questions`（给未来自己的问题）** —— 只有 `plant` / `revisit` 两个动作；
   没有 `due_date` / `overdue_count` / 每日提醒计数。回看收束为
   `still_curious`（保持 open）/ `answered_now`（answered）/ `let_fade`（faded）。
2. **`shared_idioms`（只有彼此懂的说法）** —— 双方都能提出、确认、淡忘；命中时附
   `OBLIGATION_FREE_NOTE`（接住心意但不强迫照演），绝不折算默契分。
3. **`memory_reinterpretations`（同一件往事后来理解得不同）** —— 首次记录即永久冻结
   `original_fact_cn`，之后的修订只追加 `revisions`，绝不覆写原始事实。
4. **`style_and_interest_sparks`（兴趣迁移火花）** —— `familiarity`（会做）/
   `craft_fit`（做得好）/ `mood_pull_today`（今天想做）三轴严格分立，没有标量技能等级；
   只有 `outcome == "helped"` 才把 `method_validated` 置真，`skipped_today` 是休息而非惩罚。
5. **`divergent_notices`（两个人带回不一样的注意）** —— 按 `target_ref` 并列保存双方
   各自的注意，绝不计算一致度。

全部读侧零写盘；伴侣视角过滤 `private` 行且**不泄露隐藏计数**；客户端夹带
`uid / actor_side / has_subject_capability / intimacy_score / favorability /
relationship_xp / skill_level` 等身份或关系分数键，一律在闸门处干净拒绝并回
`CLIENT_IDENTITY_REFUSED`，绝不落盘。

---

## 3. 聚焦情境选择器（`lived_context_bridge`）

`chiyo_bundle/lived_context_bridge.py` 把上面这些涌现状态与领域线索，按**当前伴侣这一轮
的话**动态匹配，浓缩成一段 `<= 1800` 字符（`MAX_LIVED_CONTEXT_CHARS`）的有界背景串，
交给对话侧自然使用——不是每天固定播报，而是「这一句提到了什么，就温和地带出什么」。

匹配是纯文本、零写盘、零模型：中文在无分词依赖下用整词 + 滑动 2/3/4 字窗口召回
（`match_situational_echoes_zero_write`），只在命中的说法/问题/往事/火花/注意上取一条摘要。

同时，三条边界被**钉死**而非让位给风格：

- **自主与拒绝**：`autonomy_line`（挂着的事、拒绝立场锁、情绪张力）优先置顶，
  一段活跃活动或一次明确拒绝绝不会因为篇幅裁剪被挤掉；
- **36B 隐私铁律**：只读日常非露骨边界，绝不读取、拼装或暴露 `solo` / `solo_slot` /
  `solo_ledger` 等无记忆独处槽；
- **有界**：超出 `1800` 字符时按优先级裁剪，而不是无限制堆叠。

---

## 4. 伴侣零凭据网关与界面（`nyairo-desktop`）

桌面伴侣进程不持有主体能力，只通过**零凭据代理**读取已脱敏的伴侣视角，再把真实状态
渲染成 Kazumi 风格的极简卡片与抽屉：

| 桌面路由 | 上游 | 脱敏要点 |
| --- | --- | --- |
| `/api/v08/living-traces` | `/api/phone/v08/living-traces` | 整条删除 `private` / `subject_only` / `shared=false` 的贴纸、铭文、套路；不保留 `hidden_*` / 私密计数；动作白名单只放行伴侣自己的 `leave_partner_trace` / `clear_partner_trace` |
| `/api/v08/v03-encounters` | `/api/phone/v03/encounters` | 深层剥离未共享材料与私密页；只呈现「看过几页」的有限视野 |
| `/api/v08/emergent-echoes` | `/api/phone/v08/emergent-echoes` | 伴侣视角过滤私密问题/火花与未共享往事重解，不泄露隐藏计数 |

界面层（`web/index.html` 的三联瓷砖 + 折叠抽屉）只消费上述干净 JSON；人称代词从角色包
动态读取，缺省为 `TA`——模块内不出现任何写死的个体姓名或固定性别代词。作者在
World-Editor 里改一间屋子、添一台家电，界面无需改一行剧本代码就能渲染其物理后果。

---

## 5. 四层如何互相加固（一条真实链路）

以「周末傍晚在窗边坐一会儿」为例：

1. WorldGen/Editor 给窗台一个 `matter_profile`（`ambient_zone=room`、`capacity_slots`…）；
2. 物质层据此允许「把杯子 `PLACE_OR_MOVE` 到窗台」「`OBSERVE_OBJECT`」等原语组合，
   并随时间推演杯里热饮的冷却；
3. 涌现层把它记成一条 `shared_idiom`「窗边时间」或一个 `future_self_question`，
   不生成任何催办；
4. 伴侣下一句若提到「窗边」，`lived_context_bridge` 只带出这一条命中摘要（≤1800 字符），
   自主/拒绝/36B 边界仍然置顶；
5. 桌面 `/api/v08/*` 代理只把脱敏后的实际痕迹交到界面，用动态 `TA` 渲染。

全程没有为「窗边」写一个专属动作，也没有调用任何付费模型。

---

## 6. 由测试守住的不变量

`tests/test_v08_antipuppet_emergent_audit.py` 把这套哲学变成可执行断言：

- **零写死人名/代词**：程序化构造禁用码点，扫描 `v08_emergent_echoes.py` /
  `v08_emergent_echoes_view.py` / `v08_universal_matter.py` / `editor_bridge.py`；
- **封闭分数 / 傀儡键拒绝**：两个写面在收到身份或关系分数键时返回 400/403/409，
  且不创建任何状态文件；
- **读侧零写**：三个涌现读面在空缺 root 上读取后文件/目录数不变、`llm_calls == 0`；
- **Scorecard 不平均、硬失败一票拉高**：`averaging_forbidden` 与
  `hard_fail_triggers_high` 恒为真，伪造好感分字段直接判 `HIGH`，不被其余 `LOW` 稀释。
