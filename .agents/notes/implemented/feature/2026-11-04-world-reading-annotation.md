# Agent Note: 世界里的文字读完之后留下自己的批注

Status: implemented

## Problem

世界包 `content.json` 的 `entries[<place>].reading` 已经进入她的上下文（地点目录带 `reading`，
`world_move_opportunity_source.daily_life_context` 与 `life_live_context_view` 都放行），2026-11-04
的真跑里她也在书店阅读角选了 `stay:reading_point:READ_HERE:600`、待满 30 逻辑分钟并
`COMPLETED_WITH_EVIDENCE`。但那一刻什么都没留下：读完就是读完，既没有批注，也没有记录。

对照之下，读**私人材料**（`READ_PERSONAL_MATERIAL`）会写批注：`_reading_step_spec` 用
`private_daily_items.plan_page_read` 记取得与停点，再让模型对那一页作答、用
`plan_page_annotation` 落一条 `memo` / `unclear_marker`。缺的那条边只是：世界停留完成之后没有
走到同一套纯规划器。

## Decision

1. **触发点是"停留完成之后"，不是完成条件。** `_classify_stay_step` 在 `complete=True` 且该地点的
   affordance 属于 `{"READ_HERE", "READ"}` 时调用 `_annotate_world_reading`。`STAY_AT_OBSERVED_PLACE`
   的完成语义（声明时长到 + 世界仍确认她在那儿）一字未改；写作是后果，失败不影响这次停留。
2. **落点和读私人材料完全一样。** 世界文本被当成一个普通来源：`source_id="world:<place>"`，
   `source_revision` 取这段文字的 sha256（同一段文字 = 同一版）。先 `plan_page_read` 记一条取得
   （`obtained={"pages":[1],"chars":N}`、`excerpt` 截断到 200 字）与停点，再走既有的
   `_reading_response_plan`：同一个模型作答形状、同一个 `plan_page_annotation`、同一个
   `reading_anchor`（source/revision/page）。没有新增 owner 合同、schema 或持久字段。
3. **文字来自同一次观察。** 文本从 `self.last_world_catalog` 读——`_observe_position` 在本轮停留
   判定前刚刷新的那份目录；不另读第二次，也不用事后补造的现场。
4. **一次 reading = 一个 owner 版本。** 取得/停点与批注的规划同读私人材料一样会各标一次版本号，
   合并后用 `_rebase_merged_version` 回落到 owner 真正创建的那**一个**版本；写入是
   `artifact_port.commit_version`，`expected_head` 是读到的 head，operation key 由取得/停点与批注
   意图共同决定。owner 调用走既有的暂停边界（`_pause_lease` + `life_pause.gate`），模型请求走
   装配时包好的同一个预算/暂停客户端。
5. **失败一律 fail closed，只记形状。** 读不到文档、writer 抛错、规划拒绝、暂停、预算拒绝、owner
   拒绝/未知：都只写进本轮 `world_reading`（state / item_type / version_number / reason），不抛出、
   不改文档、不影响停留完成；未知写入先 `resolve_operation` 再报。原文与批注正文不进报告、journal
   或日志。
6. **幂等。** 同一段文字再读一次：取得/停点的 item id 与批注的 operation key 都相同，纯规划器
   返回 `changed=False`，不产生第二个版本、第二条 memo；同一页不同意图会被既有规划器以
   `READING_OPERATION_KEY_REUSED` 拒绝，而不是悄悄覆盖。

## Alternatives considered

* **新增一种 Activity 类型（"读完写批注"）作为完成条件的一部分。** 最强立论：写作与阅读是一个
  连贯动作，完成即带上批注最自然。否决原因：任务明确不改 `STAY` 完成条件、不新增 Activity 类型；
  写作失败还会把"读完了"变成"没做完"，与 fail-closed 要求相反。
* **给 owner 加一个"以世界文本为来源"的新写入协议。** 最强立论：语义最干净，不必复用私人材料的
  取得模型。否决原因：`plan_page_read`/`plan_page_annotation` 本来就接受任意 `source_id`/
  `source_revision`，世界文本就是一个普通来源；新增协议等于第二套合同，超出本单写集。
* **把批注拼进停留的 step，让 AR-0 顺带提交。** 最强立论：一次执行、一个回执，账本最一致。
  否决原因：停留是 `STEP_KIND_INTERNAL_CHECKPOINT`，不提交任何 owner 动词；硬塞一个文件写入会改变
  停留的动作语义，属于"改完成条件"的边界。
* **第二次读用另一个停留再验幂等。** 最强立论：最贴近真实重复。否决原因：同一地点同一种停留的
  step 身份（`idem_step:v02:world:stay:start:<place>:<descriptor>`）由既有规则复用，第二次 START
  会被 `STAY_ALREADY_STARTED` 拒——那是本单之外的既有身份规则，改它不属于本工单；测试改在
  `_annotate_world_reading` 这一层直接验证 operation key 幂等。

## Consequences

* 现在她读完世界里的文字，私人日常事项文档里会多一条与读私人材料同形的 memo（或 unclear 标记），
  带 `world:<place>` 来源与文字指纹；同一段文字不会写第二条。
* 完成条件、Activity 类型、owner 合同、默认 gate、旧根、Memory 规则都没动；没有真实模型运行。
* 已知边界：只有 `READ_HERE`/`READ` 两种停留触发；一个地点同一种停留的重复 START 仍受既有 step
  身份规则限制；`world_reading` 只在停留结束时出现，NO_ACTION/提前离开不产生批注。
