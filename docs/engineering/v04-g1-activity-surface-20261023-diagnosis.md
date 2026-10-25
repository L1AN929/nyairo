# v0.4 G1 状态面矛盾诊断（2026-10-23）

范围：只读诊断 `one_day_life status` 报 `IDLE / no open Activity`，而下一真实 run 仍加载
`actv_91acbd742551228b` 作为前台 Activity。本单 0 产品调用、未启动服务/resident、未写实例，
未改业务代码/测试/notes/NOW/current。

实例：`/root/nyairo-day-20261022`，state root `/root/nyairo-day-20261022/one-day/life/state`，
`run:one-day`。

## 活动权威与矛盾

**权威位置**：Activity 的权威是 `canonical_life_state.json`（`activity_continuity.CanonicalActivityStore`
的落盘），不是 `resident_summary.json`。

本次只读取值（`python3` 直接 `json.load`）：

- `canonical_life_state.json`：`revision=15`，`foreground_activity_ref=actv_91acbd742551228b`；
  该活动 `status=PAUSED`、`kind=GO_TO_OBSERVED_PLACE`、`occurred_at=2026-10-22T22:11:02+08:00`。
- `life_transitions.jsonl`（15 行）中该活动两条转换：
  `IDLE→ACTIVE @22:11:02+08 reason=ADOPTED_DECISION`、
  `ACTIVE→PAUSED @23:49:34+08 reason=VOLUNTARY_PAUSE`。
- `resident_summary.json`：`running=false`、`ticks=334`、`last_tick_at=2026-10-22T15:51:34+00:00`，
  顶层 key 中**没有 `last_tick`**，只有 `last_tick_at`。

**误报链（当前代码，行号为本次所读事实）**：

1. `components/life/scripts/one_day_life.py:1115` `classify_state(summary, pause_view)` 是唯一实现；
   `:1133` 取 `last_tick = summary.get("last_tick") or {}`，`:1134` 取 `due`，`:1140` 取
   `material = due.get("material") or {}`，`:1141` 只在 `material.get("foreground_activity")` 为真时报
   `STATE_WAITING`，否则落到 `:1145-1147` 的 `STATE_IDLE`，basis 是**硬编码字符串**
   `"no pause, no open Activity, no blocked window in the last record"`。
2. 该字段从不落盘：`components/life/scripts/autonomous_life_resident.py:1276-1341` 的 `summary()`
   输出里没有 `last_tick`（只有 `:1293` 的 `last_tick_at`）；`tick()` 在 `:985` 用
   `self._write_summary(self.summary())` 写盘，`stop()` 在 `:928/:945` 同样只写 `summary()`。
   所以 `summary.get("last_tick")` 恒为空 → `due` 恒为空 → 分类恒落 IDLE（`STATE_WAITING` 分支不可达）。
3. 即便把 `last_tick` 补上，第二处也断了：wake 记录本身没有 `material` 字段。
   `components/life/scripts/autonomous_life_runner.py:182-199` `WakeRecord` 的字段里没有 `material`，
   `:594-599` 的 `_record()` 也不接收它；本次实测 journal 末三条（tick 332/333/334）的 `due` key 为
   `[action_id, action_status, activity_ref, at, decision, detail, extra, kind, llm_calls, outcome,
   reason, status, status_class, trigger_kind, trigger_ref]`，`material` 不存在，`extra` 只有 `waiting`。
   material 视图只存在于内存里的 `components/life/scripts/autonomous_life_cycle.py:4379-4388`
   `materials()`：它读 `self.runtime.canonical_state()["foreground_activity_ref"]`（`:4388`），
   用来算 `has_material`（`:4518-4521`），但**不进入 wake 记录**。

结论：`STATE_WAITING`（`:1140-1144`）不可达；`STATE_BLOCKED`（`:1135-1139`，读同一缺失的
`last_tick.due.status_class`）同样不可达。status 只可能给出 PAUSED / STOPPED / RUNNING / IDLE 四种。

时间上不矛盾：末段 `15:49:32–15:51:39Z`，该活动在 `15:49:34Z` 已被 PAUSE 并保留前台指针，
最后一个 tick 是 `15:51:34Z` 的 `NOT_DUE`（`status_class=silence`，`outcome=null`，无事件）。
因此「下一真实 run 加载它为 ACTIVE 并被模型 PAUSE」与 status 报 IDLE 描述的是同一存储的两个不同切面，
存续的是同一个 foreground 指针。

## 证据与另一解释

**是否真缺陷**：是。判据不是「IDLE 这个词」，而是 basis 句把三件事当成已核实的事实陈述，
其中两件根本没有读到任何权威：

- 「no open Activity」：代码从未读取 Activity 权威，只读一个不存在的 summary 字段；
  而权威当下明确有 `foreground_activity_ref=actv_91acbd742551228b`（PAUSED）。
- 「no blocked window in the last record」：`last_tick` 不存在，等于「没读记录」被表述成「记录里没有」。

**最强另一解释**：IDLE 只表示「resident 进程侧无暂停位、无运行中进程、最近窗口未阻塞」，
即它是**进程状态**而非「有没有可做的事」；按这个口径标签本身不算错，错的只是那句硬编码 basis。
否决理由有三条：其一，函数 docstring（`one_day_life.py:1116-1121`）自称「an instance that simply
had nothing to do is idle」，是内容口径不是进程口径；其二，函数自己保留了 `STATE_WAITING`
（`"an Activity is open"`），说明设计意图就是要看 Activity，只是取数路径不存在，若真是进程口径，
这条分支根本不会写；其三，`STATE_BLOCKED` 同样依赖 `last_tick`，说明「读最近记录」是原本就有的意图，
不是本次新加。故认定：**状态表述违反现有语义，属缺陷**；标签与 basis 至少有一个必须改，
最小改法是让 basis 反映真实证据。

