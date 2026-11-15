# 0.7 第四阶段：默认 practice 接线、真实经历回顾与有范围做法/试用

Status: implemented

## Problem

B 的实践工作台与 A 的 Memory 在 v0.4 已有完整接缝，但本树第二条日常链一处都没默认接上：
`scripts/v07_daily_life_demo.py` 手工把 `cycle.practice` 设成 `PracticeSeam(None)`，还手工往 env
补 `NYAIRO_LIFE_SUBJECT` 才让项目 owner 找到 subject；实例自己的 prepare profile 只写
`NYA02_SUBJECT` / `CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT`，而 `private_project_service.ENV_SUBJECT_NAMES`
与 `practice_from_env` 都不认这两个名字。另有三个具体断点：

1. `AutonomousLifeCycle._review_operation_key` 算完 digest 没有 `return`，回顾创建的幂等键恒为
   `None`（同一次 UNKNOWN 重放可以多建一份回顾）。
2. 回顾/做法、Memory 与真实 handoff 虽已存在，但没有一条同实例链证明“真实确证经历 → 正常候选
   生成回顾 → owner 写 marker=review → 自身写作结果不进自动种子 → 正式 Decision 提案 TENTATIVE
   → 两次有证据 trial → 无证据拒绝”。
3. practice 打开后，一轮真实供给（世界 12 + 普通作品续写/收尾 + 一个回顾选项……）可以超过 AG-1
   的 16 条硬上限，直接 `DECISION_ATTEMPT_FAILED:CANDIDATE_EXPLOSION`，整个决策轮失败。

## Decision

1. **默认 subject 接线**：`one_day_life` 的 prepare profile 增写已有的权威别名
   `CHIYO_LIFE_SUPPLY_SUBJECT = subject`。它是 `OWNED_ENV_NAMES`，宿主同名导出会先被清掉，落盘的
   值只能是本实例真实 subject；不硬编码 `chiyo`。demo 删除手工 `NYAIRO_LIFE_SUBJECT`，删除
   `cycle.practice = PracticeSeam(None)`，practice/review 走默认装配（同一 Supply owner、同一
   Memory）。
2. **漏接修复**：`_review_operation_key` 补 `return "review:<sha24>"`，与 `_research_operation_key`
   同形；回顾创建恢复按“选项 + 经历集”幂等。
3. **同实例主链**（demo 第 13 段）：真实阅读批注 / 作品 v2/v3 的 AR-0 版本经正式 AG-2 handoff
   进入本实例 Memory（至少 2 条可读 lexp）→ 正常 `popt:` 候选里选中回顾 → `create_review` 建真正
   artifact，owner 读回 `nyairo-practice:v1 kind=review` 与源 lexp 引用 → 自身回顾写作结果被
   `review_result_artifact_ids` 排除，普通经历仍重新长出回顾选项 → 正式 Decision 的
   `method_proposal` 由 owner 存 TENTATIVE → 本轮 offered 的 `method:<sha16>` 可见 →
   `HELPED` → TRIAL、另一条真实经历 `NOT_HELPED` → CONTESTED → 无可读证据的 `HELPED` 被既有
   证据门拒绝且方法 head/状态/trial 数不变。
4. **候选上限复用既有预算**：生产 runtime 与 isolated harness 建 `CandidateMaterializer` 时把既有的
   `global_cap` 绑到 AG-1 自己的 `MAX_COGNITIVE_CANDIDATES`（16）。它是 AG-0 本来的“资源上限、
   非价值排序”（按 candidate_id 确定性截断），不是新优先级；用新 cap 常量或放宽 AG-1 都不做。
5. 回顾仍是普通可续写作品；方法文档继续由 `_non_work_artifact_ids` 退出续写/收尾候选；没有新增
   owner、schema、Memory 准入、权限、模型参数或预算。

## Alternatives considered

* **在 demo/单测里继续关 practice，只在别处证明默认接线。** 最强理由：立刻不撞 16 条上限，改动最小。
  否决原因：本阶段要证明的正是“默认装配在真实同实例上接好了”；关掉 seam 再声称默认接好，等于把
  被测对象换成夹具。单测只在“默认解析”与“回顾种子”两个纯边界上另立用例，不替代主链。
* **给 artifact source 新增 cap，或超过预算时删掉 closeout / 普通作品续写 / 回顾选项。** 最强理由：
  直接减少候选、实现简单。否决原因：closeout 与普通续写同为合法动作，二者并存是
  `test_life_closeout_cycle_v04` 已钉住的契约；删普通作品/回顾又违反“保留普通 artifact 续写与
  普通经历种子”。用既有全局 cap 才不会改变“谁被 offer”的语义，只做确定性有界。
* **放宽 AG-1 的 `MAX_COGNITIVE_CANDIDATES` 到 17+。** 最强理由：一行解决。否决原因：16 是既有的
  认知请求硬边界（Section 139），属于承重墙；放宽等于让每轮提示词无界增长。
* **让 C 自己按 marker 拼回顾/方法，或直接把回顾写进 Memory。** 参见
  [v0.4 实践接缝](../architecture/2026-11-12-v04-practice-life-review-and-method.md) 的否决理由：那会变成第二份
  marker/第二个库，并让派生文字自我晋升为准入经历。

## Consequences

* 现在一条同实例链可以真实看到：默认 env → practice/Memory `READY/BOUND` → 至少 2 条确证 lexp →
  回顾 artifact（marker kind=review、源 lexp 引用）→ 自身结果被自动种子排除 → 做法 TENTATIVE →
  本轮句柄 → HELPED/TRIAL → NOT_HELPED/CONTESTED → 无证据拒绝且状态不变；每轮候选 ≤16。
* 证据边界：Decision、阅读响应、续写内容与评价意图都是脚本替身；真实的是 Supply/World owner、
  AG-0/AG-1/AG-2、AR-0、Memory、practice owner 与 handoff 校验。`HELPED/NOT_HELPED` 只证 owner
  状态链，不证做法客观有效；不证“自然形成的方法”、不证 G3、不做成长分/人格总脑。
  参考 [回顾反馈与供给边界](2026-11-03-review-feedback-supply-boundary.md)、
  [傍晚私人小册子](2026-11-09-v07-evening-private-booklet.md)。
* 两个 anchor（`autonomous_life_assembly.py`、`life_sharing_service.py`）未编辑；没有 live、
  没有产品模型请求、没有提交/发布；隔离根保留。
* 验证：`tests.test_v07_daily_life`（40 tests）与 `scripts/v07_daily_life_demo.py` 均退出 0；
  新增用例只覆盖 prepare 默认解析、回顾幂等键与回顾种子排除三条边界，主链由 demo 的
  check 逐段断言。
