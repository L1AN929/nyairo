# I-r3 回执：World 后果的 M0 叙述 / `experience_provider` 装配更正 / 一条经验真的改变一次合法选择

范围按父级窄收口：只动 **consumer 的 `_text`/`_refs` 有限区 + 自有 tests**，不重建 resident/assembly，
不改 W/F/Body/Memory/Supply owner 文件，不改 AG-0/AG-1/AG-2 授权与单前台语义。全部改动仍是
**UNCOMMITTED**（`HEAD` 仍 `7f09d26df64e054faf51ba35ad85c64f73a5a837`）。

## 1. owned delta（1 改 4 新）

| 文件 | 状态 | 内容 |
|---|---|---|
| `components/life/scripts/life_experience_memory_consumer.py` | M | World 后果的中性模板 + `world` 来源 refs + `UNKNOWN` 不升级 + docstring |
| `tests/test_world_daily_life_continuous.py` | 新（r2 起的未跟踪文件） | 步骤 8 文本断言改中性、新增 8b“这条行改变一次合法选择”、步骤 9 按 `prompt_view` 形状认行、替身加规则 5 |
| `tests/test_world_daily_life_world_memory_text.py` | 新 | 模板 3 例 + 对照 1 例（真实 M0 owner） |
| `tests/test_world_daily_life_experience_wiring.py` | 新 | `experience_provider` 装配两端：配了 Memory → 请求有；没配 → 请求空 |
| `docs/engineering/world-daily-i-r3-result.md` | 新 | 本回执 |
| `.agents/notes/implemented/architecture/2026-10-18-world-daily-world-consequence-wording-and-wiring.md` | 新 | 本人笔记；并就地更正 r2 笔记里两处错判 |

`git diff --stat` 里**产品代码只有 consumer 一处**：`54 insertions(+), 2 deletions(-)`。

## 2. §4 更正：`experience_provider` 在产品路径上**是**接线的

r2 回执 §4 末（`world-daily-i-r2-result.md:129-131`）写“`experience_provider` 在生产装配里没有被
`assemble_autonomous_life()` 接上，模型从来收不到 `prior_experiences`”。**这只对测试基类成立**，
对产品不成立。只读证据（本批未改这些文件）：

| 位置 | 事实 |
|---|---|
| `components/life/scripts/autonomous_life_resident.py:282-305` | `_memory_ports()`：没有 Memory DB 配置 → `(None, None, None)`；配了 → `MemoryAdmissionExperienceConsumer(port=port, enabled=True)` 和 `lambda: port.prompt_view()`；第三个返回值是同一个 port，给 C 的连续读 |
| 同上 `:399` | `consumer, experience_provider, memory_port = self._memory_ports()` |
| 同上 `:436` | `experience_provider=experience_provider, world_provider=world["provider"]` → `assemble_autonomous_life(...)` |
| `components/life/scripts/autonomous_life_assembly.py:441` | 形参 `experience_provider: Any = None` |
| 同上 `:539` | `alc.AutonomousLifeCycle(..., experience_provider=experience_provider, ...)` |
| `components/life/scripts/autonomous_life_cycle.py:1935` → `:1955` | `consideration = self.consideration_context()` → `run_decision_and_adoption_cycle(prior_experiences=consideration, ...)` |
| 同上 `:3480-3485` | `_experience_view()`：无 provider → `[]`；provider 抛错 → `[]`（有界失败，不编历史） |

**最小测试**（`tests/test_world_daily_life_experience_wiring.py`，2 例，真实 M0 owner + 真实 cycle 对象）：

- `test_a_configured_memory_is_what_the_decision_request_carries`：`MemoryFixture.admit()` 准入一条
  World handoff → 用 `AutonomousLifeResident._memory_ports(env)` 建出真实 consumer/provider → 同一个
  provider 传进真实 `AutonomousLifeCycle(experience_provider=provider)` → `consideration_context()`
  （就是 `prior_experiences`）里带着这行，形状正是 `prompt_view` 的 7 个键
  （`experience_id/occurred_at/text/activity_ref/artifact_ref/version_ref/owner_result`），
  `artifact_ref is None`、`owner_result` 以 `rset:` 开头。
