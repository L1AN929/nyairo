# Agent Note: 生活 status 的前台与阻塞取证改读权威

Status: implemented

## Problem

`one_day_life.py` 的 `classify_state()` 用 `resident_summary.json` 的 `last_tick.due` 判
WAITING/BLOCKED，但这个字段从来没落过盘：`autonomous_life_resident.summary()` 只写
`last_tick_at`，wake 记录本身也没有 `material`。于是两条分支恒不可达，非暂停、非运行、
非停机的实例一律落进 IDLE，basis 却硬编码成
`"no pause, no open Activity, no blocked window in the last record"`——其中「open Activity」
与「last record」两件事都没读过任何权威。

真机现场（`/root/nyairo-day-20261022`）因此出现矛盾：status 报 `IDLE / no open Activity`，
而 `canonical_life_state.json` 的 `foreground_activity_ref` 是 `actv_91acbd742551228b`
（PAUSED）。下一段真实 run 加载的就是它。诊断与证据见
[v04-g1-activity-surface-20261023-diagnosis.md](../../../docs/engineering/v04-g1-activity-surface-20261023-diagnosis.md)。

## Decision

只改读侧，`status` 的分类证据来自两份既有权威，不再读 summary 里不存在的字段：

- **前台 Activity**：`activity_view(state_root)` 直接解析 state root 下的
  `canonical_life_state.json`。`foreground_activity_ref` 非空且该 Activity 的 status 属于
  `ACTIVE / PAUSED / WAITING` 时，`state_class = WAITING`，basis 写出 ref、status 与
  canonical revision。ref 为空是「权威已读、确实没有前台」，可判 IDLE。
- **最近阻塞窗口**：`recent_windows(state_root)` 从 `resident_journal.jsonl` 的有界尾部
  （默认 64 KiB / 40 条，首行若是半条记录就丢弃）取窗口，反向找**最后一条真正考虑过事情**
  的窗口（跳过 `status_class == "silence"`，即 NOT_DUE/QUIET/operator pause 静默）；
  它若为 `blocked` 才判 BLOCKED。末尾静默既不是动作，也不是恢复。
- **未核实**：文件缺失、JSON 损坏、不可读、ref 指向不存在或已非 open 的 Activity，分别给出
  `CANONICAL_STATE_ABSENT / CANONICAL_STATE_CORRUPT / CANONICAL_STATE_UNREADABLE /
  FOREGROUND_ACTIVITY_MISSING / FOREGROUND_ACTIVITY_NOT_OPEN`，basis 明确写「未核实，
  本视图不声称没有打开的 Activity」。`STATE_*` 六个既有取值不变，不新增枚举。
- **优先级不变**：life pause bit → PAUSED；无 resident 记录 → STOPPED；running → RUNNING；
  之后才是 BLOCKED 与前台。`classify_state(summary, pause_view)` 两参调用继续可用，
  这类调用方未提供读取，basis 如实写明「本调用方未读 Activity 权威」，不做任何前台断言。

`status()` 把这两份读取传进去；它自身仍然不建目录、不建表、不建锁、不开服务、不写库。

## 读取边界

activity 读取刻意**不走** `ActivityReadService.get_current_life_view()`：该方法经
`CanonicalActivityStore.load_verified_state_and_journal()` 取 store 的 IO 锁，会先
`run/` mkdir 再建 `.store_io.lock`。status 是只读视图，不能因为看一眼就在未初始化实例上
造文件。这里用纯文件读（`Path.read_text`），只提取 `revision`、`foreground_activity_ref`
和对应 Activity 的 `status/activity_kind`，不复制正文，不输出私文。journal 读取只取尾部，
带 `complete` 标记：尾部被截断时 basis 只说「读到的尾部里没有真正考虑过的窗口」，不说
「整本 journal 里没有」。

## Alternatives considered

1. **B：往 `resident_summary.json` 补一份有界 `last_tick`（`at/kind/status/status_class`）。**
   最强理由：分类只要读 summary 一处，BLOCKED 与前台都能在同一文件里判断，读者最简单。
   否决：要改落盘格式并扩 writer，等于再落一份第二事实源；而 Activity 的真实权威本来就是
   `canonical_life_state.json`、阻塞事实本来就在 journal 里，纯读现有接口已足够。多一份
   摘要就多一处与权威不一致的机会。
2. **新增 `STATE_UNKNOWN` 枚举值。** 最强理由：未知就是未知，一个 IDLE 标签天然会被误读。
   否决：`state_class` 是对外可见的 status 字段，文档约定就是六个取值；新增枚举属于需要
   父级拍板的契约变化，本轮停止条件明确包含它。改由具名 reason code + basis 承担「未核实」，
   并在 basis 里显式否认「没有 Activity」。
3. **复用 `get_current_life_view()`**（诊断曾建议）。否决理由同上：它会在只读路径上取锁、
   建 `run/`；而且 store 对损坏/未初始化状态会抛 `RecoveryRequiredError`，把它当成 status
   的读取入口会把「看一眼」变成「触发恢复」。
4. **在 `state` 返回里新增结构化 `activity` 子字段。** 最强理由：未知与否可被机器区分，
   不必解 basis 字符串。否决：本轮只修读侧语义，保持 `state` 三个既有 key 不变，减少对外
   面（CLI/验收脚本）的形状变化；reason code 已经写在 basis 里。
5. **不动，接受「进程口径」的解释**（IDLE 只表示无暂停位、无运行进程）。诊断已否决：
   函数 docstring 与保留的 WAITING 分支都表明设计意图是看 Activity。

## Consequences

- 同一份 PAUSED 前台现在会以 `WAITING` + ref + `status PAUSED` 出现在 status 里，不再被
  说成「no open Activity」；ACTIVE/WAITING 前台同样以 WAITING 呈现，具体状态在 basis。
- 静默窗口不再抹掉更早的真实 blocking 事实；只有后续**真正考虑过事情**的窗口（choice/
  acted/unknown）才取代它。这会让实例在长时间无 tick 时仍报 BLOCKED——这是有意的。
- 权威不可读时，`state_class` 落在既有的 IDLE 桶里，但 basis 首句就是未核实与 reason code，
  且永远不出现「no open Activity」。要机器区分未知，需另立契约（见备选 2），本轮不做。
- `timeline()` 仍以两参调用 `classify_state`，它的 basis 现在会写明「未读 Activity 权威」，
  不再假装读过；把它接上读取属于后续独立改动，不在本单。
- 链接：[跨进程重启夹具](../testing/2026-10-17-cross-process-life-restart-harness.md)
  仍依赖 `state_class == PAUSED` 的优先级，本改动保留该语义。

## Verification

- `tests/test_g1_state_surface.py`（新，6 例，WSL `/tmp/rc104-venv`）：PAUSED 前台不再 IDLE、
  ACTIVE/WAITING 前台可见、权威已读且 ref 空是合法 IDLE、缺失/损坏/不可读具名 unknown 且
  status 前后目录快照逐项一致（无 run/、无 lock）、journal 末段 blocked + 尾随静默仍 BLOCKED、
  blocked 之后真的有 acted 才不再 BLOCKED、暂停/停止/运行优先级与两参兼容、status 既有 key 不删。
  fixture 是按真实落盘形状手写的文件替身，不是真实 owner/模型，不构成 G1 通过。
- 笔记门禁：tree / format / archive 三线。
- 现场：对 `/root/nyairo-day-20261022/one-day` 跑只读 `status`，核对权威 PAUSED 前台可见，
  以及 canonical revision、ledger 13、Memory 绑定、无活服务/FREE 前后不变。
