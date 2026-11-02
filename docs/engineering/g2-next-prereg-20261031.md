# G2 历史影响选择：A 路线预登记（2026-10-31）

状态：**A 路线（新隔离实例自然采集）保留，B 受控造历史未选。本文件只做离线准备与预登记；
真实 prepare / run / execute-approved 一律关闭，需本人直接批准新预算与运行授权。**
预算 168/300、余 132 不动；本文建议的 52 是**待批建议**，不是额度、未支用。
G1 仍 NOT_COVERED；本文件只准备 G2 仪器，不宣称已进入 P2。组件（`components/`）未改。

离线驱动：`scripts/g2_next_offline.py --offline-check`；本次收尾另加定向入口
`--offline-check --section budget-target-map`（只跑统一 52 账本与冻结目标映射，不重跑旧 10 段；
未实现或意外失败会具名返回 `PARTIAL_STOP`/非零退出，不以 `ok=true` 掩盖）。
测试 `tests/test_g2_next_offline.py`；实现说明见
`.agents/notes/implemented/testing/2026-10-31-g2-next-offline.md`。

## 1. 什么是 H，以及不为它造历史

H 是机械锁定的一条：**首个 owner 确认 `version_created=True`、且其对应正式 M0 经历可读的作品
（含精确 artifact/version 证据）**；不事后挑「更有影响」的那条，不把 checkpoint / receipt 当 H。

自然采集失败是合法结果：不脚本 START/PAUSE/换事、不清 quiet、不造 OPEN、不写 head、不设 timer。
本方案**不保证 8 次请求够建起历史**；没有可用于估计该新场景成功概率的数据，也不声称「大概率失败」。

正式比较前，同一冻结场景必须同时满足：H 与另一件**不同作品**都真可 START（或 CONTINUE/RESUME），
且 `NO_ACTION` 合法。前台占用、挂起规则、终态任一使某件不合法，就停为
`SCENARIO_NOT_IDENTIFIABLE`，**不改门、不手改 Activity/资格、不人工解卡**。当前 G1 未过，
真实 G2 的依赖与授权仍然关闭。

## 2. 对照保留已选的 A

A 臂：经过现有治理读取的真实准入历史 text。B 臂：固定中性句子，按**等 Unicode 字符数、等换行位置**
替换 text。两臂只有历史 text 可变；refs / candidates / 合法集 / 身份 / 模型参数 / 时钟 / 预算提示 /
模板一律相同。发请求前比较最终渲染请求：历史 text 之外任何差异都拒绝发送（第二变量）。历史被裁剪/
丢掉即「干预未送达」，记失败、不补样。

限制照实：等字符**不等 token**；共同 refs 可能透露 H 关联；一般信息提示/显著性仍是替代解释。
影子只在正式 cognition 请求 + 正式校验边界取选择，**不提交 Decision、不采纳、不写主 Action/Activity/M0**；
「影子选了 H」不等于实际采纳。真实执行承接必须另有合法链、每臂隔离；隔离不成立就 `NOT_RUN`，
**不克隆已有 root、不绕拒绝**（与 v1 的承接实现分开，本驱动不运行它）。

## 3. 采用层次与统计不降格

分开报告、互不冒充：材料被构造 → 请求成功 → 合法 Decision（正式校验通过）→ H 选择差异 →
正式 Action / owner 效果。模型自述 reasoning 不算因果。

沿用既有 20 对固定顺序（下表，与本驱动 `PREREG_PAIR_ORDER` 一致）：

`BA AB AB AB AB BA AB BA AB AB BA BA BA AB AB BA BA AB BA BA`

只有**完整 40 次请求**且无干预/传输/供应商故障时，才按预登记判据：双侧精确 McNemar `p<0.05`
**且** `|A 选 H 比例 − B 选 H 比例| ≥ 0.30`（≥6/20，正负方向都报）判「检出」。未完整一律
`NOT_TESTED_INCOMPLETE`，不当「无效」也不当「未检出」。20 对共享同一段历史，**不是 20 段独立经历**；
不做功效保证、不推稳定偏好。不为了「够」用更便宜/更少的样本数。

## 4. 预算、观察与出口

预算**仅建议**：`52 = 自然采集 8 + 影子 40 + 最多承接 4 + 诊断 0`。失败计入所属阶段与共享硬 cap，
**不退款、不挪额、不补样、不做第二次 run**。history 168 最坏 220、余 80 只是当前建议口径；
未来若批准，必须在 prepare 时重核账本漂移。

离线已用**同一本新持久账本**走正式 `LifeRequestBudget.reserve`：8 + 40 + 4 共 52 次允许，
第 53 次在内层 transport 之前拒；阶段未用额度不可挪（采集只用 6 时整批最多到 50）、
每臂 2 且第 3 次在预留处拒、失败预留不退。v1 的 60 上限只留作兼容旁证，不当 G2 完成证据。
v1 阶段表把 continuation 记 8，G2 收紧到 4，属建议差异，不得直接复用 v1 的 8。