- `test_no_memory_configured_leaves_the_request_without_experiences`：空 env → `(None, None, None)`；
  provider 为 `None` → `consideration_context() == []`；provider 抛 `RuntimeError` → 同样 `[]`
  （无异常、无编造）。

仍未改的一点（如实）：`tests/test_life_v1_integration.py:196-208` 的测试基类 `assemble()` 不传该参数，
所以演练里是显式 `cycle.experience_provider = …`。产品入口（resident）不需要手动接。

## 3. 中性模板：World 后果不再写成“作品新版本”

`life_experience_memory_consumer.py`：

- `:58-59` `_WORLD_CONFIRMED="在真实世界里发生了一件已确证的事"` /
  `_WORLD_UNSETTLED="在真实世界里做过一件事，结果还没有确定"`；
- `:63` `_UNSETTLED_OUTCOMES={"UNKNOWN_EFFECT","UNKNOWN","PENDING","OPEN"}`；
- `:145-146` `_refs` 在 artifact 之后保留 owner 自己的 `world` refs（最多 2 条），`settlement→owner_result` 照旧；
- `:154-160` `_text` 分流：**有 `world_refs` 且没有 `artifact_ref`** → `_world_text()`；否则原作品句原样；
- `:171-181` `_world_text()`：`一条中性句；世界事实 …；动作 …；所有者回执 …；发生在 …`；
- `:183-192` `_unsettled()`：读 `outcome_kind`/`settlement_outcome`，以及 world ref 末段（`…:UNKNOWN_EFFECT`）；
- `:194-195` `_trim()` 仍受 `max_chars` 约束。

为什么按证据分支而不是按 kind：旧模板对**任何** `SIGNIFICANT_PROGRESS` 都说“完成了一次真实修改，作品所有者
确认了新版本”，而一次消费/停留的 handoff 根本 `artifact_refs=[]`——继续用它就是伪造作品后果。
为什么“已确证”可以写：`_emit_world_experience()` 只在 owner 回执/观察为真时才发 handoff
（cycle `:3477-3485`），所以肯定句由构造保证；显式携带未决标记的照样降级。

`tests/test_world_daily_life_world_memory_text.py` 4 例（真实 `MemoryFixture`：真 `EvidenceStore` + 真控制账本 +
真 `LifeExperiencePort` + 真 consumer）：

1. World-only → 以中性句开头、含 `世界事实 world_fact:object:i-food-riceball-01:consumed` / `动作 actn:…` /
   `所有者回执 rset:…`，**不含** `真实修改`/`新版本`/`作品`；`source_refs` 里两条 `world` id 按 owner 顺序原样保留、
   `action`/`owner_result` 在、没有 `artifact`；`prompt_view()` 里查得到这行且不含“真实修改”。
2. artifact handoff → 文案与来源完全不变（World 分支一旦泄漏到它就失败）。
3. `outcome_kind="UNKNOWN_EFFECT"` → 含“结果还没有确定”，不含 `已确证`/`完成`/`新版本`/`作品`；同一 handoff
   再投递一次 → `DUPLICATE`，`rows()` 仍只有 1 行。
4. 对照：同一候选集 + 同一固定规则，真实 World 行 → `stay:home:LOOK_AROUND:180`；真实 artifact 行 / 空行 /
   空列表 → `place:street_front`；末了 `world_consumed==[]`、`here=="home"`（记忆没有替位置/权限/身体说话）。

## 4. 主链：这条行真的改变了一次合法选择，之后的续步还是真执行

`tests/test_world_daily_life_continuous.py`（一次连续 episode，同一实例）：

