# Agent Note: G2 历史影响选择的 A 路线离线准备驱动

Status: implemented

## Problem

G2 出口门要求「历史干预能影响实际选择」，而 v1 的 harness 与 v2 的自然观察控制器各自只覆盖一半：
`history_choice_experiment.py` 有影子两臂、发送前单变量门、20 对固定顺序与预登记统计，但它的采集
是「实验把主体叫醒答题」；`history_choice_natural_v2.py` 有只读观察与正式结构门，却不含两臂比较与
统计。本人已直接选定 A 路线（新隔离实例自然采集），B 受控造历史未选。G2 预登记因此需要一个**薄驱动**
把两者接起来，并把「离线准备到底做到了哪、哪层还没做」如实写清，而不是再写第三套控制器或声称
已经开跑。G1 仍 NOT_COVERED，本单只准备仪器。

## Decision

新增 `scripts/g2_next_offline.py`（唯一命令 `--offline-check`）、`tests/test_g2_next_offline.py`
与预登记 `docs/engineering/g2-next-prereg-20261031.md`。驱动只**读复用**两件既有仪器，不复制它们的
常量/落盘格式：

1. **A 控制器接缝**：直接跑 `history_choice_natural_v2.offline_live_seam_demo()`，核「同状态 QUIET
   不强问、纯读观察不产生新请求」与「H 成立后正式结构门对不成立场景具名拒绝」。本环境实测 outcome
   `SCENARIO_NOT_IDENTIFIABLE`、gate `SECOND_WORK_CANDIDATE_MISSING`，即门拒绝而非静默放行。
2. **影子构造/解析**：复用 `neutralise_history`、`prompt_identity`、`prompt_text_identity`、
   `make_history_recorder`、`PAIR_ORDER`。断言两臂只差历史 text、两臂均能过发送前门；改候选/裁历史/
   改 version_ref 都在发送前以 `SECOND_VARIABLE_DIFFERS` 拒绝；20 对顺序与预登记表逐位相等（10 AB /
   10 BA）。
3. **额度门**：复用 v1 `BatchLedger.assert_room` 验证阶段（第 41 次）与总量（第 61 次）发送前拒绝；
   复用 `arm_ceiling` + 真实 `LifeRequestBudget` 验证单臂压到 `起点+2`、第 3 次 `reserve` 被
   `REQUEST_BUDGET_RUN_WINDOW_LIMIT` 拒绝、退出后恢复总额度。
4. **统计**：用明确合成选择对喂 v1 `shadow_statistics`，核强正/强负（方向都报）、零差、边界
   `6/20` 检出（p=0.03125、差 0.30）与 `5/20` 未检出、19 对或带 stop_reason 一律
   `NOT_TESTED_INCOMPLETE`。
5. **真实入口关闭**：`prepare` / `run` / `execute-approved` 一律 `PRODUCT_RUN_NOT_AUTHORIZED`、零写盘；
   `refuse_existing_root` 让内部不接任意已有 root，scratch 只用新 `mkdtemp` 且保留。
6. **G2 的 52 实际硬限**（`g2_budget_gate_check`）：新建一本 `LifeRequestBudget`（`run_window_max=52`），
   所有发送都走正式 `BudgetedCognitionAdapter` → `reserve`。采集 8、影子 40、承接 4（两臂各 2）
   全部落在同一本持久账本：第 52 次允许，第 53 次在 `reserve` 处拒、内层 transport 零调用；
   阶段未用额度不可挪（采集只用 6 时整批最多 50）、每臂第 3 次拒、失败预留不退。v1 的 60
   （`budget_gate_check`）只留作兼容旁证，不改 v1 的 `STAGE_CAPS`。
7. **正式 runtime 两臂输入隔离**（`formal_input_isolation_check`）：新 mkdtemp 里 `prepare_scratch_instance`
   起真 owner，正式 `build_experiment_runtime` + 正式 `raw_cognition_adapter`；脚本 Decision/内容
   只用于把 H 建起来。两臂最终 payload/text 由真实 `build_prompt` 在 transport 边界截获，两臂
   只差历史 text（refs/候选/合法集/observed facts/输出 cap 一致）；被裁剪正文、改候选、改 version_ref、
   改 facts 都在内层 transport 之前以 `SECOND_VARIABLE_DIFFERS` 拒绝。
8. **两臂离线承接**（`continuation_isolation_check`）：两臂各自新 root（真 owner），各自
   Decision → Action、`owner_version_created=True` / `APPLIED`，作品集合不相交；共享一本 4 格
   持久账本，每臂 2、第 3 次在预留处拒。不走 v1 `clone_branch`，不访问任何已结束 root。
9. **统一 52 账本**（`budget_target_map_check`，`--section budget-target-map`）：一本
   `LifeRequestBudget(run_window_max=52)`。前 48 格是明确标注的机械计数 transport（全部走正式
   `BudgetedCognitionAdapter.reserve`；第 8 格故意失败，失败不退）；后 4 格是两臂真 owner/
   正式 runtime 的 Decision+content 实际预留。总账 48→50→52，第 53 次在内层 transport 之前拒，
   每臂第 3 次在同一次 `reserve` 处拒；用同一路径重开仍读到 52 并继续拒第 53 次。准备步骤
   （建 H/冻结）使用单列夹具账本并报告 setup 次数，不计入也不冒认这 52 格。
