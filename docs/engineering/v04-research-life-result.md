# v0.4 阶段 A/B 结果：自主求知 —— 疑问 + 真实资料 → 研究作品，且后续能回用

> 一句话：她写下的疑问能配上**研究工作台里真实可读的资料**（owner 里真实存在的工件 + 具体版本）变成一件
> 研究作品；选中它的那一轮由 B 的 `ResearchWorkspaceService` 真的建出来、由既有 AG-2/AR-0 真的写进去；
> 写作提示词里带的是**来源正文和版本**（不是 hash、不是 refs 清单），头 marker 不被改写；下一轮决策与普通
> 作品的写作能从 owner 重读到的研究结果里参考「方法/适用范围/未知」，但它始终只是文档情境 —— 不是 Memory、
> 不是信念。资料版本一动就是 STALE，归档就是 UNAVAILABLE，绝不伪装 CURRENT；资料永远不会被当成作品续写或
> 收尾。**没有真实模型、没有联网、没有动原实例。**

## 1. 实现了什么（A 侧）

| 能力 | 落点 | 关键事实 |
| --- | --- | --- |
| 研究工作台的只读/一次写接缝 | `components/life/scripts/research_work_context.py`（新，A 独占） | 只用 B 固定 API：`list_materials` / `read_material` / `create_research` / `list_research` / `read_research`；envelope 以 `ok` 为准（B 的写成功是 `status="applied"`）。marker **不复制**：研究事实只经 B 的 `parse_research_content` 得到；没有该 helper 就回答「没有研究事实」。服务缺失 → `NOT_WIRED`/`[]`/`None` + `last_error`，绝不当成「没有资料」 |
| 研究选项 | `life_project_options.py`：`KIND_RESEARCH`、`materials_view`、`material_refs`、`build_options(research_materials=, research_available=)` | 只有工作台真的接线时才把「她的疑问」变成**一个**研究选项（不是两个）；`materials` 就是 `list_materials` 刚返回的命中（`artifact_id` + `source_revision` 原样保留，缺 id 的命中直接丢弃）；没有命中就 `materials=[]` 并在 `uncertainty` 里说实话；资料的 revision 进 option 身份，所以「资料变了」是**新选项**而不是复用旧选项 |
| 选中即创建 | `autonomous_life_cycle._research_adoption_spec` / `_research_operation_key` | 只对「Decision 选中的正是这个 popt 选项」执行；`create_research(question=疑问, source_refs=真实命中, title, scope, uncertainty, operation_key=…)` 由 B 调 owner 建件；拒绝按名抛出、不建 Activity、不编造 artifact id。`operation_key` 由「选项 + 具体来源版本」派生，重试不会多建一件 |
| 写作真的读到来源 | `model_continuation_writer.py`（`context=` → prompt 的 `research_context`、`split_header` + 保 marker 的 `_merge`）、`cycle._next_content` | AR-0 步骤与直连 port 两条写路径都走 `_next_content`；研究作品拿到 `own_research`（question/scope/uncertainty/source_refs + **每个来源在它被引用的那个版本上的正文**，由 `read_material(expected_revision=…)` 读）+ B 自己的 `source_checks`；普通作品拿到 `prior_research`（既有研究的 question/scope/uncertainty/片段）。首行 `<!-- … -->` 是**头部**：append 写在它下面，revise_tail 只改正文，marker 不会被搬走/删掉/改写。`no_change` 仍然合法（`ModelNoChange`，不写版本） |
| 决策 prompt 看得见 | `cycle.decision_situation_context` 的 `research` 块；`research_decision_context()`；`ResearchSeam._content_excerpt` | 有界材料命中（id + 命中时的 revision + 出处 + 片段）+ 已有研究结果（question/scope/uncertainty/片段/来源检查词）。片段取自 **B parser 的 body**（marker 已分离），并按「小头 + 尾巴」截断 —— 所以 AR-0 刚写进去的暂定发现/做法也在里面，后续 prompt 不会只看到 marker 或模板头。研究选项本身也在 `project_options` 里，带 `materials`/`scope`/`uncertainty` |
| 资料不是作品 | `artifact_continuation_source.py`：`exclude_artifact_provider`、`_material_exclusions`、`last_material_skip_reasons` | 工作台说「这是资料」的工件不进候选、不进收尾；已存在的候选在 `refresh` 时被 `RESEARCH_MATERIAL_NOT_A_WORK` 撤回。阅读材料与私人事项文档**不**受这条影响（阅读机会照旧），研究作品本身是普通可续写工件 |
| 安静 | `life_step_readiness.SITUATION_KEYS` 增 `research_materials` / `research_works`；`cycle._situation_parts` 填入 seam 的指纹 | 只有 id / 版本 / 出处 / 检查词进入指纹，**没有文本、没有时间**。资料换版、被归档、读不到、研究换头都会变成新处境；单纯的 tick 不会 |
| 生产接线 | `autonomous_life_assembly.resolve_research_service` + `assemble_autonomous_life` | `research_from_env(port, env)` 用与 private-project 同一套 env/bindings 建服务；把 A 已绑定的 `CHIYO_LIFE_READING_ARTIFACT_ID` / `CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID` 以 B 自己的名字（`NYAIRO_LIFE_READING_ARTIFACT_ID` / `NYAIRO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID`）转交（只做拼装，语义仍是 B 的）。装好的回路：`research_workspace` → `cycle.research`；报告里 `assembled["research"]` 说清是「树里没有」还是「有但没有资料」 |
| 疑问真的读得到 | `autonomous_life_assembly` 的 `project_questions` provider | 原来读 `items("unclear")`（类型拼写不对）且元数据视图**不含 note 文本** → 现在按 `ITEM_UNCLEAR_MARKER` 取行，再用文档自己的 `full_text(item_id)` 显式读取疑问原文 |

