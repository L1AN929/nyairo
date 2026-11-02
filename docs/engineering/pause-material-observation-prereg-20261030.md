# 材料变更后的单次暂停选择观测：预登记草案

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

日期：2026-10-30。当前状态：World与身份隔离收尾后，父级新模块19例31.749秒OK/exit0。21:28:45准备前root/authority均ABSENT、相关产品Python匹配空；首次prepare于21:29:10.843 CST成功并立即报监护。22:30再次audit exit0：prepared、started_at=null、window absent、ledger0，history141/cap20/1800秒/01:00与模型参数匹配，无operator材料或对照。真实root/run已经创建，尚未source或启动run，等待监护侧并发预算确认；不重复prepare。旧测试少数断言的worker结果不作为父级独立旧行为证据，早期未创建/阻点文字按历史保留。

拟root `/root/nyairo-pause-observe-20261030-d`，独立authority `/root/nyairo-pause-observe-20261030-d-memory-authority`；run `run:pause-observe-20261030-d`，域 `pause-observe:20261030-d`。这些只是拟定值，尚未核路径是否存在；若占用不覆盖或换名追成功。

硬cap20，Decision/内容/失败共享持久新增预留；历史保守141/300，最坏161/300，失败不退、不续跑、不补第二次。1800真实秒且2026-10-31 01:00 CST更早截止；World gate保持off，不注入事项、不强制行为、不碰任何旧root/a/b/c。沿原custom、https://gw.example.invalid/v1、cn:deepseek-v4.1-flash，温度0、非stream、决策4096/内容2048，无seed/top_p；不新增供应商/读取或输出密钥。本草案不批准source或实际调用。

## 目的与四项判定（跑前固定）

本次不测G1、不测完成一件事，只观察新材料下的选择，不把三批旧输入当对照。

1. 是否实际到达「要不要暂停」的选择点。必须有本轮候选/合法集和正式记录支持；若根本未出现，整次结论NOT_COVERED，不是失败/成功。若出现也只报告观测到达，不称文案效果。
2. 若PAUSE，记录绑定条件的已存合法类型/引用，是否对应同一作品与基线；缺失UNKNOWN。同目标仅证明绑定，不证明只有主体能写或条件永不满足。
3. ACTIVE时ABANDON是否在实际可选动作集、是否产生正式ABANDON/真实采纳；可选不等选择，选择不等采纳。没记录不倒推。
4. 新增事实句与恢复出来的完整规则尾段是否进入**同次最终结构化请求**，不是只验证view或中间白名单。现有仪器不记录该文本，当前此项UNKNOWN，不能承诺本草案能直接测出；见阻点。

无论暂停/没暂停，都不能当修好/修坏。暂停视图（新增事实句+恢复完整规则尾段）和每轮静态底稿同时变化，多因素不能归到一项；输入变了，行为是否变、为什么变，UNKNOWN。模型仍可合法暂停/静默，不能强求出样本。

## 开跑前两个阻点

- 既有c候选观测：`scripts/g1_real_adoption.py` 的 `_trace_pause_condition` 只ID/枚举/布尔、不碰自由文本，`record_prompt`只保存同次最终候选及发送状态。它不足以证明第4项，send_status本身仍可能UNKNOWN。不新增观测仪器的原限制与第4项直接证明冲突。已向监护报：坚持零新仪器就接受第4项UNKNOWN；若必须证明则需明确另批同次结构固定短句存在性观测，不保存prompt，此项当前未实施。
- 既有G1入口封闭a/b/c身份，并含operator对照材料、暂停/重启及UNKNOWN/CAS注入。本次是非G1、不注入的自然观测，不能原样套新d。最小实验入口适配或其他已有合适入口尚未获确认，不扩大到生产规则/新观测仪器。

## 监护最新裁定与路线待点头

监护已确认身份参数，并明确放行**一个**固定布尔：最初单布尔提议已被监护更新为**两个独立布尔**：`fact_sentence_present`与`tail_present`，只用两条编译期固定文本逐字核对同次最终结构，不存prompt/片段/长度/上下文/其他字符串、不正则或关键词扫描；与send_status同条记录，各自只证明那条文本存在，不证明发送或理解。最终结构/how不可取时两者UNKNOWN/null；可取时各自true/false，无basis允许true/false，不再把两者混成一个结果。此项取代上文第四项只能UNKNOWN及后续单布尔提议，不扩大其他仪器。

父级选择监护给的第二路线，监护随后已明确『发吧』；唯一scripts施工bsjd7q0xe/300轮3600秒在途，真实根尚未创建：在既有scripts/g1_real_adoption.py增加封闭d身份与独立自然phase，沿用账本/截止/候选记录，a/b/c保持原样；d绝不创建operator对照、不装SubmissionInjector、不暂停重启、不CAS/UNKNOWN注入，不采用G1判据。day_capability_probe现有硬上限600秒/6次且prepare固定材料，不能原样满足本次1800/20；不为此次改它的生产默认。

