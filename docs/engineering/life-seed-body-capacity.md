# BODY-CAP-1 使用说明：真实 Body 读视图 → 有限容量（窄只读适配器）

- 代码：`components/life/scripts/body_capacity_source.py`（适配器本体）；
  定向测试：`tests/test_body_capacity_source.py`。
- 它只做一件事：把**既有**的 World/Body 只读读口（`get_body_signals`）翻译成 AG-1
  `CapacityEvaluator` 接受的那一对 `body_capacity_state` / `body_capacity_ref`。
- 它**不做**：不写内态、不建第二 Body 库、不发 Candidate、不授权任何动作、不改
  Canonical schema、不自定阈值、不碰 cycle/assembly/resident/runner/World/Supply/Memory。
- 本卡是**隔离适配器**：未接线到 cycle。接线由单一维护者在 W05R 写者停稳后合并（施工单
  BODY-CAP-1「后续集成」）。
- 平台：本模块本身可被任意平台 import；真实读路径与测试需要 POSIX（unix socket +
  `fcntl`，见 `body_runtime.py:33`、`activity_continuity.py:15`），实测在 WSL `/usr/bin/python3` 3.12.3。

## 1. 实际读路径（每一跳都是既有 owner）

```
body_runtime.initial_runtime / advance        components/world/scripts/body_runtime.py:517,736
  → WorldBodyService.body_summary / _moment   components/world/service/world_body_service.py:574,158
  → GatewaySurface.get_body_signals           components/world/service/world_body_gateway_api.py:390
  → unix socket（命令 get_body_signals）        components/native/integration/world_body_client.py:247
  → WorldBodyReadPort.body()                  components/native/integration/world_body_read_port.py:76
  → body_capacity_source.read_body_capacity
```

- 端口本身**构造上只读**（`world_body_read_port.py:14-16`）：`submit_action` 从这里不可达。
- 适配器每次调用**只发一次读**（`port.body()`），线上只出现一条 `get_body_signals`；测试用真实
  socket 记录的命令日志断言这一点。
- 读口真实返回（`world_body_gateway_api.py:409-418`）：`available`、`recovery{state,mode,trend}`、
  `latch`、`signals[]`；每条信号含 `signal_type`、`qualitative_band`、`occurred_at`、
  `source_body_version`、`runtime_version`、`body_id`（`body_signal.py:179-194`）。

## 2. 接口

```python
read_body_capacity(port, *, home, subject_id=None, now=None,
                   expected_body_id=None, expected_revision=None,
                   world_scripts_dir=None) -> dict          # 单次读
ag1_capacity_kwargs(view, *, unknown_capacity_policy=None)
    -> {"body_capacity_state", "body_capacity_ref"}         # 策略只在显式给出时附带

BodyCapacitySource(*, port, home, subject_id=None,
                   expected_body_id=None, expected_revision=None,
                   world_scripts_dir=None)
    .read(*, now=None, expected_revision=None) -> dict
    .ag1_capacity_kwargs(view=None, *, unknown_capacity_policy=None) -> dict
    .last_view
```

**AG-1 对 UNKNOWN/LIMITED 的处理（接线前必读）**：`CapacityEvaluator.evaluate_capacity`
（`agency_decision_ag1.py:1956`）的 `unknown_capacity_policy` 默认 `ALLOW_WITH_UNKNOWN`（:1971）——
`LIMITED` → `EXECUTABLE_WITH_LIMITS`（:2044-2045，无阻断理由）；`UNKNOWN` + 默认策略 →
`EXEC_UNKNOWN`（:2050-2051，候选保持 open，:2060）；只有显式 `BLOCK_ON_UNKNOWN` 才会变成
`EXEC_NOT_NOW` + `CAPACITY_UNKNOWN_DEFERRED`（:2047-2049）——**那才是永久 DEFER，而且它来自调用者策略，
不是身体事实**。空信号集（`NO_BURDEN_EVIDENCE`）是这个适配器正常的诚实答案，所以不要因为它而配
`BLOCK_ON_UNKNOWN`。`ag1_capacity_kwargs` 因此只在显式传入时附带该策略（非法值抛
`BodyCapacitySourceError`）。

**可复用公开助手**（本包其余读视图共用同一规则，不复制）：`bind_home_ref`、`parse_instant`、
`require_instant`、`require_structured_ref`、`resolve_subject_id`（下划线旧名是同名函数的别名）。

`port` 就是既有 `WorldBodyReadPort`（只用到 `port.body()`）。