既有的 admitted Memory provider 一行未改；研究内容不进 `previous_experiences`，也不带 `experience_id`。

## 2. B 接口的实际对接（与阶段单的差异 / 已对齐点）

* 用到的方法与签名**完全按阶段单**：`ResearchWorkspaceService(projects, *, reading_artifact_id=, private_items_artifact_id=)`、
  `list_materials(query, limit)`、`read_material(artifact_id, expected_revision=)`、`create_research(question, source_refs, title, scope, uncertainty, operation_key)`、
  `list_research(limit)`、`read_research(artifact_id)`、`research_from_env(port, env)`、`parse_research_content(content)`。
* 三处**形状细节**（已在 A 侧适配，不改 B）：
  1. 写成功的 envelope 是 `{"ok": true, "status": "applied", ...}`，不是 `status: "ok"` → A 先看 `ok`；
  2. 来源检查的字段名是 `status`（`CURRENT`/`STALE`/`UNAVAILABLE`），不是 `check` → A 两个都认；
  3. `research_from_env` 读的是 `NYAIRO_*` 名字，而 Life 实例的环境用 `CHIYO_LIFE_*` → 装配时按 B 的名字转交这两个 id。
* 没有任何 A 侧复制 marker/资料 marker 定义；A 不写研究作品以外的任何存储。

## 3. 能怎么运行

```bash
# 新增测试（POSIX；life 模块 import fcntl）：12 条纯逻辑 + 3 条真实 owner 链
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_v04_research_life_stage

# 直接受影响的既有链（本阶段只跑这几组，不做全库回归）
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest \
  tests.test_v04_autonomous_day_stage tests.test_model_continuation_no_change \
  tests.test_day_life_quiet_runner tests.test_day_life_decision_legality
```

真实运行不需要新命令：`assemble_autonomous_life` 会自动接上 `research_workspace`；工作台可不可用取决于
B 的 `research_from_env` 需要的 env（Supply socket / subject / workspace / grant）。不全就是「不可用」——
不出现研究选项、也建不出研究作品。

## 4. 已测什么（实跑）

| 组 | 条数 | 结果 | 日志 |
| --- | --- | --- | --- |
| `tests/test_v04_research_life_stage.py`（全部：14 纯 + 3 真实 owner 链） | 17 | `OK`，退出码 **0**（最新一次 `Ran 17 tests in 12.048s`；加 excerpt 两条用例之前 15 条时为 `Ran 15 tests in 9.349s`） | `/tmp/research-new.log`（修复前红灯 `/tmp/research-red.log`，`RED_EXIT=1`；修后精准方法 `/tmp/research-fix-methods.log`，`FIX_METHODS_EXIT=0`，3 条 7.446s） |
| `test_v04_autonomous_day_stage` + `test_model_continuation_no_change` + `test_day_life_quiet_runner` + `test_day_life_decision_legality`（直接受影响） | 43 | `OK`，`AFFECTED_EXIT=0`（`Ran 43 tests in 8.002s`） | `/tmp/research-affected2.log` |

新测试里的**完整功能链**（真实 Supply socket + 真实 AG-1 原始 JSON 校验 + 真实 AG-2 + 真实 AR-0；只有
决策回答与内容回答是脚本替身，测试文件里已明示 `_ResearchCognition` / `_ScriptedModelClient`）：

1. B 的工作台真的 `import_material` 导入一段带来源声明的资料（owner 里多一件工件）；
2. 她通过私人事项文档自己的 `mark_unclear` 真的写下疑问（`full_text` 读回原文）；
3. 决策处境里出现材料命中（id + revision + 出处 + 片段）与研究选项；脚本替身只回原始 JSON，真实 AG-1 校验；
4. `START` 该选项 → B `create_research` 让 owner 建出研究作品（首行 marker、question、真实 source_refs、uncertainty）；
5. 同一轮 AR-0 写出第 2 版；写作 prompt（真实 `ModelContinuationWriter` 生成的 JSON）里 `research_context.own_research.sources[0]`
   就是来源正文 + 它的版本，`scope`/`uncertainty` 在场；写完后首行 marker 仍在，**脚本模型写的那句独有发现**
   （`FINDING`）真的进了 owner 文档；