2026-10-31 收尾把**同一本 52 账本**真正接到两臂：前 48 格是明确标注的机械计数 transport
（第 8 格故意失败，证明失败不退），后 4 格是两臂真 owner/正式 runtime 的 Decision+content；
同一本持久账本 48→50→52，第 53 次在内层 transport 之前拒，每臂第 3 次在同一次 `reserve`
处拒。用同一路径重开 `LifeRequestBudget` 仍读到 52 并继续拒第 53 次。准备步骤（建 H/冻结）
用单列的夹具账本，报告 setup 次数，不计入也不冒认这 52 格。

观察窗口与请求数**分开**：窗口 1800 真实秒，最多 31 次纯读（开始一次，之后至多每 60 真实秒一次），
纯读不调模型、不提交 Decision、不刷新 Activity/资格。不快进、不为凑请求推进；业务时间只沿生产时钟。

明确出口（任一即停，不自动续跑）：无 H → `NO_EFFECTIVE_HISTORY`；有 H 但无可比场景 →
`SCENARIO_NOT_IDENTIFIABLE`；最终输入非单变量 → 发送前拒绝；治理扣留 / 裁剪 → 干预未送达；
传输/供应商失败、坏账、错身份、安全拒绝 → 停批并具名。所有失败占槽，不补。

新 root / run / domain 只**拟议、不创建**；不写可用真实 profile、不加载密钥。参数可沿原
`custom` / `v1` / `cn:deepseek-v4.1-flash`、temperature 0、stream=false、决定 cap 4096 / 内容 cap 2048；
实际构建不符须先停下更新预登记，不采集后临时改。预算未批不支用。

## 5. 本次离线实际做到 / 未做到

实际做到（0 产品、0 网络、0 正式根，scratch 全部新 mkdtemp 保留）：

- **A 控制器真实接缝**：同状态 QUIET 不强问、纯读观察不产生新请求；H 成立后正式结构门对不成立
  场景给出具名拒绝（离线样本为 `SECOND_WORK_CANDIDATE_MISSING`）。
- **v1 形状/统计层**：中性替换、指纹、20 对固定顺序；明确合成选择对的阈值与不完全出口。
- **G2 的 52 实际硬限**：一本新持久账本、正式 `BudgetedCognitionAdapter` 发送口，8 + 40 + 4
  全部经 `LifeRequestBudget.reserve`；第 52 次允许、第 53 次在内层 transport 之前拒；阶段未用
  不可挪、每臂 2 拒 3、失败占额不退。v1 的 60 只作兼容旁证。
- **正式 runtime 两臂输入隔离**：新 mkdtemp 里真 owner + 正式 assembly/cognition 构造；脚本
  Decision/内容把 H 建起来后，两臂最终 payload/text 由真实 `build_prompt` 在 transport 边界截获。
  两臂只差历史 text（refs/候选/合法集/observed facts/模型输出 cap 一致）；改候选、裁正文、
  改 version_ref、改 facts 都在**内层发送前**以 `SECOND_VARIABLE_DIFFERS` 拒绝。transport 离线，
  全程 `http.client.HTTPConnection.connect` 硬断。
- **同一冻结目标的两臂映射**（2026-10-31 收尾）：同一件本单新建、真 owner 确证的冻结作品 A、
  活动 actA、版本 R（H 的 owner 版本与冻结经历交叉核对一致）。两臂从 **A 任何写入之前**的同一
  冻结起点隔离复制（现成 `clone_branch`：一致 sqlite 快照、路径 rebase、socket 重指、
  `branch_path_audit` 通过、删除底账按既有例外共享），各自经正式 Decision 选 CONTINUE →
  同 A 同 actA 的新 Action/owner 版本（`APPLIED`），两臂沿用同一作品身份，各自的新版本不同。脚本 Decision
  只是离线裁决替身；影子两臂的选择记录（CONTINUE + 候选目标 A）与执行目标有明确连接，
  错 A / 错 actA 在发送前拒、不 fallback。`clone_branch` 的现成 `runtime_env` 把分支身份硬绑在
  v1 的 `RUN_ID` 上，因此本单隔离 scratch 沿用该身份（不改 clone_branch、不改 v2 计划）。
  场景仍 `SECOND_WORK_CANDIDATE_MISSING`（第二件作品不可执行），本项只验证目标执行接缝。
- **真实入口关闭**：`prepare` / `run` / `execute-approved` 一律拒绝且不写盘；内部不接任意已有 root。

未闭合：可比较双作品场景（仍 `SECOND_WORK_CANDIDATE_MISSING`/`SCENARIO_NOT_IDENTIFIABLE`）、
真实模型自然采集/冻结/provider 采样；本轮只把同一冻结目标的执行接缝与统一 52 账本接通，不宣称
完整 G2 比较或 G2 通过。这里的非空历史由脚本 Decision/内容经真 owner 产生并从 M0 读取，
**不是真模型自然历史**。不得强造合法集或资格，但也没有证据证明技术接线只能靠付费运行解决。
父级上一轮独立运行原 `--offline-check`（10 段）；本轮只独立运行约定的
`--offline-check --section budget-target-map`，实际 exit 0、budget_unified/frozen_target_mapping 均 ok。
worker 额外复跑原 10 段不作父级新证据，也不冒本单约定范围。这些是分段机制检查，不是完整 G2
实验准备验收。保留现有成果与具名技术停点，不以“再批准一批”替代缺口处理；真实入口仍关闭。