## 最小补救

本单不实现，交给父级派工。建议写集 ≤3 个文件：

1. `components/life/scripts/one_day_life.py`（`:1115-1147`）：让 open-Activity 子句读**权威**而不是
   wake 记录——优先复用树内既有 Activity 读口（`activity_continuity.ActivityReadService` /
   `CanonicalActivityStore` 的 `get_current_life_view()`），读不到时明确写 `UNKNOWN` 与原因，
   禁止再输出「no open Activity」；IDLE 的 basis 同步改成它当时真正核实的条件。
2. `components/life/scripts/autonomous_life_resident.py`（`summary()` `:1276-1341`）：落一份**有界**
   `last_tick` 摘要（`at/kind/status/status_class`），修 `STATE_BLOCKED` 这一半；
   不写入正文、不写 prompt。若父级只想要最小改动，也可先只做第 1 项并把 BLOCKED 记为独立缺口，
   但那样 basis 里「blocked window」分句依旧无据，必须一并删掉或标注未核实。
3. `tests/test_one_day_life.py`：当前该文件对 `classify_state`/`IDLE`/`WAITING` **零覆盖**
   （grep `IDLE|WAITING|state_class|BLOCKED` 只命中两处无关的 BLOCKED 断言），需新增三例：
   - `summary(running=false, kind=...)` + 权威里 foreground 为 PAUSED（如 `actv_...`）→
     断言 `state_class == WAITING` 且 basis 含该 ref、不含 `"no open Activity"`。
   - 权威 `foreground_activity_ref=None` 且该活动已 COMPLETED + 最近一条 NOT_DUE → 断言 `IDLE`。
   - 有 `last_tick.due.status_class == "blocked"` → 断言 `BLOCKED`（若采纳第 2 项）。

**不采用**：改 Activity 状态、清理遗留 PAUSED、新增 owner、新增契约、让 status 去写库。
全部超出本单，且会破坏「读只读」语义。

## 具名验收

本单实际执行（全部只读，无产品/owner/HTTP/测试/模型调用；未 source 任何密钥，未打印私文）：

| 命令 | 退出码 |
| --- | --- |
| `grep -n "no open Activity\|state_class\|IDLE" components/life/scripts/one_day_life.py` | 0 |
| `grep -n "def read_status\|def start\|current_activity\|open_activity\|restore" components/life/scripts/autonomous_life_resident.py` | 0 |
| `grep -rn "foreground_activity" components/life/scripts/` | 0 |
| `grep -rn "def run_due\|class WakeRecord" components/life/scripts/*.py` | 0 |
| `wsl.exe -d Ubuntu -- bash -lc 'python3 - <<PY ... resident_summary.json / canonical_life_state.json 只取 key/ref/status ...'` | 0 |
| `wsl.exe -d Ubuntu -- bash -lc 'python3 - <<PY ... resident_journal.jsonl 末三条 due 的 key/status ...'` | 0 |
| `wsl.exe -d Ubuntu -- bash -lc 'python3 - <<PY ... life_transitions.jsonl 中该 id 的 from/to/reason/时间 ...'` | 0 |
| `grep -n "IDLE\|WAITING\|state_class\|BLOCKED" tests/test_one_day_life.py` | 0（无 classify_state 覆盖，属事实非失败） |

实例侧只新增了读取，未产生任何写入：未启动 serve/resident、未调用产品模型、未执行 status 或
day probe 的写路径；`run:one-day` 账本仍为 13，共同 14/96 余 82 未变。

后续工单建议验收命令（≤5，届时由父级复跑）：

1. `python -X utf8 -m unittest tests.test_one_day_life -v`
2. `python -X utf8 -m unittest tests.test_v04_autonomous_day_stage -v`
3. `node ~/.claude/skills/write-notes-like-deepseek/scripts/verify-agent-note-tree.ts`
4. `node ~/.claude/skills/write-notes-like-deepseek/scripts/verify-agent-note-format.ts`
5. `node ~/.claude/skills/write-notes-like-deepseek/scripts/verify-archived-agent-notes.ts`

## 未验证

- **未验证**：修改后的 status 在真实例上是否真的报 WAITING（以及 WAITING 标签是否符合操作者语义）；
  本单只证明当前实现的取数路径断了。
- **未验证**：`STATE_BLOCKED` 的预期输出与历史 blocked 记录是否一致；实例末段没有 blocked 记录可对照。
- **未验证**：是否存在别的读者依赖 `resident_summary` 中「无 last_tick」这一现状（若补字段，属新增 key，
  按现有读取方 `resident.read_status()` 原样返回，风险未逐一排查）。
- **未验证**：PAUSED 不代表新阅读能开始。按 `autonomous_life_cycle.py:5125-5127` 的既有规则，
  ACTIVE 与 PAUSED 都占用前台槽位、唯一合法动作是 RESUME 该活动；本单**未审候选、未验 RESUME 路径**，
  也不建议为了观察而再跑真模型。下阶段最短入口是：在同一实例上用离线/只读手段核 `actv_91acbd742551228b`
  的 RESUME 合法链路（Decision→AG-2→Activity），确认后再由父级决定是否开一个有界真机段。
- 本单不构成 G1 任何场景通过，也不改变 V-C FAIL 与 understanding UNKNOWN 的既有边界。