6. 下一轮决策的 `research_results[0].excerpt` 与普通作品的 `prior_research[0].excerpt` 都**包含那句独有发现**
   （且不含 `<!--` / `nyairo-research:v1`），研究作品的写作上下文 `own_research.content_excerpt` 同样包含它 ——
   也就是说后续 prompt 读到的是 AR-0 真正写进去的内容，不是 marker 或 B 的模板头；question/scope/uncertainty/
   source_checks 仍是独立字段；
7. 资料再提交一版 → `read_research` 的检查词是 `STALE`、`sources_current=False`，A 的写作上下文对该来源
   `availability=STALE` 且**不给正文**；再把资料归档 → `UNAVAILABLE`；研究文档本身没被动；
8. 资料永远不会被当成作品：`materialize()` 不产出它的候选、拒绝原因具名、也不在收尾目标里，而她真正的作品仍在。

### 本阶段修掉的一个实际缺口（父级 smoke 发现）

`_project_view` 原先的 `content_excerpt` 是**整篇 `content` 的头 240 字**，而研究文档首行的 marker JSON 通常就已经
超过 240 字：后续决策与普通作品的 prompt 因此只看到 marker，看不到 AR-0 新写进去的暂定发现/做法。现在
`ResearchSeam._content_excerpt` 用 **B 自己的 parser 给出的 body**（marker 已由 B 分离；没有 parser 时按通用
Markdown 规则剥掉前导 `<!-- … -->`），并按「小头 + 尾巴」有界截断（尾部占多数，因为 writer 是往末尾追加）。
先加**更强断言**证明红（`/tmp/research-red.log`：`RED_EXIT=1`，`FINDING not found in '<!-- nyairo-research:v1 …'`），
再修；修后同一条链 `FIX_EXIT=0`（`/tmp/research-green.log`，`Ran 1 test in 8.452s`），并新增两条纯用例
（`ResearchExcerptTest`）钉住「长文取尾、短文原样、无 marker 文本不丢」。

**未跑**：全库回归、跨进程 qualification、真实模型/密钥、真实实例。夹具在本会话
`/tmp/nyairo-v04-research-fixtures`（我自己的根，不删）；实例树由既有 `WorldGenTestCase` harness 管（与
上一阶段同一种用法，没有新增/去除清理逻辑），`PYTHONDONTWRITEBYTECODE=1` 全程开启。

### 当前失败中、但不在本阶段改动范围内的三处红灯（未修）

本树上顺手跑到的三处失败；它们所在文件本次**没有被修改**，也**没有**被本阶段的改动覆盖：

* `tests/test_v04_private_projects_stage.py::LifeOwnerAdapterTest::test_gateway_creates_one_project_through_the_owner`
  —— 当前失败：断言 `read_project` 的 `artifact_id == "artifact:" + artifact_id`，而 owner 返回的 id 已经带
  `artifact:` 前缀。
* `tests/test_life_wait_resume_v04.py::WaitBasisCycleTest::test_a_round_without_a_dependency_sends_no_basis_at_all`
  —— 当前失败：期望「无可依赖就不发 basis」，实际收到 `parkbasis:…`（`park_basis_for_round` 在「前台 ACTIVE 且
  可行动」时给出 park basis）。本阶段未改 park/wait 代码。
* `tests/test_day_life_continuity.py::DayLifeContinuityTest::test_a_body_act_adoption_names_w_fact_key_and_expected_value`
  —— 当前失败：`ERROR`，调用 `self._world_cycle(snapshot=…)`，而该测试自己的 helper 不接受 `snapshot=`。

我不修这些测试，也不断言它们的历史来源；父级门禁会看到同样的红。

## 5. 边界（不宣称的）

* 不联网、不做全网研究：资料来自**操作者导入/既有阅读材料**，`origin` 是**声明**而不是真实性认证。
* 不是稳定的「个人方法」：研究作品里的「暂定发现」还是暂定，`uncertainty` 默认就是「暂定，尚未验证」。
* 不是 Memory 因果闭环：研究内容只作为 owner 文档情境进 prompt，不冒充已准入的 experience，也不改变人格/信念。
* 不是 24 小时自主生活：这一阶段只把「疑问 → 资料 → 研究作品 → 回用」这一条链做实，安静/预算/暂停语义未改。
* 手机侧资料库/研究工作台的 UI 与 API 是 B 的独占范围，本文件不涉及。