### 返回字段（每条返回路径形状相同）

| 字段 | 含义 |
| --- | --- |
| `schema_version` / `kind` | `chiyo.life.body_capacity_view.v1` / `body_capacity_view`（**非** Canonical schema） |
| `source` | `world_body_read_port.get_body_signals` |
| `subject_id` / `home_ref` | 经 owner 校验的 subject；`home:<sha256 前12位>`（不含路径） |
| `source_outcome` / `degraded` | 端口原始 outcome 词表（`OK`/`NOT_READY`/`UNAVAILABLE`/`TIMEOUT`/`FAILURE`/`INVALID`），不折叠 |
| `freshness` | `body_reader` 词表：`fresh`/`stale`/`unverified`/`unavailable` |
| `observed_at` / `observed_body_id` / `observed_revision` | 来源观察时刻、`body_id`、`bodyrev:<version>` |
| `observed_recovery_state` | 来源 `recovery.state` 原样（仅溯源，不参与映射） |
| `identity_bound` / `revision_bound` / `observation_time_bound` | 这三件事到底绑上没有（无信号时全 false） |
| `body_capacity_state` | `AVAILABLE`/`LIMITED`/`UNAVAILABLE`/`UNKNOWN`（AG-1 词表） |
| `body_capacity_ref` | `bodycap:<body_id>:bodyrev:<version>`；绑定不上时为 `null` |
| `reason_code` | 下面第 4 节的封闭码 |
| `evidence` | 触发判定的 `signal_type:band` 列表（排序去重） |
| `qualitative_only` / `raw_values_exposed` | `true` / `false` |
| `read_only` / `reads_performed` / `writes_performed` / `mutations_attempted` | `true` / `1` / `0` / `0`（常量，不是计数器） |
| `candidates_emitted` / `actions_authorized` | `0` / `0` |

## 3. 唯一映射规则

来源：`body_signal.SIGNAL_RULES` + `TREND_RULES`（`body_signal.py:46-91`），共 13 条，全部是
**偏离**规则，没有一条说「能」。适配器把这张词表**完整二分成 burden / non-burden**，测试断言
两者并集恰好等于 owner 词表（新增规则会让测试失败，而不是被静默忽略）：

- **burden（降低容量）**：`fatigue_noticeable`、`fatigue_high`、`activation_low`、
  `discomfort_mild`、`discomfort_noticeable`、`sensory_load_elevated`、`tension_elevated`、
  `recovery_interrupted`。
- **non-burden（不参与判定）**：`warmth_high`、`cold_tendency`（冷热不是能力证据）、
  `recovery_underway`、`tension_releasing`、`activation_recovering`（改善趋势，既不升也不降）。

判定：

| 条件 | 结果 |
| --- | --- |
| 绑定成功 + 新鲜 + 至少一条 burden 信号 | `LIMITED`，`evidence` 记下 `signal_type:band` |
| 绑定成功 + 新鲜 + 无 burden 信号 | `UNKNOWN` / `NO_BURDEN_EVIDENCE`（**不是** `AVAILABLE`） |
| 其余任何情况 | `UNKNOWN` + 对应 `reason_code` |

三条刻意结论（都有据，且被测试锁定）：

1. **band 永远不能变成硬停机**：`strong` 也只会得到 `LIMITED`。定性 band 不是能力事实。
2. **`AVAILABLE` 在当前合同下不可达**：合同里没有任何「我能」的正向信号；从「没有 burden 信号」
   反推健康，正是 Body 合同禁止的推断（`CHIYO-施工规划/05-身体与情绪现状勘察.md` §1
   「不能无信号就推出身体健康」）。
3. **`UNAVAILABLE` 在当前合同下不可达**：封闭词表里没有一条表示「做不到」；新鲜度不是能力事实。
   来源宕机一律 `UNKNOWN`（fail closed），不是「身体坏了」。

## 4. 失败与 UNKNOWN（封闭 `reason_code`）

