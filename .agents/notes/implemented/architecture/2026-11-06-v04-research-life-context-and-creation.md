# Agent Note: v0.4 A 侧 —— 从疑问 + 真实资料到研究作品，并把结果作为文档情境回用

Status: implemented

## Problem

上一阶段能做到「从她自己的疑问/到期项长出**新项目选项**」，选中后由 B 的 owner 建出一件普通作品并写下去。
但「求知」这件事仍然缺三块真东西：

1. **疑问没有资料。** `life_project_options` 只会把 `unclear` 的文本当成 seed text，从来没读过任何**可访问的
   资料正文与版本**；`create_project(seed_text=…)` 建出来的是「一句话项目」，不是「有来源的研究」。
2. **写作提示词读不到来源。** `ModelContinuationWriter` 的 prompt 只有作品自己的尾部正文 + 记忆切片，
   没有任何「这份东西依据的是哪段材料、哪个版本」的信息。选中一个研究作品后，模型即使想写「暂定发现」，
   也无处读到来源，只能编。
3. **B 的研究工作台（资料库/研究作品）与 Life 主循环之间没有接缝。** 阶段单已经固定了 B 的公共 API
   （`ResearchWorkspaceService` 的 5 读 1 写 + `research_from_env` + 两个纯 helper），但 A 侧没有任何东西
   调用它：候选集里没有研究选项，决策处境里没有材料命中与研究结果，安静指纹里没有资料版本，候选通道也
   不知道「哪些工件是资料、不是作品」。

## Decision

一句话：**A 只做三件事 —— 把 B 的工作台包装成一个有界、只读为主、一次写的接缝；把「疑问 + 真实命中」
变成一个研究选项并在 Decision 选中时交给 B 创建；把 owner 重读到的来源正文/版本/检查词与研究结果，
作为有界的**文档情境**放进决策与写作提示词。** marker 与资料分类仍然只有 B 一份定义。

1. **新接缝 `research_work_context.ResearchSeam`**（A 独占，`components/life/scripts/research_work_context.py`）。
   它把 B 的 envelope 变成有界视图：`materials()`（命中 + id + 版本 + 出处 + 片段）、`existing()`（研究头 +
   question/scope/uncertainty + B 的 `source_checks` + 有界正文摘要）、`writing_context(artifact_id)`、
   `decision_context()`、`create_research(...)`、`fingerprint_parts()`。envelope 以 `ok` 为准（B 的写成功是
   `status="applied"`）。研究事实只经 B 的 `parse_research_content` 得到：**A 不复制 marker 定义**；helper 不在就
   回答「没有研究事实」。服务缺失 → `NOT_WIRED` + `[]`/`None` + 具名 `last_error`，绝不当成「没有资料」。
   **正文摘要必须是「她的字」**：`_content_excerpt` 用 B parser 给出的 `body`（marker 已由 B 分离；无 parser 时
   按通用 Markdown 规则剥掉前导 `<!-- … -->`），超长时取「小头 + 尾巴」（尾部占多数，因为 writer 往末尾追加）。
   最初写成「整篇 content 的头 240 字」，而 marker JSON 本身就超过 240 字，后续 prompt 只看到 marker —— 这是
   父级产品 smoke 读到的实际缺口，已修并由更强的链条断言钉住。
2. **研究选项**（`life_project_options.KIND_RESEARCH`）。只有工作台真的接线时，一个疑问才变成**一个**研究
   选项（不是两个）；`materials` 就是 `list_materials` 刚返回的命中，`artifact_id` + `source_revision` 原样保留，
   缺 id 的命中直接丢弃；没有命中就 `materials=[]` 且 `uncertainty` 写明「当前没有可读资料，问题保持开放」。
   资料的 revision 进 option 身份 → 资料换版是**新选项**，不是悄悄复用旧选项。
3. **选中即创建，复用既有法定路径**（`cycle._project_adoption_spec` 的 `research` 分支 →
   `_research_adoption_spec`）。候选仍是既有的 `PERSONAL_OPPORTUNITY` 来源 + `CONSIDER_PERSONAL_OPPORTUNITY`
   种类（`popt:` ref），所以前台/合法性/AG-1 校验/AG-2 adoption 一条都没绕。`operation_key` 由「选项 + 具体来源
   版本」派生（`research:<sha>`），UNKNOWN 解析后重试不会多建一件。拒绝按名抛出、不建 Activity。
4. **写作真的读到来源**（`model_continuation_writer.next_content(..., context=)` + `cycle._next_content`）。
   AR-0 步骤与直连 port 两条写路径都走 `_next_content`，所以「提示词带来源」与走哪条写路径无关。研究作品
   的 `research_context.own_research` 带 question/scope/uncertainty/source_refs + **每个来源在被引用的那个
   版本上的正文**（`read_material(expected_revision=…)`）+ B 自己的检查词；普通作品的 `prior_research` 带
   既有研究的 question/scope/uncertainty/片段。都只是**文档情境**：不进 `previous_experiences`，不带
   `experience_id`，admitted Memory provider 一行未改。