现场仅test-e/test-L核新root和authority均ABSENT；这是本次读取时点，不是未来占用保证。尚未prepare/派工/source密钥。前一材料单已收口，这个草案不重开它；余159不是自动支用授权。

## 实现已落盘（2026-10-30，scripts 施工交付，未运行）

上面第 1—31 行是**历史**：身份未定/单布尔/阻点全都按当时记录保留，不倒写成"一开始就批准过"。

- 代码：`scripts/g1_real_adoption.py` 追加封闭 `--batch d`（`BATCH_PROFILES["d"]`：
  root `/root/nyairo-pause-observe-20261030-d`、独立 authority `…-d-memory-authority`、
  `run:pause-observe-20261030-d`、`pause-observe:20261030-d`、历史 141、
  截止 `2026-10-31T01:00:00+08:00`）。a/b/c 的 profile、函数、记录字段与 phase 一个都没有改；
  `offline-check --batch d` 直接拒绝（那 8 段检查整体按 G1 的 prepare/p1/p2 写，对 d 无意义）。
- d 的独立路径：`prepare` 只走 `demo.prepare_demo(seed_work_artifact=False, reading_title="",
  reading_content=None)`，**不启动 Supply、不调用 `_provision_operator_material`**，
  因此没有 `MATERIAL_TEXT`/`CONTROL_TEXT`、没有 `mark_unclear`、没有人工 later 事项；
  prereg 里 `operator_material`/`operator_control` 均为 `null`，另记
  `observation.{mode, natural_phase, operator_material_provisioned, control_created,
  injector_constructed}`。
- d 的唯一 phase 叫 `natural`（`_phase_natural`）：真 owner/正式 runtime/正式
  `evaluate_once`（离线）或 `runner_tick`（真实），从第一轮自然走到 cap 20／窗口／发送异常；
  **不构造 `SubmissionInjector`**、不设 operator pause、不做两 PID 恢复、不要求先写出首版。
  `coverage` 恒为 `{}`，`result` 只在 `OBSERVED`/`NOT_COVERED` 之间取，永不出现 `COVERED`。
- 两个布尔：`D_PAUSE_FACT_SENTENCE`/`D_PAUSE_TAIL` 是编译期常量，`_pause_material_booleans`
  只读**同一次** `build_prompt` 返回的 `situation_context.pause_condition.how`，做两次逐字
  包含判断；缺块/`how` 非字符串 → 两个 `null`。两个字段与 `send_status` 写在同一
  `PROMPT_FINAL`（或 `PROMPT_BUILT_NOT_SENT`）行；a/b/c 的 trace 不带这两个键。
  生产 `how` 经 `_bounded_pause_condition` 的 680 字符上限后：有 basis 的 538 字两句都在，
  无 basis 的 326 字只有事实句——所以"有 basis=true/true、无 basis=true/false"是真实形状，
  不是构造出来的样本。
- 事后摘要 `_d_trace_observation` 只数 d 自己的 trace 行：`pause_choice_opportunity`
  （有请求时才知道，零请求是 `null`）、`pause_condition_offered`、`abandon_in_legal_set`、
  `pause_chosen`/`abandon_chosen`、逐条 `prompt_rows`（send_status + 两个布尔）。
  条件与作品的同作关系 trace 不携带，固定记 `pause_condition_same_work: "UNKNOWN"`，不逆哈希。

### 离线界限与一条具名 STOP

- 验收命令（worker 与父级各跑一次同一模块）：
  `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_pause_observe_d`
  → 17 例 OK、17.8 秒（WSL）。覆盖 d 身份/截止/历史、错批与错 phase 拒绝、cap 20 停发、
  同根不重跑、`_provision_operator_material` 与 `SubmissionInjector` **被打成 raise**
  （d 的路径从不调用它们）、两布尔的 exact/factonly/neither/missing 与同行/脱敏。
- **STOP（材料不够，不许注入）**：在这份"无材料 + 本入口不启动 World"的装配下，
  自然 phase 每一轮都返回 `NO_MATERIAL`，**零模型请求、零 Decision**（离线实测 4 轮全如此；
  `assembled["world"]` 是 `WORLD_NOT_READY`，因为入口只读 `WORLD_BODY_SOCKET`，而该网关
  没有任何组件在这里启动）。真实 run 若同样没有候选，整批就是 `NOT_COVERED`、
  两个布尔一条都不会落。按任务书"材料不够必须注入才跑即具名 STOP"，本单**没有**为了出样本
  去加材料/任务/World 启动；真实 run 是否先起 World 服务由父级裁定。
- 旧测试的红是预期的：`tests/test_g1_real_adoption.py:327` 与 `tests/test_g1_batch_c.py:72`
  都硬编码 `set(BATCH_PROFILES) == {"a","b","c"}`；加入 d 后这两条会红（其余 30 例通过，
  已实跑确认）。按本单要求不为了让旧绿改这些断言——它们记录的是历史三批范围，
  升级留待以后单独决定。这也意味着 a/b/c 的 `offline-check` 现在会因为
  `_run_unit_tests` 里那一条断言而报 FAIL，不是行为回归。

