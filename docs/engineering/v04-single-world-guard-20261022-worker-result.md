# 单 World 分支候选匹配 · 最小修复（worker 结果）

日期：2026-10-22。结论：只有 World 一条路径可走时，非 World 候选现在会在任何 World Activity
形状或 step 被构建之前，以既有拒绝文本诚实失败；合法 World 候选（含合法 extras）仍按原五类
前缀承接。这是防误送，不保证私人机会本身有可继续路线，也不证明历史故障根因已全部定位。

## 1. 选定窄接缝与改动

写集内两个业务改动，均在 `components/life/scripts/autonomous_life_cycle.py`：

- 新增 `_require_world_candidate(decision_record)`：复用 `_selected_candidate` 与既有
  `_candidate_is_world`（place/object/stay/bodyact/dailyact 五类 target 前缀）。非 World 或
  候选读取失败时抛既有 `AutonomousLifeError`，文本与 `_world_step_spec` 当前拒绝一致：
  `the committed Decision %r names no observed World target`。不新增错误码/持久字段，
  不重新选候选、不重试、不写回原 Decision。
- 新增 `_world_step_for_decision` 与 `_world_adoption_for_decision`，并在**只有 World 一条
  路径**时的两个分派点使用：`world_step = _DecisionBoundStepSpec(self._world_step_for_decision)`
  与仅 WORLD 可用时的 adoption lambda。多路径 `_routed_step_builder` / `_RoutedAdoptionSpec`
  未改；它们本来就在 `_candidate_is_world` 为真后才调用 World builder，因此不会落到
  `_work_step_spec_for_decision` 的私人内容 fallback。

修复前离线实测（仅 WORLD 路径 + 私人候选）：World adoption builder 与 World step builder
各被调用一次，随后才拒绝。修复后两处均为 0 次。

## 2. 验收命令与实际结果

均在 WSL `/mnt/d/nyairo-rc1-body-backend`，`PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B`：

| 命令 | 结果 | 证据 |
| --- | --- | --- |
| `-m unittest -v tests.test_single_world_candidate_guard` | 6 例 OK，exit0 | `.agents/worker-runs/20261022-single-world-guard-new-tests.log` |
| `-m unittest -v tests.test_world_daily_opportunities tests.test_world_daily_life_consume tests.test_world_body_life` | 43 例 OK，exit0（复跑两次连续绿） | `.agents/worker-runs/20261022-single-world-guard-world-regression.log` |
| `npx tsx .../verify-agent-note-tree.ts` | `ok: 86 note(s) tree and relative links verified`，exit0 | 会话输出 |
| `npx tsx .../verify-agent-note-format.ts` | `ok: 86 note(s) verified`，exit0 | 会话输出 |
| `npx tsx .../verify-archived-agent-notes.ts` | `ok: 0 archived note(s) verified, 0 seal(s) in manifest`，exit0 | 会话输出 |
| `git diff --check`（写集 tracked 两文件） | exit0；新 untracked 文件另查尾随空白/制表符，无命中 | 会话输出 |
| `git diff --stat` | `autonomous_life_cycle.py` +35/−3；既有 feature 笔记 +3 | 会话输出 |

备注：首次整组 43 例出现过一次红：
`test_world_daily_life_consume.DailyWorldConsumeTest.test_a_consume_decision_really_uses_one_instance_through_the_owner_gateway`
报 `NO_ACTION`、`ABANDON_NONTERMINAL_CHOSEN`、`no legal candidate`，`step_path` 为多路径
`AR0_READING_CLOSEOUT_WORLD_ROUTED`，失败在 AG1 决策阶段，未进入本工单修改的单 WORLD builder。
随后：当前版本单用例复跑 OK；用临时目录加载 HEAD 版单文件隔离对照，同一用例 OK；整组再跑两次
均 43 例 OK。未复现，未归因到本修改，但保留这次瞬时红记录，不称“从未红过”。

「86 note(s)」= 原有 85 篇 + 本次新增 1 篇；archive 0，不是空目录误验。

## 3. 正负控（`tests/test_single_world_candidate_guard.py`）

- **A 负控**：正式 `cycle.evaluate_once` 的唯一 WORLD 分派场景（无前台、无可续作品/阅读/收尾/
  项目路径），脚本替身 Decision 为 START 并选私人候选。断言 `OUTCOME_DECISION_FAILED`、
  detail 含既有拒绝文本；World step、World adoption、作品 step、阅读 step、项目 step 五个
  builder 均 0 调用；coordinator `built` 为空、port 无提交；Decision 只被询问 1 次且
  `decision_id` / `selected_candidate_ref` 原样不变。
- **A2/C 负控**：候选读取抛 `RuntimeError` 时诚实拒绝，World builder 仍 0 调用，不拿观察到的
  第一个世界目标兜底；`last_world_error` 保留读取失败类型。
- **B 正控**：同场景合法 World 候选，World step 与 adoption 各 1 次；step target 为
  `place:park`、verb 为 `MOVE`；adoption 为 `GO_TO_OBSERVED_PLACE` 且 target_refs 含
  `world_fact:place:park`。不是只测谓词 true。
- **B/C**：私人候选记录带合法 World extras（`artifact:...` + `place:park`）时，沿用原谓词进入
  World step，证明没有按 `candidate_kind` 误挡。
- **C**：五类前缀参数化均判 World；纯 artifact、纯 `dopp:`、空 refs、非 Mapping 均不判 World。
- **D**：多路径（可续作品 + WORLD）选合法 World 候选，`step_path` 仍为
  `AR0_WORLD_ROUTED`，World step 正常构建，证明新门没有把合法 World 动作全部挡掉。
- 测试不启动真实模型、不连 socket；只复用既有 Stage A 替身与临时目录。

## 4. 笔记

- 新建 `.agents/notes/implemented/bug-fix/2026-10-22-single-world-candidate-guard.md`，
  记录 Problem / Decision / Alternatives considered / Consequences。
- 既有 `.agents/notes/implemented/feature/2026-10-17-world-daily-life-opportunity-routing.md`
  只在 Consequences 末尾加一条事实同步/互链，原命名空间分流决定未改。
- 检索未发现同一决定归属的既有 bug-fix 笔记。

## 5. 未验证与边界

- 未调用产品模型，未启动/修改真实主实例，未跑浏览器、24h、全库或已绿旧套件。
- 历史 START 的完整 target_refs/step_path 未知；本修复不证明当时模型选择一定正确，也不证明
  该次真实故障的全部因果已定位。只证明“仅 WORLD 路径时非 World 候选会在 World builder 前被拒”。
- 私人机会为何在只有 WORLD 路径的轮次出现、是否有合法承接路线，未诊断，不在本工单范围。
- 合法 World 候选会多一次候选读取；两次读取之间若候选变化，后一次仍会诚实拒绝。
- Memory UNBOUND、understanding UNKNOWN、EP-09 恢复 FAIL 等既有边界不变。
- 写集外未改；既有 dirty 全部保留，未 stage/commit/push/deploy。