5. **marker 不动**：`ModelContinuationWriter.split_header` 把首行 `<!-- … -->` 当**头部**（通用的 Markdown
   规则，不是第二份 marker 定义）：append 写在它下面，revise_tail 只改正文。`no_change` 仍然合法
   （`ModelNoChange`，不写版本）。
6. **资料不是作品**：`ArtifactContinuationSource` 增只读 `exclude_artifact_provider`；工作台说「这是资料」
   的工件不进候选、不进收尾，已存在的候选在 `refresh` 时按 `RESEARCH_MATERIAL_NOT_A_WORK` 撤回，并在
   `last_material_skip_reasons` 里具名报告。**阅读材料与私人事项文档被显式排除在这条排除之外**（否则工作台
   一接线就会把「读一页」的机会关掉），研究作品本身仍是普通可续写工件。
7. **安静仍然是安静**：`life_step_readiness.SITUATION_KEYS` 增 `research_materials` / `research_works`；
   指纹里只有 id / 版本 / 出处 / 检查词，没有文本、没有时间。资料换版、归档、读不到、研究换头 = 新处境；
   单纯 tick 不动一个字符。
8. **生产接线在装配里**（`autonomous_life_assembly.resolve_research_service`）：用 B 的
   `research_from_env(port, env)` 在同一套 env/bindings 上建服务（factory 只吃 env 也能接），并把实例已绑定的
   阅读材料/私人事项 id 以 **B 自己的 env 名字**转交（只做拼装）。报告里 `assembled["research"]` 说清是
   「树里没有」「工厂拒绝」还是「有但没有资料」。
9. **顺手修掉一个真缺陷**：装配里的 `project_questions` provider 原来读 `items("unclear")`（类型拼写错）且
   私人事项的元数据视图**不含 note 文本** → 现在按 `ITEM_UNCLEAR_MARKER` 取行 + 文档自己的
   `full_text(item_id)` 显式读取疑问原文。没有这一修，「研究选项」永远没有 seed。

## Alternatives considered

* **A 自己按 B 的 marker 格式拼一个研究文档（`<!-- nyairo-research:v1 … -->`），只调 `create_project`。**
  最强立论：完全不必等 B 的服务，A 立刻能跑通「选中 → 建件 → 写」，也不会被 B 的 envelope 细节卡住。
  否决原因：那就是**第二份 marker 定义**，B 一改格式（或加字段）两边立刻漂移，而且资料分类、来源核对、
  STALE 判定都会变成 A 自己的一套。阶段单明确写了 helper 由 B 唯一维护；A 只调 API。
* **给 AG-0 加一个新的 source_kind（如 `RESEARCH_PROJECT`）。** 最强立论：语义最干净，候选一眼能看出类别。
  否决原因：`candidate_sources_ag0.py` 的禁列就是为了防止这种「发明类别」；研究选项本来就是她的**个人机会**，
  复用 `PERSONAL_OPPORTUNITY` + `popt:` ref 是同一个意思、零新增类别、且自动复用 AG-1 legality 与 AG-2。
* **把研究结果写进 Memory / experience provider，让「经验回用」更彻底。** 最强立论：她「学到的方法」应该
  长期可用，Memory 已经有准入治理。否决原因：研究作品是**暂定**的东西（`uncertainty` 默认「尚未验证」），
  让它自动成为已准入 experience 就是把「暂定」升级成「信念」，正是设计禁止的晋升；而且 A 无权替 Memory
  做准入判定。文档情境是唯一诚实的形态。
* **资料命中直接从 `LifeArtifactPort` 读（跳过 B 的 `list_materials`）。** 最强立论：A 已经有 port，少一层。
  否决原因：B 的「哪些工件算资料」是按它自己的 marker 分类的（`KIND_MATERIAL`/`KIND_READING_MATERIAL`），
  A 自己判断会立刻出现第二套资料定义，并且会把研究作品/普通作品误当资料。
* **在 `refresh` 之外再放一个「资料已变」的通知/唤醒。** 最强立论：资料换版应该尽快重新考虑。否决原因：
  那就是按事件强醒，而不是按处境；把资料 id/版本/检查词放进处境指纹已经让「资料变了」成为新处境，
  不需要第二条唤醒通道，也不会因为 tick 变成付费请求。

## Consequences

