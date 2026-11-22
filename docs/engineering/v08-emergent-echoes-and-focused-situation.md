# v08 涌现回响与专注情境（Emergent Echoes & Focused Situation）

> 实现：`components/life/scripts/v08_emergent_echoes.py` + `components/phone/v08_emergent_echoes_view.py`
> 路由：`GET/POST /api/phone/v08/emergent-echoes`
> 状态：`<root>/phone/v08_emergent_echoes.json`（`phone.v08.emergent_echoes.v1`）

## Problem

长期关系里的「生活感」很容易被写成每天打卡的提醒债：种一个问题就要到期催办，
确认一个说法就要算亲密度，尝试一次兴趣就要给熟练度打分。这样会把关系变成任务清单，
也会让角色说出来的话像系统提示，而不像两个人自己长出来的默契。

需要的是几个极小、通用、不依赖大模型的原语：它们只记录「发生过什么」，
不制造债务、不计算分数、不预设情景脚本，读的时候也绝不写盘。

## Decision

五个原语，全部零 LLM 调用、可读可写、按需裁剪：

1. **给未来自己的问题**（`future_self_questions`）
   - `plant_future_question` 只存问题、锚点、关键词和可见性；
     没有 `due_date` / `overdue_count` / 每日提醒计数。
   - `revisit_future_question` 只有三种收束：仍然好奇（保持 open）、已经回答（answered）、
     让它淡掉（faded）。
2. **只有彼此懂的说法**（`shared_idioms`）
   - 双方都能 `propose` / `confirm` / `fade`；状态是 proposed → confirmed → faded。
   - 情境命中时附带固定的「不强迫照演」提示：接住心意、说明真实状态即可。
3. **同一件往事后来理解得不同**（`memory_reinterpretations`）
   - 第一次记录即永久冻结 `original_fact_cn`；后续同 `episode_ref` 的修订只追加
     `revisions` 并更新 `current_view_cn`，绝不覆写原始事实。
   - `share_memory_reinterpretation` 单独控制伴侣可见性。
4. **兴趣迁移火花**（`style_and_interest_sparks`）
   - `familiarity`（会做）、`craft_fit`（做得好）、`mood_pull_today`（今天想做）严格分立，
     没有标量技能等级。
   - `method_validated` 只在 `outcome == "helped"` 时置真；`did_not_fit` 明确不匹配；
     `skipped_today` 只是休息，不惩罚、不清空既有验证。
5. **两个人带回不一样的注意**（`divergent_notices`）
   - 按 `target_ref` 并列保存 `subject_notice_cn` / `partner_notice_cn`，
     永不计算一致度或合并成一条「共识」。

**零写读出**：`read_emergent_echoes_zero_write`、`project_emergent_echoes_zero_write`、
`match_situational_echoes_zero_write` 以及 HTTP 视图的读与摘要，全部不建目录、不建临时文件、
不触碰 mtime；状态文件缺失时返回空默认集合。

**权限与隐私**：伴侣视角（`has_subject_capability=False`）整行剔除
`visibility == "private"` 的问题与火花、以及 `shared_with_partner != True` 的往事重解，
不输出任何 hidden/private 计数。伴侣调用主体动作返回 `403`；不存在的记录返回 `404`，
不泄露某个私密 id 是否存在。客户端自带的身份键或关系/游戏化分数键返回 `409`。

**开源中立**：人称代词从 `character_pack` 动态读取，缺省 `"TA"`；
模块内不出现任一个体姓名或固定性别代词。

## Alternatives considered

- **用每日提醒 / 到期队列来维持回访**：最容易让问题「被看见」，但会把好奇变成债务，
  角色会像催办系统一样说话。否决：违背「不做强制人类式打卡」的基石决策。
- **用一个亲密度/熟练度标量驱动所有板块**：实现最省事，但会把「会做」「做得好」「今天想做」
  压成一个数，失去真实的取舍与犹豫。否决：`v3.0` Ch.24 §七明确要求分离。
- **只保留主体一侧写入**：伴侣无法提出共同说法或带回自己的注意，关系会退化成单机日记。
  否决：共享说法与分歧注意本来就该由双方共同长出。
- **在第一次之后允许修正 `original_fact_cn`**：看似更准确，但会让「后来的理解」偷偷改写
  过去，破坏「同一件往事、不同理解」的张力。否决：原始事实永久冻结、只追加修订。

## Consequences

- 用户可见：手机端可以种下问题、回看并让它自然淡掉；双方能确认只有彼此懂的说法；
  往事会出现「后来的理解」而不抹掉原貌；兴趣火花可以试、可以休息、也可以承认不适合；
  同一件事上两个人的注意并列存在。
- 维护代价：五个列表各有上限（问题/说法/往事 64，火花/注意 96），`operation_key`
  账本上限 256，状态文件随日常使用缓慢增长但不会无界膨胀。
- 读出成本：所有读路径都是纯内存解析 + 字符串匹配，零外部调用；适合放进每一轮聊天背景。
- 未做：跨设备同步、全文索引、按时间衰减的自动隐藏；这些都留到确实需要时再加，
  当前版本只保证「发生过的事能被温和地重新看见」。