10. **同一冻结目标的两臂映射**：真 owner 建起 H 后，冻结出同一件作品 A/活动 actA/版本 R
    （H 的 owner 版本与冻结经历交叉核对一致）。影子两臂的脚本裁决记录为 CONTINUE + 目标 A；
    在 **A 任何写入之前**用现成 `clone_branch` 隔离复制出 B 起点（一致 sqlite 快照、路径 rebase、
    socket 重指、`branch_path_audit` 通过、删除底账按既有例外共享）。两臂各自经正式 Decision
    选 CONTINUE → 同 A 同 actA 的新 Action/owner 版本（`APPLIED`），作品身份相同、两臂新版本不同；
    错 A / 错 actA 在发送前拒、不 fallback。`clone_branch` 的现成 `runtime_env` 把分支身份硬绑在
    v1 `RUN_ID` 上，所以本单隔离 scratch 沿用该身份——门要求的正是它，不改 clone_branch、
    不改 v2 计划。场景仍 `SECOND_WORK_CANDIDATE_MISSING`，本项只验证目标执行接缝。

父级限定：正式输入截获所用 H 是脚本 Decision/内容经真 owner 产生并从 M0 读取的历史，不是真模型自然采集。
场景门仍是 `SECOND_WORK_CANDIDATE_MISSING`，故输入隔离只证明非可比场景上的接缝。
上一轮两臂执行各从新合成起点 START、其 4 格账本与 52 格计数检查分开；这两处已由第 9、10 点补上
（同一本 52 账本，同一冻结目标 A/actA/R 的两臂承接）。其余仍未闭合：可比较双作品场景、真实模型
自然采集/冻结/provider 采样、真实影子选择。驱动 ok 不表示 G2 已过。

## Alternatives considered

- **直接跑 v1 的 `offline-check` 再补一句话**：最省事，但 v1 的采集是实验 trigger、两轮零请求早停，
  正是 A 路线要删掉的东西；拿它当 G2 准备会回答错问题，否决。
- **让 v2 控制器自己长出影子/统计**：会把观察控制器变成第二个 harness，重复预算与统计逻辑，违背
  「复用已有仪器」，否决。
- **在驱动里真跑 20 对影子（建 runtime + 脚本 transport）**：能更接近端到端，但需要真实冻结场景；
  本次离线样本结构门给出 `SECOND_WORK_CANDIDATE_MISSING`，场景本不可比，强跑影子等于在不可识别场景
  上造样本，因此本次未运行完整比较。父级收窄：这不证明只能留给真实批解决；合法离线场景与承接映射
  仍是具名技术缺口，不能靠新预算替代。
- **把 G2 建议的承接 cap 4 写进 v1 的 `STAGE_CAPS`**：会悄悄改既有仪器常量；本单只报差异
  （v1 现为 8），未来批准时在 prepare 显式设置，否决现在改。
- **52 只做算术、发送负控继续用 v1 的 60**：最省事，但「第 61 次会拒」回答不了「第 53 次会不会拒」，
  52 也从未落到真实持久账本；现在改为新建一本 `run_window_max=52` 的账本、全部走正式 reserve，
  否决旧做法。
- **用自造 `_payload`/recorder 冒充正式 runtime 最终输入**：合成 JSON 只能证明指纹函数本身，
  证明不了真实 `build_prompt` 两臂只差历史文本；现在改为真 owner/正式 assembly 在 transport
  边界截获，合成检查仅保留为快速形状门，否决用合成输入冒完成。
- **照搬 v1 `run_continuation` 的 `clone_branch` 做承接**：上一轮因它会从主 root 整树复制、
  且本单禁止访问/clone 已结束 root，改为两个全新合成起点；本轮重新核可用性后采用它复制
  **本单自己**的隔离 scratch（不是已结束 root）：它提供一致 sqlite 快照、路径 rebase、socket
  重指与 `branch_path_audit`，是现成的隔离复制接口。裸 `cp -r` 仍否决。
- **让两臂各自 START 新作品冒充映射**：最省事，但回答不了「影子选择 → 同一冻结目标执行」，
  也证明不了同 A 同 actA；否决。
- **改 `clone_branch` 接受 v2 run 身份 / 改 v2 计划**：都会动到既有仪器或本单计划；改为让本单
  隔离 scratch 沿用 clone_branch 已要求的 v1 `RUN_ID`（门要求的正是该身份），否决改接口。
- **52 仍只做机械计数、承接另开一本 4 格账本**：上一轮做法，两个账本不贯通；本轮把 48 机械格
  与 4 格真 runtime 放到同一本持久账本，否决两本账。

## Consequences

- 收益：自然观察、输入隔离、合成统计、52 reserve 硬限、双根执行隔离，以及本轮的统一 52 账本
  （48 机械 + 4 真 runtime）与同一冻结目标的两臂映射都有可运行检查。真实入口与已有 root 都拒绝，
  避免误入产品运行或正式根。
- 代价/残留风险：`--offline-check` 变重（多起 3 个 scratch 实例，约一分钟）；`--section
  budget-target-map` 再起 1 个 setup 实例 + 1 个分支并跑两臂（约半分钟）。承接与隔离仍用脚本
  transport（owner/AG1/AG2/AR0/writer 是真的，裁决不是）。统计仍是合成选择对，不证明历史改变
  真实模型选择。`clone_branch` 要求 v1 `RUN_ID`，本单隔离 scratch 沿用该身份；这是接口前提，
  不改生产身份或默认。
- 未验证：真实模型会怎么选、8 次够不够、1800 秒窗口能否建起历史、观察间隔真实抖动、可比较双作品
  场景（离线仍 `SECOND_WORK_CANDIDATE_MISSING`）、真实影子选择到执行的承接。本驱动绿不当 G2
  通过，也不当 G1 进展。

相关：`2026-10-29-history-choice-harness`、`docs/engineering/history-choice-v2-proposal-20261029.md`、
`docs/engineering/g2-next-prereg-20261031.md`。
