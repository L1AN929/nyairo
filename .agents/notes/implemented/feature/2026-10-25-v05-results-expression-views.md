# Agent Note: v0.5 第一块 —— 有界的显式评价投影与同轮表达来源

Status: implemented

## Problem

后果回流不是完全没有：`DecisionRecord.method_ref/method_trial`、Practice owner 保存的
trial 明细、作品版本与 step receipt 都已存在，下一轮与重新装配都从 owner 重读，TRIAL /
CONTESTED 状态本来就能进入判断。真正丢在两处接缝上的是**更小的读侧损耗**：

1. 方法列表行已经有 `trial_count` / `counterexample_count`，但最终 decision 的 practice
   白名单在改前**两者都没有**进入（改前只带 `method_ref/title/instruction/scope/
   uncertainty/method_state/head_version_id`，见 `git diff` 对
   `agency_cognition_adapter.py` 白名单段）；owner 已保存的最近一条显式 trial
   （outcome / 受评版本 / 证据条数）也从没被正式决策读路径消费。
2. 分享来源只有 `artifact_id` / `kind`，没有可读名字；`prompt_block` 的 `truncated`
   在 adapter 白名单被丢掉，模型看不出展示的分享列表被源侧截断过。

前轮只读核查给出的最大诱惑是"新增持久 usage 记录"，用一张新表补"没有 trial 也能跨重启
恢复精确使用版本"。那会造第二个真相源，超出本轮范围。

## Decision

**只做有界读视图，不新增任何持久使用记录。**

1. `PracticeSeam.evaluations_for(rows, max_reads=3)`：只对本轮实际 offered 的最多 3 条方法
   各做**一次**既有治理 `read_method`，取最近一条显式 trial 的 `outcome` / `version_id` /
   `evidence_count`；`detail` 正文永远不投影，`evidence_usable` 也不冒充本轮重新确证。
2. 列表 head 与详情 head 不一致 → `PRACTICE_METHOD_HEAD_CHANGED`，不拼接、不再追读；
   详情读失败 → `PRACTICE_METHOD_DETAIL_UNAVAILABLE`，可读列表保留；owner 扣留证据 →
   `PRACTICE_EVIDENCE_WITHHELD`，不从原文/缓存/Decision 旁路恢复；没有显式 trial →
   `PRACTICE_METHOD_NO_EXPLICIT_TRIAL`，不推断"没用过"。
3. cycle 每回合只富化一次（重复调用同一回合的 `decision_situation_context` 不加读），
   把结果 merge 进本轮自己的列表行；owner 写路径、CAS、HELPED 证据门、退休语义全部不动。
4. 最终 practice 白名单照传真实整数计数；缺值保留 `None`（未知），不补 0。
5. 分享来源只在**同一次已实际读取**能配对时带 `title`（作品名来自本轮 resume point，
   阅读材料名来自本轮缓存且 `reading_artifact_id` 必须对上）；缺标题/身份不符就省略，
   不新增 owner 读。`truncated` 分别如实报告 owner 列表自身的截断与 prompt bound 截掉的
   OPEN 草稿，随后经 `_bounded_sharing` 透传；整块仍按既有 prompt 预算裁剪。

## Alternatives considered

* **新增持久 `method_usage` 表/日志。** 最强立论：没有 trial 时也能跨重启精确恢复"当时用了
  哪个方法版本、后果是什么"。否决：那是第二个 owner 真相源与新的写入合同，代价和治理面都
  远超"让已有事实可读"；而且现有 `last_method_usage` 只是回合内状态，把它持久化必须先定义
  归属、幂等和删除语义，不属于本轮。
* **让 cycle 自己按 owner 行拼评价（绕过 `read_method`）。** 否决：`read_method` 是既有治理
  读口（扣留正文、证据核对、递归检查都在这条路上）；自己读等于自造第二个读权限。
* **为了显示详情放宽 `evidence_usable` / 把历史 trial 当本轮确证。** 否决：那会把"当时有证"
  说成"现在可读"，掩盖治理撤回；本轮只报记录本身的元事实。