| `reason_code` | 条件 |
| --- | --- |
| `OK_LIMITED_BY_BODY_SIGNAL` | 唯一成功路径（`LIMITED`） |
| `SUBJECT_REJECTED` | subject 被 owner（`activity_continuity.validate_subject_id`）拒绝 |
| `SUBJECT_OWNER_UNAVAILABLE` | subject 规则 owner 不可解析 → 不读、不猜 |
| `HOME_UNBOUND` | home 不是目录 / 为空 |
| `BODY_CONTRACT_UNRESOLVED` | `body_reader`/`body_signal` 不可解析 → 不自造新鲜度阈值 |
| `BODY_SOURCE_<OUTCOME>` | 端口 outcome ≠ `OK`（如 `BODY_SOURCE_UNAVAILABLE`、`BODY_SOURCE_FAILURE`） |
| `BODY_SOURCE_RAISED` | 端口读取抛异常 |
| `BODY_PAYLOAD_MALFORMED` | `data` 不是 mapping / 缺 `available` / `available` 非 bool |
| `BODY_STATE_NOT_PRESENT` | `available == false`（该实例还没有 body 状态） |
| `BODY_SIGNAL_SCHEMA_INVALID` | `signals` 不是 list / 缺 `signal_type` / band 不在 `body_signal.BANDS` |
| `BODY_SIGNAL_TYPE_UNCLASSIFIED` | 信号类型不在 owner 词表内（新版本来源）→ fail closed |
| `BODY_SIGNAL_RAW_VALUES` | 信号带 `body_signal.FORBIDDEN_SIGNAL_FIELDS` 里的键（如 `value`） |
| `BODY_IDENTITY_MISSING` / `BODY_SUBJECT_MISMATCH` | `body_id` 缺失或跨信号不一致 / 与 `expected_body_id` 不符 |
| `BODY_REVISION_MISSING` / `BODY_REVISION_MISMATCH` | `source_body_version` 缺失或跨信号不一致 / 与 `expected_revision` 不符 |
| `BODY_OBSERVATION_TIME_MISSING` / `..._INVALID` / `..._AHEAD` | `occurred_at` 缺失或不一致 / 不可解析 / 晚于注入的 `now`（回拨） |
| `BODY_FRESHNESS_UNVERIFIED` | 未注入 `now`（与 `body_reader.assess_freshness` 同语义） |
| `BODY_VIEW_STALE` | `now - observed_at` 超过 `body_reader.FRESHNESS_MAX_AGE_HOURS`（**复用**该声明值，不新增阈值） |
| `NO_BURDEN_EVIDENCE` | 有信号、绑定成功、新鲜，但没有 burden 证据 |

判定顺序即上表顺序（同一视图只带一个 `reason_code`）；先绑身份/版本/时刻，再谈新鲜度与容量。

## 5. 调用者要求（集成者必读）

1. **`now` 必须由调用者注入**（ISO-8601 或 `datetime`）。适配器内部不读墙上时钟，与
   `body_reader.assess_freshness` 一致；不注入只会得到 `BODY_FRESHNESS_UNVERIFIED`。
2. **`body_capacity_state` 与 `body_capacity_ref` 必须成对传**：`agency_decision_ag1.py:1977-1980`
   在「只给 ref 不给 state」时会**伪造 `AVAILABLE`**。用 `ag1_capacity_kwargs(view)`，不要手抄。
   同时**不要顺手配 `BLOCK_ON_UNKNOWN`**：本适配器正常会给 `UNKNOWN`，那会把候选永久 DEFER（见第 2 节）。
3. **`home` 与 `subject_id` 在装配期绑定**：`BodyCapacitySource(...)` 构造时就校验（非法 subject /
   不可用 home / 端口没有 `body()` 都会在这里抛 `BodyCapacitySourceError`），不要在循环里才第一次传。
   类路径下 `read()` 会复核 home 存在性；函数路径下 home 丢失返回 `HOME_UNBOUND` 而不是抛。
4. **不要为了补语义去多读一跳**：`get_body_signals` 顶层不带 `body_id`/`occurred_at`/body 版本，
   而 `get_status` 只有 `body_id`、没有 body 版本也没有观察时刻 —— 补不了这个洞，只会多一次往返。
   空信号集下适配器诚实地给 `UNKNOWN` + 三个 `*_bound=false`。要拿到 `AVAILABLE`，需要 Body owner
   先在 `get_body_signals` 顶层补上正向能力声明/观察时刻/版本，那是 Body 侧的改动，不是本适配器能造的。
5. **不要跨轮缓存 `view`**：适配器无缓存、无持久化、每次 `read()` 重新读 owner；重启语义就是「再读一次」。
6. **预算不是这里的输入**：额度/配额/token 属于 AG-1 的另一个维度（`resource_*`，
   `agency_decision_ag1.py:1964-1965`）。本模块签名里没有任何 budget 参数，返回里也不会出现
   budget 词汇（测试有负控）。
