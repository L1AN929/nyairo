# World 目标窄诊断 · worker 报告

日期：2026-10-22。父级验收状态：**分派缺口已确认，历史故障根因仅部分定位**。本文件保留worker原始分析，以下过强结论由此批注限定：

- 父级独立复跑第6节命令exit0，确认单WORLD分支缺候选类型预检、该决定选私人机会候选、上下文observed_world_refs为空。没有独立证实该tick确实走此分支，实际target_refs也未知，不能称历史因果链完全闭合。
- 第96/108行附近的“不可能包含World前缀”不成立：构造允许追加合法extras，只有前两项非World不能排除extras；candidate_kind/source_kind本身也不能证明完整目标列表没有World目标。
- 多路径一定不可能报错、读候选纯内存即可排除一切读取变化、模型选择一定正确、except本次一定未触发，均缺足够历史证据，不采纳为确定结论。
- 加类型门只能防误送/诚实拒绝，不证明私人机会已有合法承接路径，也不证明修后能正常生活。未修改代码、未测试修复。
- 账本由父级本次直接读取确认仍12；加旧预检1，共同13/96余83。JSON中原“本工单未重读”与正文声称读过不一致，以父级独立记录为准。

worker称首个命中即落PARTIAL；最终报告与证据确已存在，但父级没有工具逐步轨迹，不独立确认落盘具体轮次。
授权范围：只读；未运行产品脚本、未调用模型、未改源码/测试/配置。

## 1. 报错入口（已确认）

`components/life/scripts/autonomous_life_cycle.py:6397-6399`，函数 `_world_step_spec(decision_record)`：

```python
candidate_ref = getattr(decision_record, "selected_candidate_ref", None)   # :6379
candidate = None
if candidate_ref:
    try:
        candidate = self.runtime.candidate_read.get_candidate(str(candidate_ref))  # :6383
    except Exception as exc:
        self.last_world_error = f"{type(exc).__name__}: {exc}"                # :6385
target_ref = ""
if candidate:
    for ref in candidate.get("target_refs") or []:                            # :6388
        text = str(ref)
        if (text.startswith(PLACE_REF_PREFIX)
                or text.startswith(WORLD_OBJECT_REF_PREFIX)
                or text.startswith(STAY_REF_PREFIX)
                or text.startswith(BODYACT_REF_PREFIX)
                or text.startswith(DAILYACT_REF_PREFIX)):
            target_ref = text
            break
if not target_ref:
    raise AutonomousLifeError(
        "the committed Decision %r names no observed World target" % (candidate_ref,))  # :6398-6399
```

已证条件：抛错唯一触发是 `target_ref == ""`，其来源只有两条互斥路径
(a) `candidate` 为假值 —— `candidate_ref` 空、或 `get_candidate` 返回 None/假值、或抛异常被 :6384 吞掉只写 `last_world_error`；
(b) `candidate` 为真，但 `candidate["target_refs"]` 为空或不含任何以
`place:` / `object:`(WORLD_OBJECT) / `stay:` / `bodyact:` / `dailyact:` 开头的元素。

检查字段：`decision_record.selected_candidate_ref` → `candidate["target_refs"]`（只读候选记录，不重新向模型索取）。docstring 明确「A Decision that names no observed target is a refusal, not a default」，即此处不回落重定向。

## 2. cand:a077fcadc6750011 的最短路由（已确认分派缺口）

两条进入 `_world_step_spec` 的路径不等价：

```python
# autonomous_life_cycle.py:5263-5265  —— 单路径：无条件绑定，无候选类型预检
world_step = None
if act and not has_foreground and self._world_step_enabled() and world_available:
    world_step = _DecisionBoundStepSpec(self._world_step_spec)

# :5292-5312  —— 分派
live_paths = [name for name, spec in (("AR0", ar0_step), ("READING", reading_step),
                                      ("CLOSEOUT", closeout_step),
                                      ("WORLD", world_step)) if spec is not None]
if len(live_paths) > 1:
    step_spec = _DecisionBoundStepSpec(
        self._routed_step_builder(resume_point, observed_at=obs, closeout=closeout_point))
elif live_paths == ["WORLD"]:
    step_spec = world_step          # ← 直接是 _world_step_spec，没有 _candidate_is_world 关
```