- `:92-93` `_WORLD_CONSUMED = re.compile(r"world_fact:object:([A-Za-z0-9_.:+-]+?):consumed")`；
- `:225` `ScriptedWorldCognition._world_consumed(experiences)`：只从**被准入的历史行**里读 owner 的结构化事实 token；
- `:158-162` 规则 5 的边界写进 docstring：记忆只能说“那件事已经发生”，永远不能代替位置/权限/身体/人格；
- `:453-464` `experience_rows()` = `memory.port.prompt_view()`：模型真正看到的那一份（**没有** `action_ref`）；
- 步骤 8（`:901-924`）：按 `consume_fact` 从 `memory.port.recent()` 挑出世界行，断言 `action_ref` 是本次消费的
  AR-0 动作、`artifact_ref is None`、`outcome_kind=="SIGNIFICANT_PROGRESS"`、`owner_result` 以 `rset:` 开头，
  文本含世界事实 + 动作 + 回执，且**不含** `真实修改`/`新版本`/`作品`；
- 步骤 8b（`:926-949`，本批新增）：同一次输入里同时有世界行和作品行（作品行 `artifact_ref` 非空、不含世界事实，
  不能顶替）；`goal=="eat:any"` 不变，`drill:after-consume` 这一轮替身读到 `world_consumed==[FOOD]`，在
  `place:street_front` 仍然合法可去的情况下选了 `stay:home:…`（`planned_seconds>0`、`phase=START`），随后
  `advance=planned+60` 走到 `END`、前台清空；
- 步骤 9（`:964-976`）：RESUME 那一次的 `DecisionRequest` 里真的带着这行（`experiences` 含 `consume_fact` 与
  `owner_result`）以及同一条 `observed_facts` 通道的 `world_place_*`；随后 CONTINUE 真落新 Supply 版本。

`ir3-main-2.log` 的原始行（演练自己的打印标签仍是 `R2 `，那是脚本的行前缀，不是批次号）：

```
R2 CONSUME receipt=world-consumption:b2a9114f73f4b6204fbd88ea24d2380e execution=world-exec:26586f2b8ab51b87bb72a6da4163e2d4 body=1
R2 M0-WORLD row=lexp:356e66a60efd69bc action=actn:8e35b11af61b9011 execution=world-exec:2658… owner_result=rset:e4a7f889c88e59c5 text=在真实世界里发生了一件已确证的事；世界事实 world_fact:object:i-food-riceball-01:consumed、world:object
R2 drill:after-consume    outcome=ACTED decision=START chose=stay:home:REST:300 stay=START place=home
R2   offered=6 facts={"home": "OPEN", "street_front": "OPEN"} chose=stay:home:REST:300
R2 WORLD-IN-CHOICE chose=stay:home:REST:300 consumed=['i-food-riceball-01'] reason=world_fact:object:i-food-riceball-01:consumed is in her admitted history; the appetite is answered, staying here instead of going out for more
R2 M0-INTO-REQUEST experiences=5 carries=world_fact:object:i-food-riceball-01:consumed
R2 RESUME checkpoint=True continued=artifact-version:93791bb600824241a7832c14bc527541->artifact-version:386bca781efc4a42a6a8446f618ab103
R2 HISTORY row=lexp:f4a634180a2ddec8 owner_result=rset:3adf38fbcece5239 head=artifact-version:386bca781efc4a42a6a8446f618ab103
```

同一次 episode 里仍然成立的旧证据（本批未推翻）：`STEP_UNKNOWN → RECONCILED_SUCCEEDED`、
`list_actions(status="UNKNOWN")==[]`、门前街读到 `world_place_reading_point_availability_CLOSED` 后改成就地停留、
`PAUSE calls=4->5 budget=0`（暂停不重置计数）。

## 5. 精确命令与真实退出码

```bash
S=/mnt/c/Users/<user>/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37
cd /mnt/d/nyairo-v03-backend
export PYTHONDONTWRITEBYTECODE=1 TMPDIR=/tmp/nyaw05r.jdK3q
$S/w04venv/bin/python $S/i_daily_runner.py <module>   # 每模块独立子进程，抓真实 exit（runner 已把 shutil.rmtree 置 no-op）
```

