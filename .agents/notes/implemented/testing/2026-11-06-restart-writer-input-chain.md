# Agent Note: 同作品跨重启续作的正式 writer 输入链可证明范围

Status: implemented

## Problem

G1/H 的真实批已正常结束并记 `NOT_COVERED`，旧根全部关闭。此时「下一版正文从哪里来」这件事
仍缺一条**可复核的输入链证据**：P1 正式写出作品并正规关停后，P2 在新 PID 重新装配，正式
`CONTINUE` 的 `ModelContinuationWriter` → `AgencyModelClient` → transport 实际收到的
当前版本/正文、材料来源、上一版摘录，是否真的是 owner 当前状态，而不是首版残留、另一件作品，
或测试自己拼出来的 prompt。此前只有 [[2026-11-06-decision-nochange-projection]] 证明
`CONTINUE` + `no_change` 的读面，以及
[[2026-10-28-v04-work-revisit-history-and-sourced-new-work]] 证明 `continuation_excerpt`
单独可读；没有一条把「跨 PID + 正式决定 + 实际请求体 + owner 确证」接在一起的验证。

## Decision

只新增 `tests/test_restart_writer_input_chain.py`，**零生产改动**。现有正式链在四个聚合
case 上都按契约工作，未确证接线缺陷，因此本单不造补丁、不改提示词、不新增落盘协议。

- 真实件：真实 Supply owner（AF_UNIX）、`ProductionLifeRuntime`、AG-0/AG-1/AG-2/AR-0、
  `ModelContinuationWriter` + `AgencyModelClient` + 脚本 transport。Decision 与内容回答
  是脚本替身并在代码/注释里明示，不调用真实 provider。
- Case 1：P1 在独立 OS 进程用正式 `START`（v1 研究种子 + v2 模型首段）与 `CONTINUE`
  `revise_tail`（v3）写出同作品，正规 `close`；P2 在 unittest 进程重新装配同一 scratch 根。
  暂停窗口 0 内容/决策请求；解除后脚本 `CONTINUE` 的真实请求体与 owner 逐项对齐：
  `current_version_id`/`current_version_number` 是 owner 当前 head，`tail_of_current_text`
  以 v3 正文结尾且不含只留在 v2 里的 V1 哨兵，`research_context` 的 `artifact_id`/`source_refs`
  指向该作品与 owner 材料，`earlier_versions[0]` 的 id/版本号是 v2 且其 600 字摘录等于
  owner `read_version(v2)` 的前缀，错误作品诱饵不出现。P2 再写出一版，owner 读回同内容。
- Case 2：正式 `CONTINUE` + writer 合法 `no_change` 保留已提交 Decision 身份，
  `owner_called/version_created=false`，owner 版本/Action/receipt/M0 不增，不当成模型
  `NO_ACTION`，`self_note` 不在 wake/journal/decision store/memory 里；随后合法新内容用
  正式 CAS 写出同 A/actA 的新 Action 与新版本，owner 读回相同字节。
- Case 3：内容等待边界由现有 owner 接口的另一 writer 推进 head；旧 `expected_head` 被
  owner CAS 拒（`stale expected_head` 收据），不覆盖新 head、只发一次内容请求、变化仍绑在
  同一作品/Activity。
- Case 4：writer 实参保留产品值 `max_new_chars=300` / `max_prompt_chars=4000` /
  `max_output_tokens=512` / `timeout_s=20.0`，transport 实收同一组值；`self_note` 不落盘。

唯一验收：

```
MSYS_NO_PATHCONV=1 wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && \
  /tmp/rc104-venv/bin/python -B -m unittest tests.test_restart_writer_input_chain'
```

结果 `Ran 4 tests ... OK`、`EXIT=0`。开发中曾有两次红：一次是 seed 轮本身也走 writer
导致的 prompt 计数差，一次是把 v2 的 600 字摘录误当成应包含尾部哨兵；两处都是测试假设
错误，修正后未改产品代码。

## Alternatives considered

- **新增 `self_note` 落盘来找"为什么 no_change"**：最强理由是读面能直接看到模型自述；
  否决原因是自由文本既是隐私又不是事实，且现有契约本来就不持久化它。
- **直接再跑一批真实模型**：最强理由是用真实样本代替推理；否决原因是输入链未核实，
  更多样本不能证明接线，只会把环境噪声混进判断。
- **只调 `_next_content` 或手拼 prompt 断言**：最强理由是快；否决原因是那样绕过了正式
  AG-1/AG-2/AR-0 与真实 owner，测不到"实际发出的是什么"。
- **P1/P2 都在测试进程里跑**：最强理由是简单；否决原因是没有任何跨 OS PID 证据，
  重启后的重新装配只是同一进程内的对象复用。
- **为 CAS 拒绝新增全局事务/权限，或在测试里自动重试**：最强理由是让写更"原子"；
  否决原因是现有 owner CAS 已经给出正确拒绝，加事务会改承重墙，自动重试会掩盖缺陷。

## Consequences

- 可证明：跨两个真实 PID / 同 scratch 根 / 同作品 / 同 Activity 的正式 `CONTINUE` 请求
  携带 owner 当前 head 的版本 id、版本号与正文；研究材料来自 owner 的材料 id/版本；
  上一版摘录是 owner `read_version` 的真实前缀；合法 `no_change` 是零写且保留决定身份；
  内容等待期间 head 变化由 owner CAS 拒绝，不覆盖、不裸重发；writer 的 cap 仍是产品值。
- 不可证明 / 不宣称：真实模型自己的选择与写作质量（本单产品调用 0，累计仍 539/1000）；
  G1 EL4 或真实链后半段通过；H/G 旧实例、自然 24h、崩溃矩阵。此夹具里
  `practice_context`/`work_context` 未出现，测试只校验"若出现则为 dict"，不伪造；
  `research_context` 出现且被按 owner id 校验。
- 本单没有新增任何 sink；`self_note` 已核不在 life 侧落盘读面。既有 AR-0 对 step payload
  的持久化边界不在本单审计范围，未据此下结论。
- 相关基线：[[2026-10-25-v04-private-practice-cycle-writer-no-change]]（作品 `no_change`
  语义）、[[2026-11-06-decision-nochange-projection]]（已提交决定与 no-op 同读面）。