* **为了凑标题再读一次作品 owner。** 否决：本轮写集只允许复用同轮已读取的数据；identity
  对不上就省略名称，比多一次不一致的读更诚实。
* **把 `truncated` 当成"最终 payload 完整"的保证。** 否决：它只描述该 sharing 列表；整块
  仍可能被 prompt 预算丢掉，两件事分开说。

## Consequences

* 下一轮决定能看到：方法状态、真实的 trial / 反例计数、最近一条显式评价的 outcome、
  受评版本和证据条数 —— 但不会知道"这个方法从没被用过"，因为那仍然未知。
* 代价：每个正式决策回合最多 3 次额外 owner 详情读（不花 LLM）；同一回合内不重复取数，
  重新装配后从同一 owner 读回相同事实，不依赖 `last_method_usage`。
* 分享来源可能在 prompt 里第一次有名字，并明确"这份列表被源侧截断过"；名字不是版本、
  不是披露权，受众/通道/外发一行没改。
* 不能宣称：真实模型自然采用这些新字段、G1、24h、完整 resident crash 恢复。决策与内容在
  验收里仍是脚本替身，真实 owner 覆盖 Supply / M0 Memory / Practice / Sharing。
* 相关决定原地同步在
  [实践与做法](../architecture/2026-11-12-v04-practice-life-review-and-method.md) 与
  [身体参与与有分寸分享](../architecture/2026-11-14-v04-body-participation-and-bounded-sharing.md)。
* 证据：`tests/test_v05_results_expression_views.py`、`scripts/v05_results_expression_smoke.py`；
  固定验收 A（4 个测试模块）`Ran 43 tests ... OK`、exit 0，B（真实 owner 冒烟）
  37 项 check 全 PASS / exit 0。
  2026-10-25 收尾补上真实 Supply 阅读材料的正式配对：实例既有绑定 →
  `assemble` → `evaluate_once` → AG-1 请求，`sharing.sources` 里的
  `reading_material` 与同轮 `refresh=True` 读到的 `material.title` 成对出现，
  生产路径 0 次额外 owner 读（B 中 3 项 reading check）。
* **截断读侧缺口已修（同一收尾的直接返修）**：`truncated` 原先有两层漏接——
  `life_sharing_service.list_shares` 忽略 `private_project_service.list_projects` 返回的
  `truncated`，又用自身收到的行数与同为 `MAX_LIST=100` 的上限重算；`prompt_block` 按
  `MAX_PROMPT_SHARES=5` 截掉 OPEN 草稿时也只透传上一层。现在两处都只做既有字段的准确投影：
  - 101 条真草稿 → 分享列表返回 100 行且 `truncated=True`，`counts.total` 仍是 100
    （当前答案返回的行，不冒充全量）；
  - 6 条真 OPEN 草稿 → prompt 展示 5 条且 `truncated=True`，第 6 条仍是 DRAFT、无 receipt。
  修复不翻页、不改 `MAX_LIST` / `MAX_PROMPT_SHARES` / 状态过滤 / 排序 / counts / 读写次数；
  缺 `truncated` 字段的 owner 答案仍报 `False`。真实 owner 证据在两个新测试类
  （`RealOwnerShareTruncationTest`）和 B 的独立根截断链（7 项 check）；真实模型自然采用、
  真实回合延迟仍未验。
* **跨进程重开（2026-10-25 晚）**：这些有界读视图不依赖进程内状态。两个真正不同 PID 的
  同根链里，P2 重开真实 owner 与 AG-2 store 后的第一次正式请求就看到了 TRIAL 方法
  （含 trial 计数）、DEFERRED 草稿、作品来源与 stale 事实；同一请求没有写任何版本或
  receipt。续写后的请求仍从 owner 重读同一批事实。证据：
  `tests/test_v05_restart_continuation.py`、
  `scripts/v05_restart_continuation_smoke.py`；交付说明
  [v05 restart continuation](../../../../docs/engineering/v05-restart-continuation-20261025.md)。
  这不改变本 note 的边界：真实模型自然采用、真实回合延迟与 G1 仍未验。