| 模块 | 结果 | 日志 |
|---|---|---|
| `tests.test_world_daily_life_world_memory_text` | `Ran 4 tests … OK`，`PY_EXIT 0` | `ir3-world-memory-2.log` |
| `tests.test_world_daily_life_experience_wiring` | `Ran 2 tests … OK`，`PY_EXIT 0` | `ir3-wiring-1.log` |
| `tests.test_world_daily_life_continuous` | `Ran 1 test … OK`，`PY_EXIT 0`（28.0s） | `ir3-main-2.log` |
| `tests.test_world_daily_life_plan_change` | `Ran 1 test … OK`，`PY_EXIT 0`（21.9s） | `ir3-planchange-1.log` |

第一次的失败日志也留着，不覆盖：`ir3-world-memory-1.log`（`world` ref 断言取到了第二条 world ref）、
`ir3-main-1.log`（`experience_rows` 的 `def` 行在编辑里丢了 → `AttributeError`）。两处都已修，
**没有**为了让测试变绿而放宽断言。

**没跑**：四模块一次全跑、旧 17/56/155/179/184/109 套件、真实模型、外网、长跑、部署（父级未授权，本批也不需要）。

## 6. 仍然缺什么（真话）

1. **owner 侧 `UNKNOWN` 根因**：World intent/gateway 偶发把真实出发答成 `UNKNOWN`；Life 只做闭环，根因归 owner/explorer。
2. **无回执动作的结算**：AR-1 只能给 `settlement_status=OPEN` / `outcome_kind=UNKNOWN_EFFECT`。本批保证这种未决后果
   **不会**被写成确定成功（模板降级），不等于“已经能结算”。
3. **“等开门”无 owner due-time**：她只能换一个合法动作，不会在关着的门口自动重试。
4. **`prompt_view` 不含 `action_ref`**：模型看到的是 `text/activity_ref/artifact_ref/version_ref/owner_result`。
   所以“读历史”只能认行文里 owner 自己的结构化 token（`world_fact:object:<id>:consumed`），不能按 `action_ref` 索骥；
   本批没有为了测试方便去改 Memory 的视图形状。
5. **`tests/test_life_v1_integration.py` 的 `assemble()` 不传 `experience_provider`**（`:196-208`）：那是测试基类的
   装配，不是产品路径。要不要顺手对齐，由父级决定。

## 7. 未删除 / 未触碰 / 未 stage

- 夹具全保留：`/tmp/nyaw05r.jdK3q/i-r1-*`、`ir2-*`、`ir3-*`（新的 text/wiring 用例各自 `mkdtemp` 一个新目录）、
  `w05inst-*`（含 `w05inst-fgon6ibm`）。没有 `rm`/`unlink`/`rmtree`/`TemporaryDirectory` 清理、没有 pyc 删除、
  没有 socket unlink、没有全局安装、没有 provider/部署改动。
- 未改：W/F/Body/Memory/Supply 的 owner 文件、`life_integration_ag2.py`、`action_reality_ledger.py`、
  AG-0/AG-1/AG-2 授权与单前台语义、别人的 notes/AGENTS、管理 00/03/方案文档。
- 未 stage / 未 commit / 未 push / 未 reset / 未 clean / 未 checkout：`git diff --cached` 为空（0 个文件），
  `git rev-parse HEAD` = `7f09d26df64e054faf51ba35ad85c64f73a5a837`。

## 8. 门禁（`gates_r3.sh` 实跑，真 subprocess exit）

```
ok: 23 note(s) tree and relative links verified
TREE_EXIT=0
ok: 23 note(s) verified
FORMAT_EXIT=0
DIFFCHECK_EXIT=0
HEAD=7f09d26df64e054faf51ba35ad85c64f73a5a837
STAGED=0
```

笔记树从 22 条变 23 条，是因为新增了本批的 r3 笔记；r2 笔记里两处错判已就地更正
（`experience_provider` 的装配结论、M0 叙述模板）。