- 多路径分支走 `_routed_step_builder`（:6345-6352），先用 `_decision_chose_world`（:6342-6343 → `_candidate_is_world` :6324-6340，同一组 5 个前缀）筛过，只有 World 候选才会进 `_world_step_spec`，因此那条路不可能报本错。
- **只有 WORLD 存活时（`live_paths == ["WORLD"]`）直接绑定 `_world_step_spec`，没有任何候选类型预检**：AG-2 一旦提交 START，无论选中的候选是否带 `place:`/`object:`/`stay:`/`bodyact:`/`dailyact:` 目标，都会进 :6388 的循环；循环取不到 → :6397-6399 抛错。
- 这也解释了为什么分派与构建「读同一份候选却结论相反」：单路径下根本没有分派判断参与。

`get_candidate` 佐证不存在读取侧不确定性：`candidate_sources_ag0.py:2826-2828` 是 `self.materializer._candidates_by_id.get(id)` + `cand.to_dict()`，`to_dict` 为 `copy.deepcopy(dataclasses.asdict(self))`（:683-684），无消费、无 TTL、无裁剪。**"候选被读走/过期"的假设可排除。**

最短路径：world 单路径 tick → 模型提交 START（dec:f1ca026164e400f0，选 cand:a077fcadc6750011）→ `_DecisionBoundStepSpec.__call__`（:800-803）→ `_world_step_spec` → target_refs 无匹配前缀 → 抛错 → resident 记 BLOCKED / DECISION_FAILED、actions 空。

尚缺一环：该候选的 `target_refs` 实际内容，以及该 tick 的 `step_path` 是否确为 `WORLD_AR0`。段4 安全证据 `20261022-day-segment4-evidence.json` 未记录 `step_path` 字段（已读全文核对）。

## 3. 该候选当时的安全结构（实例只读）

`cand:a077fcadc6750011` 来自 `agency_decision_state.json` 的 `candidate_dispositions_by_decision["dec:f1ca026164e400f0"]`：

```
candidate_kind   CONSIDER_PERSONAL_OPPORTUNITY
source_kind      PERSONAL_OPPORTUNITY
source_ref       artifact:artifact:7d39dcca9d824c6583267b5141892199
disposition      SELECTED_FOR_START
executability    EXECUTABLE_NOW
reason_codes     CONTINUITY_PREFERRED, GOAL_STEP_ACTIONABLE
```

`candidate_audit_journal.jsonl` 同一 id 共 5 条 MATERIALIZED（20:10:00 / 20:51:57 / 21:39:46 / 22:11:02 各一条，20:10:19 另有 INVALIDATED→20:10:26 WITHDRAWN），source_kind 与 source_ref 一致——即该 id 是同一私人机会按 source 重建的稳定 id，22:11:02 的那次是第四次段真正使用的那份。

该源构造 target_refs 的方式（`life_supply_candidate_source.py:301`）：

```python
target_refs = [source_ref, opportunity_id]
for extra in item.get("target_refs") or []:
    if isinstance(extra, str) and _STRUCTURED_REF_RE.fullmatch(extra) and extra not in target_refs:
        target_refs.append(extra)
```

即首项固定是 `artifact:artifact:...`、次项是 `dopp:...`，**不含 `place:` / `object:` / `stay:` / `bodyact:` / `dailyact:` 任何一个**。

同一决策的 context snapshot（`dctx:dc98029544b013ee`）里 `observed_world_refs` 为 **空列表 `[]`**，`candidate_revisions` 只记录 5 个候选的 revision、不含 target_refs。候选集 `cset:a2792522b0070ad2` 在 state 各文件中只以 ref 出现，明细未落盘。

结论：这个候选是一个「继续私人事项」的机会，本来就不是 World 候选；模型选它是对的，错误发生在把它硬塞进 World 步构造。

## 4. 确定原因