## 收尾交付（2026-10-30，d 两项具名缺口）

### 1) 旧 profile 集合与 d 的独立注册

- `BATCH_PROFILES` 恢复为原三批 `{"a","b","c"}`，三批的值一个字段都没改；
  `D_PROFILE` 是模块级独立常量，`MODE_PROFILES = {**BATCH_PROFILES, "d": D_PROFILE}`
  是运行期唯一可选表。`_select_batch`、`_check_root` 的"另一批"判定、CLI 的
  `--batch` choices 都走 `MODE_PROFILES`/`BATCH_CHOICES`，不再把 d 塞回旧集合。
- 旧硬编码断言不需要改：`tests/test_g1_batch_c.py` 的
  `set(BATCH_PROFILES) == {"a","b","c"}` 与 `_select_batch("c")` 两条已实际复跑通过；
  旧测试文件未改动。本模块新增反向断言 `d_is_registered_outside_the_original_profile_set`
  钉住"旧集合仍三批、d 不在里面、CLI 仍允许 d"。
- 因此早先"加入 d 会让旧批 offline-check / 旧枚举断言必红"的停点已解除；
  旧批 `offline-check` 的失败史按历史保留，不倒写。

### 2) d natural 起标准 World

- `_start_world` 复用 `components.phone.demo.owner_children(...)["world"]`：同一实例根、
  同一 world home/package、同一日志。gate 显式传 `off`，父 shell 的 `canonical` 不继承；
  旧 generation 的 `gateway.sock` 只由 `demo.retire_socket` 改名保留，不 unlink。
  socket 就绪（`wait_ready(..., probe="gateway")`）之前不装配 runtime；
  runtime env 的 `WORLD_BODY_SOCKET` 还必须等于这个 live child 的 socket，否则拒绝。
- `_stop_world` 用标准 `ChildService.stop()`（SIGTERM→有界等待→SIGKILL），
  不删 socket，只核 `socket_live_after=false`；`finally` 顺序是
  runtime → world → supply，异常/到 cap/自然停止都一样。
- 装配层的一个既有顺序问题由 scripts 层最小处理：`assemble_autonomous_life` 先构造
  cycle、之后才 `resolve_world_provider`，因此 cycle 拿到的是 `None`。d 路径在装配后把
  **同一个已经真实读回 READY 的 provider** 接回 cycle（`world_feed=True`），不新建
  provider、不改 `components/`；a/b/c 不传这个开关，行为保持原样。
- phase 报告新增安全元数据（无正文）：`world = {started, pid, socket, gate, ready,
  status, reason}` 与 `close_world = {pid, exit_code, socket_path, socket_exists_after,
  socket_live_after, graceful_stop_line}`；`observation` 里新增
  `world_candidates_per_request`（每次请求里 `source_kind=WORLD_OPPORTUNITY` 的候选数，
  零请求时为空列表）。没有新增 prompt 仪器，两个布尔与 `send_status` 语义未改。

### 3) 离线现实结果与未验

- `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_pause_observe_d`
  → **19 例 OK**（约 39 秒，WSL）。其中自然链两轮真实请求：
  `world.status="READY"`、`world.gate="off"`、`world_candidates_per_request=[4,4]`；
  两行 `PROMPT_FINAL` 的 `send_status="UNKNOWN"`，布尔分别是
  `fact=true/tail=false`（无 basis）与 `fact=true/tail=true`（有 PAUSED Activity）。
  World 停止后 `socket_live_after=false`。
- 未验：真实模型在真实环境里的自然选择（离线 Decision/内容仍是脚本替身）；
  真实运行时刻该装配是否同样有世界候选（离线夹具时钟是"当前 + 400 分钟/轮"，
  不是自然等待，也不是对真实时点的断言）；a/b/c 未重跑整套旧 suite，
  只按任务要求复跑了上面两条既有断言。

### 4) 当前命令（父级在 fresh 核后执行；本单不代跑）

```bash
# 不在 offline-check 里跑 d（那 8 段按 G1 的 p1/p2 写）
python -B scripts/g1_real_adoption.py offline-check --batch a      # a/b/c 的既有检查
python -B scripts/g1_real_adoption.py prepare --batch d \
    --root /root/nyairo-pause-observe-20261030-d
python -B scripts/g1_real_adoption.py audit --batch d \
    --root /root/nyairo-pause-observe-20261030-d
python -B scripts/g1_real_adoption.py run --batch d \
    --root /root/nyairo-pause-observe-20261030-d --execute-approved --capture-candidates
```

`--capture-candidates` 是 d 的两个布尔能否落行的前提；prepare 不产生 operator 材料、
不启动 Supply/World，世界只在 natural phase 起来。首次 prepare 前须现场确认 root 与
`…-d-memory-authority` 都 ABSENT；已存在就停，不覆盖、不换名。