7. **变化不产生 Candidate**：本模块只返回视图；`candidates_emitted`/`actions_authorized` 恒为 0。

## 6. 示例（已实测可直接接入 AG-1）

```python
import body_capacity_source as bcs
from world_body_read_port import WorldBodyReadPort
import world_body_client as wbc

client = wbc.WorldBodyClient(socket_path="/srv/nyairo/profiles/<profile>/run/gateway.sock",
                             mode=wbc.MODE_CANONICAL)
source = bcs.BodyCapacitySource(port=WorldBodyReadPort(client),
                                home="/srv/nyairo/profiles/<profile>/world-home",
                                expected_body_id="chiyo_body")   # 装配期绑定

view = source.read(now=datetime.now(timezone.utc).isoformat())
kwargs = source.ag1_capacity_kwargs(view)        # 只给这两个键，成对

snapshot = ag1.CapacityEvaluator.evaluate_capacity(
    allowed_candidates=allowed,
    context_snapshot=context_snapshot,
    captured_at=captured_at,
    **kwargs,
)
```

实测结果（真实 `CapacityEvaluator`，见 `tests/test_body_capacity_source.py::Ag1Seam`）：
`body_capacity_state=LIMITED`、`overall_capacity_state=LIMITED`、`body_capacity_ref` 原样进入快照、
`candidate_executability` 为 `EXECUTABLE_WITH_LIMITS` 且 `candidate_remains_open=true`、
`blocking_reasons=[]` —— 容量只限制，不删候选、不产生动作。

## 7. 验证（本卡）

```bash
$ cd /mnt/d/nyairo-v03-backend          # WSL；本机原生 Windows 无 AF_UNIX，跑不了这套
$ PYTHONDONTWRITEBYTECODE=1 <隔离venv>/bin/python tests/test_body_capacity_source.py
...
Ran 54 tests in 9.433s
OK
$ echo $?
0
```

覆盖：真实 bridge 正例（真实 body_runtime 状态 → 真实 `WorldBodyService.body_summary` → 真实
`GatewaySurface` → 真实 unix socket → 真实 `WorldBodyClient` → 真实 `WorldBodyReadPort`）；
A/B 两种合法身体条件给出不同答案；同 Body 不同非身体输入不改变身体事实；线上命令恰为一条
`get_body_signals`；home 目录字节级零变化；重启重读 owner；旧 revision / 错 subject / 错 home /
陈旧 / 未注入 now / 时钟回拨 / 缺失 / 损坏 / raw 泄漏 / 未知信号类型全部 fail closed；
AG-1 词表与结构化 ref 正则漂移守卫；`CapacityEvaluator` 真实接入。新增 5 项（`Ag1UnknownPolicy`）：
策略镜像与 AG-1 源码一致、诚实 UNKNOWN 默认不 DEFER（`EXEC_UNKNOWN`、候选仍 open）、只有
`BLOCK_ON_UNKNOWN` 才 `CAPACITY_UNKNOWN_DEFERRED`、`LIMITED` 在两种策略下都是
`EXECUTABLE_WITH_LIMITS`、非法策略被拒且默认调用不多带键。

同一包内的处境读视图（`life_situation_view.py`，把 Body/预算/知识分列）在
`tests/test_life_situation_view.py`（29 项）；短期评价投影（`short_appraisal_view.py`）在
`tests/test_short_appraisal_view.py`（29 项）。两文件均由本包维护，接口与拒绝语义见
`docs/engineering/life-v1-a-result.md`。

未证项（不冒充）：未接 cycle（本卡不做）；未在真实部署的 gateway socket 上跑过；未证明
`AVAILABLE`/`UNAVAILABLE` 可由当前合同导出（结论相反，见第 3 节）；未验证真实模型行为、24h、
生产 profile。

## 8. 留给集成者的明确缺口

1. `get_body_signals` 顶层缺 `body_id` / `occurred_at` / body 版本：空信号集下无法绑定，只能 `UNKNOWN`。
2. 合同无正向能力信号 → `AVAILABLE` 不可达；若集成需要「明确可用」，须由 Body owner 补声明。
3. 合同无 `last_advanced_at` → 无法回答「body 状态是否长期未推进」（legacy 19.4 天冻结那类问题），
   本适配器只能回答「这次观察有多旧」。
4. 本卡不改 cycle 里现在用 round budget 当 capacity 的那段（`autonomous_life_cycle.py:743-753`）；
   替换成 `**ag1_capacity_kwargs(view)` 由单一维护者在该文件停写后做。
