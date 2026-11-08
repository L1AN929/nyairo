# Agent Note: 自动回顾候选的来源反馈边界（回顾成果不再当回顾种子）

Status: implemented

## Problem

回顾仍然只能是**普通可续写作品**：她选中回顾选项，B 的实践工作台真建一份回顾 A，AR-0 真写版本
R，AG-2/M0 再按现有政策把这次写作结果记成一条可回忆经历 E2（`artifact_ref` = A）。下一次
`life_project_options.review_seed` 从 A 的 Memory 里取「最近可读经历」当种子时，**E2 本身就在
候选池最前面**：于是一份回顾的写作结果被当成下一份回顾的素材，形成「回顾→写回顾→再回顾」的
自反馈供给。这不是 Memory 准入漏洞，也不是「同一根事件只能准入一次」；M0 的同结果幂等、异版本
各自成记录都照旧成立。要改的只有自动供给层这一条边。

## Decision

在**现有 owner 读口**上做精确分类，自动回顾种子不取「owner 已标记为回顾的作品的确证写作结果」：

1. **判据只用 owner 标记，不用标题/正文/时间**。经验行里的 `artifact_ref` 是 AG-2 交接时 owner
   自己确认的 `artifact:<id>`；对它调用既有 `PersonalPracticeService.read_review`，第一行
   `<!-- nyairo-practice:v1 ... -->` 的 `kind` 为 `review` 才算回顾。普通作品的标题恰以「回顾：」
   开头不会被误杀；真正回顾改成别的标题也绕不过标记。`life_practice_context.PracticeSeam`
   新增 `review_result_artifact_ids()`：每个不同的来源 artifact 至多一次 owner 读（上界
   `max_experiences`），不开第二索引；**Life 侧不检查正文语义、也不输出私文**。判据是
   `owner.read_review` 底层确实会读取该文档以取第一行标记，返回给 Life 的只有分类状态与
   artifact id，不带正文。
2. **三态，不把未知当普通**。`READY` 带回 owner 确认为回顾的 id；owner 明确回答
   `PRACTICE_KIND_MISMATCH`（做法）或 `PRACTICE_NOT_A_DOCUMENT`（普通作品）是确定的**非回顾**；
   其余（找不到/读失败/标记非法/服务错）一律 `UNKNOWN`。
3. **只有自动种子这一条边被切断**。`life_project_options.review_seed` 跳过来源是回顾结果的
   经历，并在同一有界集合里继续找合法项（首项是回顾结果时不吞后面的普通经历）；
   `build_options` 新增 `review_excluded_artifact_refs` / `review_source_verified` 两个默认参数，
   **缺省行为与原样完全一致**（其它调用方零改动）。没有任何经历、或没有可写实践工作台时，仍
   和以前一样没有回顾选项。
4. **源不可分类时具名扣留本轮自动种子，其它候选不受影响**。`UNKNOWN` 时
   `review_source_verified=False`，`review_seed` 直接返回 `None`，cycle 在回合报告里写
   `review_seed_withheld`（如 `PRACTICE_SOURCE_KIND_UNKNOWN`）。这不会被当成「她已经没有经历」，
   也**不会**因此拒绝正常 M0 读取或删掉经历。
5. **写集之外全冻结**：不新增 M0 字段/协议/存储、不改 AG-1/AG-2/LR 规则、不改指纹/TTL/排序/
   前台/step/Memory 写入、不改 `PersonalPracticeService` 的 marker 格式或手动
   `create_review` 允许嵌套的合同。

## Alternatives considered

* **按标题前缀「回顾：」过滤**。最强立论：零额外 owner 读，代码一行。否决原因：普通作品也可以
  以「回顾：」开头（误杀），而真正回顾改名就绕过（漏放）；这正是本任务禁止的命名/文本判断。
* **在 `PersonalPracticeService` 新增「读某工件的 practice 类型」专用 API**。最强立论：一次调用
  拿到类型，语义最直。否决原因：现有 `read_review` 已经用第一行标记给出确定答复（回顾=成功，
  做法/普通=具名拒绝），新增 endpoint 只是把同一判据实现第二遍，不动 owner 合同更小。
* **一次性 `list_reviews` 建全量排除集**。最强立论：一次调用覆盖所有回顾。否决原因：
  `_scan` 会逐个读最多 `MAX_SCAN=200` 件工件，远重于本轮候选经历的逐条分类；而候选池本身已被
  `max_experiences` 限死，逐条读更省且不依赖列表上界。
