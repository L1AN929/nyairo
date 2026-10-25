# Agent Note: 一个 wake 内复用同一次 owner 读，不跨回合缓存旧许可

Status: implemented

## Problem

已结束的 fastday-e 留下的是 **17 条唯一 wake 记录、10 次认知尝试、34 个唯一候选**
（`autonomous_wakes.jsonl`、`resident_journal` 的 `cognition_diagnostic`、
`candidate_audit_journal.jsonl`；`resident_summary.counters.wakes=327` 是累计读面，
不是唯一 wake 数）。统计只能证明：63 条候选审计、34 个不同 candidate id 被 materialize、
0 次同一 candidate id 重复 materialize、1 次同 `source_revision` 再次出现。
**同一语义候选跨认知轮再次被呈现是正常可能**，这些计数不能推出“语义层面没有重复候选”，
也不能推出“候选太多导致不行动”。那次 `NO_ACTION` 是模型在 5 个合法候选里按
`COGNITIVE_REQUIRED` 自己选的，不是本地规则空集。

同一个 wake 内，Life 侧对同一个 ManagedArtifact owner 反复读取同一时刻的同一份视图：
`materials()` 的 source availability + 续作点、`evaluate_once` 的 `_read_resume_point`、
`ArtifactContinuationSource.refresh` 逐个候选复查、`build_candidate_set` 又 materialize
同一来源、阅读情境再读一次。每次都是真实 socket 往返；`LifeArtifactPort.call_count`
能把它们数出来。这不是候选语义重复，而是**同回合重复取数**。

## Decision

- 新增 `components/life/scripts/wake_candidate_cost.py`：
  `RoundCachedArtifactPort` 继承真实 `LifeArtifactPort`（`PrivateDailyItems` 的
  `isinstance` 检查必须继续成立），只覆盖传输钩子 `_call`。缓存键是**完整请求**
  （op + artifact id + 版本/分页参数），不同 artifact、不同来源、不同约束不会合并。
- **装配**：`AutonomousLifeCycle` 把 `self.artifact_port`（已包装的回合 port）交给
  `ArtifactContinuationSource`，所以 AG-0 的 refresh/materialize 与 cycle 自己的读共用
  同一份回合快照；此前 source 拿的是裸 port，候选构建实际没有被 memo 覆盖。
- 缓存只在**一个回合内**有效：`evaluate_once` 本身用 `try/finally` 包住整轮，
  `begin_round_reads(obs)` 在开头、`end_round_reads()` 在 `_finish` 和异常/早退路径
  都会清空。直接调用 `evaluate_once` 的探针/工具不依赖 runner 的 `finally`。
  每次 begin 都清空，即使两个 wake 恰好同一秒也不复用上一回合的答案。
- 任何 command op（create / commit / archive / resolve）在调用前后都清空缓存；
  `lookup_operation` / `read_effect_receipt` 这类 UNKNOWN 效果解析**不缓存**，每次仍问 owner。
  失败不缓存：owner 拒绝或传输错误下一次仍然重新问。
- **use-time 复查先失效再读**：
  - `private_material_situation(refresh=True)`、阅读响应写手返回后的
    `_recheck_reading_sources` 读两个 owner 之前；
  - **预采纳新鲜度检查之前**：AG-2 的 `pre_adoption_mutation_hook` 就是
    “Decision 已提交、采纳之前”的边界，生产接线 `_pause_gate_hook` 在这里
    `invalidate()`；紧接着的 `validate_decision_for_adoption → validate_candidate →
    ArtifactContinuationSource.refresh` 因此一定重新问 owner，archive / tombstone /
    新 head 会被观察到并阻止采纳，而不是被 Decision 前的快照掩盖。
- 候选、Decision 合法矩阵、AG-1 路由、预算、World/Body/Memory 与 owner 契约一字未改；
  没有删除候选、没有新增默认 topK、没有强制活动。

## Alternatives considered

- **跨 wake 复用 owner 读**：省得更多，但会把旧权限/旧窗口/旧 World 状态冻结到下一个
  wake；与“权限/窗口/World/body 必须再验”冲突。否决。