**确定**：当该 tick 只有 WORLD 一条活路径时，`:5265` 把 `_world_step_spec` 直接绑成 build，绕过了 `:6342` 的 `_candidate_is_world` 预检；已提交的 START 选中一个 `PERSONAL_OPPORTUNITY`/`artifact:` 候选，`:6388` 在它的 target_refs 里找不到任何世界前缀，于是 `:6397` 抛错，整轮记 `BLOCKED / DECISION_FAILED`、actions 空。

**未知（不冒充已证）**：

1. 该候选 22:11:02 那次 materialize 的 `target_refs` 实际列表未落盘，只能由构造代码（`[source_ref, opportunity_id]` + 合法 extras）推断不会有世界前缀；没有逐个值的快照。
2. 该 tick 的 `step_path` 未记录在段4安全证据里；「走的是 `WORLD_AR0` 单路径分支」是与报错文本、`:5265` 结构结合的推断，不是日志直证。
3. `last_world_opportunities` 为何非空而 context snapshot 的 `observed_world_refs` 为空，未查——属于另一个问题，已越出窄范围。
4. `:6384` 的 `except Exception` 静默吞错路径本次未触发（`get_candidate` 是纯内存读），但不排除它会在别处掩盖真实读失败；未验证。

## 5. 最小修复方向（未执行、未验证）

给 `:5265` 的单 WORLD 分支补上和路由分支相同的 `_candidate_is_world` 门：单路径下也先判断提交的 Decision 是否选了 World 候选，不是就按「路由不匹配」拒绝，而不是交给 `_world_step_spec` 抛目标缺失。等价做法是单路径也走 `_routed_step_builder`。两种都未实现、未跑测试。

## 6. 验收命令（父级可复跑同一条，exit 0 已记录）

```bash
MSYS_NO_PATHCONV=1 wsl.exe -- python3 -c '
import json
repo="/mnt/d/nyairo-rc1-body-backend/"
base="/root/nyairo-day-20261022/one-day/life/state/"
src=open(repo+"components/life/scripts/autonomous_life_cycle.py",encoding="utf-8").read().splitlines()
for a,b,tag in ((5132,5134,"world_available"),(5262,5266,"single-world-bind-NO-PRECHECK"),(5291,5313,"dispatch"),(6342,6344,"routed-precheck"),(6386,6400,"raise")):
    print("==",tag,"lines",a+1,"-",b)
    for i in range(a,b):
        print("%4d| %s"%(i+1,src[i].rstrip()))
s=json.load(open(base+"agency_decision_state.json",encoding="utf-8"))
d=s["decisions_by_id"]["dec:f1ca026164e400f0"]
disp=s["candidate_dispositions_by_decision"]["dec:f1ca026164e400f0"]["cand:a077fcadc6750011"]
ctx=s["context_snapshots_by_id"][d["context_snapshot_ref"]]
print("== DECISION", d["decision_id"], d["decision"], "SELECTED", d["selected_candidate_ref"])
print("== CAND", {k:disp.get(k) for k in ("candidate_kind","source_kind","source_ref","disposition","executability")})
print("== OBSERVED_WORLD_REFS", ctx.get("observed_world_refs"))
print("== CAND_TARGET_REFS_PERSISTED", "NO (candidate detail lives only in runtime materializer)")
'
```

实测 exit 0，53 行，输出存 `.agents/worker-runs/20261022-world-target-narrow-acceptance.log`。

## 7. 边界与账本

- 全程只读：未调用网关/模型，未运行 status/run/verify/prepare/evaluate/serve，未起 owner/runtime/浏览器，未 source 密钥。
- 读实例只用了 `agency_decision_state.json`、`candidate_audit_journal.jsonl`、`life_request_budget.json`，只打印 id / kind / ref / 键名，未输出正文、prompt、completion、reasoning。
- `life_request_budget.json` 读得 `run:one-day.calls = 12`，与段4一致，本工单无新增产品请求。
- 写集只含本报告与 `.agents/worker-runs/20261022-world-target-narrow-{evidence.json,acceptance.log}`；未改源码、测试、notes、NOW/current.json、memsearch、实例数据，未 stage/commit/push。
- Memory UNBOUND、understanding UNKNOWN、24h NOT_RUN_BY_USER_SCOPE、EP-09 跨备份恢复 FAIL 均未触碰。