* 现在可以：她写下「为什么旱季取水要排这么长的队？」→ 工作台里那段真实资料作为命中出现（id + 命中时的
  版本 + 出处）→ 这一轮多出**一个**研究选项 → 她选中 → B 的 `create_research` 让 owner 真的建出研究作品
  （首行 marker、question、真实 source_refs、uncertainty）→ 同一轮 AR-0 写出第 2 版，写作 prompt 里带的是
  来源**正文和版本**，marker 保持不动；下一轮决策能读到这份研究的结果，普通作品的写作也能参考它。
* 现在可以：资料再提交一版 → 检查词 `STALE`、A 的写作上下文对该来源不给正文；资料归档 → `UNAVAILABLE`；
  研究文档本身一字不动。
* 现在可以：资料永远不会被当成「她的作品」续写或收尾，而她真正的作品与阅读机会照旧。
* 已知代价与残留：
  1. B 的 envelope 细节（写成功是 `status="applied"`、来源检查字段叫 `status`、`research_from_env` 用
     `NYAIRO_*` 名字）与 A 的既有拼写不同；A 侧已适配并在结果文件里记录，但**这些是 B 的接口事实**，
     若 B 再改，A 会按名拒绝而不是猜。
  2. 「研究选项」只在一个疑问 + 工作台可用时出现；工作台不可用就没有研究选项 —— 这是有意的（不提供
     选了会失败的东西）。
  3. 研究作品的第一版正文是 B 的纯结构模板（「## 问题 / ## 暂定范围 / ## 已知来源 / ## 暂定发现 / ## 未知」），
     A 只往里追加/改末段；真正的「发现」要等真实模型跑起来。正文摘要有界（240 字，头 1/3 + 尾 2/3），所以在
     极长的研究文档里，中段内容不会同时出现在摘要里 —— 尾部（最新写入）总是优先，且 question/scope/
     uncertainty/source_checks 始终是独立字段。
  4. 资料命中进入处境指纹，所以「同一批资料同一版本」保持安静；导入新资料或资料换版会重新问一次。
  5. 夹具与日志仍在 `/tmp/nyairo-v04-research-fixtures` / `/tmp/research-*.log`，本阶段不清理。

## Verification

* 新增 `tests/test_v04_research_life_stage.py`：17 条（14 纯逻辑 + 3 真实 owner 链），`OK`，退出码 **0**
  （最新一次 `Ran 17 tests in 12.048s`，`/tmp/research-new.log`；加 excerpt 两条用例之前 15 条时为
  `Ran 15 tests in 9.349s`）。
* excerpt 缺口先红后绿：更强断言下 `RED_EXIT=1`（`/tmp/research-red.log`：
  `FINDING not found in '<!-- nyairo-research:v1 …'`）；修完精准方法
  （真实链条 + `ResearchExcerptTest`）`FIX_METHODS_EXIT=0`（`Ran 3 tests in 7.446s`，
  `/tmp/research-fix-methods.log`）。
* 直接受影响的既有链 43 条：`tests.test_v04_autonomous_day_stage`、`tests.test_model_continuation_no_change`、
  `tests.test_day_life_quiet_runner`、`tests.test_day_life_decision_legality` → `OK`，`AFFECTED_EXIT=0`
  （`Ran 43 tests in 8.002s`，日志 `/tmp/research-affected2.log`）。
* 真实 owner 链一条到底：真实 `LifeSupplyService`（自己的 Unix socket）+ 真 `import_material` + 真
  `mark_unclear` + 真 AG-1（**原始 JSON 校验**）+ 真 AG-2 + 真 AR-0，只有决策回答与内容回答是脚本替身
  （`_ResearchCognition` / `_ScriptedModelClient`，文件里明示）：研究作品被建出（marker/question/真实
  source_refs/uncertainty）→ AR-0 写出第 2 版（marker 仍在，脚本模型那句独有发现真的进了 owner 文档）→
  写作 prompt 的 `research_context.own_research.sources[0]` 就是来源正文 + 版本 + scope/uncertainty →
  下一轮决策 `research_results[0].excerpt` 与普通作品 `prior_research[0].excerpt` **都包含那句独有发现**
  （且不含 `<!--`/`nyairo-research:v1`）→ 资料再提交一版变 `STALE`（且不给正文）→ 归档变 `UNAVAILABLE` →
  资料不进候选/收尾。
* 没有跑全库回归、没有跨进程 qualification、没有真实模型/密钥、没有动原实例；`PYTHONDONTWRITEBYTECODE=1`
  全程开启。本树上另有三处**当前失败、所在文件本次未被修改**的测试（`test_v04_private_projects_stage` 的
  artifact id 前缀断言、`test_life_wait_resume_v04` 的 park basis 断言、`test_day_life_continuity` 的
  `_world_cycle(snapshot=)` 调用），已在 `docs/engineering/v04-research-life-result.md` 记录，均未修，也不对
  它们的历史来源下结论。