- **只缓存确实不可变的 `read_version`/`list_versions`，不缓存 `read_artifact`/`list`**：
  最保守，但真实重复全在 `list_artifacts` + `resume_point`（同一个 `read_artifact`），
  这样等于不优化。改为“回合快照 + 显式复查点失效”，把安全责任放在 use-time 检查上。否决。
- **让 AG-0 source 继续用裸 port，只 memo cycle 自己的读**：资格读最保守，但候选构建的
  重复读全部留下，且“正式资格复查”与 memo 无关，等于本单没有验证对象。否决。
- **在 materialize 后、Decision 前再插一次全量复查**：多一次 owner 往返，且 Decision
  之前没有“许可能力”可发；真正的许可点是采纳。否决。
- **按候选数量裁剪 / 默认 topK / 删掉长期候选**：审计没有重复语义候选，也没有评分或
  阶段时长证据；`NO_ACTION` 是合法选择。裁掉合法选择就是在制造行为。否决。

## Consequences

- 隔离真 Supply owner + 真 `LifeArtifactPort` + 真 `ArtifactContinuationSource` +
  真 `CandidateMaterializer` 的代表性一轮读序列：owner 调用 **11 → 3**，8 次命中，
  避免的响应字节估计约 **7.8 KB**（随版本 id 长度略有浮动），候选 refs 与续作 head
  完全相同。
- 生产装配（真 `AutonomousLifeCycle` + 真 `runtime.candidate_read`）同一轮
  `materials()` + AG-0 `build_candidate_set`：**3 次 owner 调用、6 次命中、约 5.7 KB**；
  `artifact_source.port is cycle.artifact_port` 已断言，候选 refs 与 baseline 一致，
  0 次认知调用。
- **外部变更实证**（真实 owner，独立 port/operator，不是手动 `invalidate`）：
  - 回合内先缓存 head/list，独立 port 真实 `archive_artifact`；预采纳边界后
    `validate_candidate` 返回 `valid=false / ARTIFACT_NOT_CONTINUABLE`，lifecycle 已是
    ARCHIVED，校验本身没有创建版本。
  - 独立 port 真实 `commit_version` 新版本；复查返回 `valid=false /
    ARTIFACT_HEAD_ADVANCED`，head 版本号是操作者的 2，校验没有追加版本。
  - 真实 `tombstone`：边界后读 owner 得到 `NOT_FOUND`，旧正文不再被返回。
  - 真实 `revoke_grant`：回合内已缓存的读仍然存在，但 command 不缓存、owner 直接
    `REVOKED` 拒绝写入，head 未变；没有用缓存读来“认许可”。
  - 阅读写手（模拟）返回后外部 commit：生产 `_recheck_reading_sources` 失效后重读，
    发现 material head 变了并抛 `ReadingResponseFailed`，private-items head 未变，
    拒绝信息里没有正文。
- **freshness 边界（准确说明，不承诺无限即时撤权）**：
  - 一个 wake 是**一个时点的读**，不是带完整 revision 协议的不可变快照。第一次读与
    Decision 之间发生的 archive/tombstone/commit 仍可能出现在本回合的候选 **ref/枚举**
    快照里；候选描述只含 kind/source_kind/source_ref，**不含正文**，所以决策提示不会
    展示被撤权正文。
  - 唯一把正文送给模型的是阅读步骤：它先 `refresh=True` 失效再读，模型返回后
    `_recheck_reading_sources` 再失效复查；两次读之间（发出请求前）的竞态仍属于既有
    TOCTOU，不由本缓存消除。
  - 明确承诺的是：**预采纳资格复查**与**阅读 pre-commit 复查**一定观察 owner 最新状态；
    “读后到 Decision 之间”的变化只保证不授权写入，不保证决策上下文即时刷新。
- `scripts/wake_behavior_stats.py` 是只读白名单统计：wake/Decision/候选/Action/World/
  移动时间分域报告；`RULE_RESOLVED` 记 0 次模型、账本只认
  `life_request_budget.json`；缺结束或时钟的 travel 不给 0 秒，只给
  `PARTIAL/UNKNOWN` 与已知部分；“总活动占比”固定 `NOT_MEASURABLE`。
- 未验证自然行为、真实模型采用、G1 或 24h；本次 smoke 0 产品调用。缓存没有把
  e 的历史数据改写成“更省”；它只对未来的 wake 生效。