* **把 E2 从 Memory 里删掉/屏蔽，或让 M0 拒绝派生正文**。最强立论：从根上不再出现回顾结果经历。
  否决原因：那是改准入/删除治理，会让「普通 experience/history 仍可读 E2」这条原本合法的能力
  失败；M0 的同结果去重与异版本保留是既有合同，本单只改自动供给，不改 Memory。
* **源读失败时按「大概是普通经历」继续出种子**。最强立论：安静优先，少一轮回顾。否决原因：
  不确定就当成普通，正是自反馈回路的来源；宁可本轮不给种子（其它候选仍在），也不冒用回顾成果。

## Consequences

* 收益：回顾写出来的经历仍然进 Memory、仍可读、回顾作品 A/R 仍是普通可续写作品；但自动回顾
  种子不会再由它自己派生。普通确证结果、世界/身体结果、以及没有 artifact 来源的经历仍可成为
  种子；手动创建回顾、已有作品续写与其它个人机会完全不变。
* 代价：每轮自动供给多一次有界 owner 分类读（每个不同候选来源至多一次，候选池上界
  `max_experiences=4`）。没有新增持久缓存，也没有新增第二个索引。
* 边界：只解决「回顾成果作为自动回顾种子」这一条供给边。它不是「同一根事件只准入一次」，不是
  「禁止反思/禁止派生」，也不代表 M0 已经会拒绝派生正文。真模型的自然反思是否因此改变未被证明；
  证据只到「可用供给」这一层。
* 离线证据：`scripts/review_feedback_boundary_check.py check --offline` 现在会先真实跑
  `tests/test_review_feedback_boundary.py` 的 11 个用例（`discover` 只圈这一个文件，runner
  trace 收进 `StringIO` 不打印），再走真实 Supply/M0/正式 assembly 链：正式 CONTINUE 真写
  下一版、E3 必须精确对上本次回执的 owner 版本；真实 `review_seed`/`build_options` 不把
  E2/E3 当种子，原普通经历 E 仍可作种子，E2/E3 仍 M0 可读。报告只打印固定用例名与
  PASS/FAIL，不打印 detail。
* 历史偏差（照实保留，不作父级证据）：首个 worker 除本单约定外，还额外跑了旧 38 例与标题
  边界脚本，并在当次自测中出现过两次首轮红；这些是它自己的验证记录，父级既未复跑也未采作
  验收证据。
* E3 版本归一（2026-11-03 定点修复，仅改本 driver）：第一次固定入口实跑 30 checks 中前三红
  （E3 存在、E3 精确版本、E2/E3 两结果）根因是 driver 的 `bare_version` 只剥一层前缀。真实
  数据里 Memory 经历的 `version_ref` 是 `artifactversion:artifact-version:<id>`（consumer 在
  owner `version_id` 上又加了 `artifactversion:`），而 step receipt 的 `version_id` 是
  `artifact-version:<id>`；单次剥离后两边不相等，E3 因而匹配不到。改为循环剥离全部已知前缀后
  两边归一为同一 `<id>`（E2=`474bc6d9…` v2、E3=`812bbc88…` v3、A=`d58b1a33…`），30 checks
  全 PASS、exit 0。**未改任何业务实现、未削 E3 正控、未改 Memory/AG-2/AR-1/准入，也没有用
  ledger.release/删账本/手造记录求绿。**
* 父级最终独立验收：固定 `scripts/review_feedback_boundary_check.py check --offline` 实际 exit0，30 checks 全 PASS，内部新单元 11 例、0 skip；新根 `/tmp/rf-_c4ltat4` 保留。真实 Supply/M0/正式 assembly 下，回顾 A 正式 CONTINUE 写下一版，E3 精确匹配该次回执版本；E2/E3 两条经历各自可读，二者都不进入自动种子，普通经历 E 仍生成 review 候选。stderr 为空。前次父级 E3 配对三红、空交付及越界改任务书的历史保留，不以最终绿倒写；未跑真实模型、跨进程或长窗，不声称自然反思行为已改变。
* 与既有决定的关系：回顾/做法的 owner 与 marker 仍归
  [个人实践工作台](2026-11-07-v04-personal-practice-workspace.md)；回顾作为普通作品、经历作为
  Memory 记录的边界不变，见
  [确证经历→回顾→做法](../architecture/2026-11-12-v04-practice-life-review-and-method.md)；
  同标题去重与标题截断的既有结论不重开，见
  [标题边界离线实测](../testing/2026-10-31-review-title-boundary-offline.md)；M0 同结果去重与
  异版本保留不在此改动，见
  [S05 记忆可靠性](2026-10-27-s05-memory-reliability-review.md)。
