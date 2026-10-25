# G1 状态面最小修复 · worker 结果（2026-10-23）

范围：只改 `one_day_life status` 的读侧取证（方案 A）。不改落盘格式、不新增 owner/枚举/
契约、不动 resident/cycle/phone/配置，不清理 PAUSED 遗留活动。产品请求 0，账本仍 13，
共同 14/96 余 82。

## 改动文件

| 文件 | 改动 |
| --- | --- |
| `components/life/scripts/one_day_life.py` | 新增 `CANONICAL_ACTIVITY_STATE_FILENAME`、`OPEN_ACTIVITY_STATUSES`、`JOURNAL_TAIL_BYTES=65536`、`JOURNAL_TAIL_RECORDS=40`、`SILENT_STATUS_CLASSES`；新增 `activity_view()`（纯文件读权威）与 `recent_windows()`（journal 有界尾部）；`classify_state()` 新增可选 `activity`/`windows` 关键字参数，删掉读不存在的 `summary.last_tick`，basis 改为陈述真实读到的证据；`status()` 把两份读取传入 |
| `tests/test_g1_state_surface.py` | 新，6 例；临时根均为唯一 mkdtemp，保留不清理 |
| `.agents/notes/implemented/bug-fix/2026-10-23-life-status-authority.md` | 新 decision note |
| `.agents/worker-runs/20261023-g1-state-surface-status.txt` | 现场只读 status 的安全摘要证据 |

写集之外无改动（`git status --short` 确认）。

## 具名方法事实

- `activity_view(state_root)`：读 `<state root>/canonical_life_state.json`（`Path.read_text`），
  不构造 `CanonicalActivityStore`，因此不 mkdir `run/`、不取 `.store_io.lock`、不建 lease。
  `foreground_activity_ref` 非空且该 Activity `status ∈ ACTIVE/PAUSED/WAITING` → `READ` +
  `foreground_status`；ref 空 → `READ` 且无前台；文件缺失/JSON 损坏/OSError（含路径是目录）/
  ref 缺失/状态非 open → `UNVERIFIED` + 具名 reason code。
- `recent_windows(state_root)`：从 `resident_journal.jsonl` 尾部最多 64 KiB 读、丢首行半条
  记录、最多取 40 条，返回 `status/records/complete/source`；文件缺失为 `ABSENT`，读失败为
  `UNREADABLE`。
- `classify_state()`：优先级仍为 pause → STOPPED → RUNNING；BLOCKED 取 journal 尾部反向
  第一条 `status_class` 非空且不是 `silence` 的窗口（即最后一条真正考虑过事情的窗口）；
  前台非空 → WAITING（basis 写 ref/status/revision）；权威已读且无前台 → IDLE（basis 只写
  已核实事实）；权威未核实 → IDLE 但 basis 首句具名未核实并显式声明「不声称没有打开的
  Activity」，全文不再出现 `no open Activity`。两参调用兼容，basis 写「本调用方未读」。
- `status()` 只多传两个读取参数；其余输出 key 不变。

## 实际命令与结果

| # | 命令 | 结果 |
| --- | --- | --- |
| 1 | WSL `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_g1_state_surface` | exit 0，Ran 6 tests OK |
| 2 | `npx tsx .../verify-agent-note-tree.ts` | exit 0，87 note(s) tree and relative links verified |
| 3 | `npx tsx .../verify-agent-note-format.ts` | exit 0，87 note(s) verified |
| 4 | `npx tsx .../verify-archived-agent-notes.ts` | exit 0，0 archived note(s) |
| 5 | WSL `... one_day_life.py status --instance /root/nyairo-day-20261022/one-day` | exit 0；`state_class=WAITING`，basis `an Activity is open: actv_91acbd742551228b (status PAUSED, canonical revision 15)`，source `canonical_life_state.json`，pause absent |
| — | `git diff --check`（限写集） | exit 0 |

现场前后只读对照（详见 `.agents/worker-runs/20261023-g1-state-surface-status.txt`）：
canonical revision 15→15、foreground `actv_91acbd742551228b` PAUSED→PAUSED、ledger
run:one-day 13→13、days 2026-10-22 13→13、Memory 三个环境名与两库文件存在、lease FREE→FREE、
state root 条目 31→31、无活服务/进程。

## 未验证项

- 新 fixture 是按真实落盘形状手写的文件替身（`canonical_life_state.json` 的
  `revision/foreground_activity_ref/activities[].status`、summary 的 `last_tick_at` 无
  `last_tick`、journal 的 `due.status_class`），**不是**真实 `CanonicalActivityStore` 写出，
  也不是真实 owner/模型；不构成 G1 任何场景通过。
- 未验证 `timeline()` 是否也应接同一读取：它仍两参调用，basis 会写「未读」，本轮按写集
  范围未改。
- 未验证其他外部读者是否会因 `state_class` 在「权威不可读」时仍落 IDLE 而产生新误读；
  basis 已具名，机器区分未知需另立契约（已在 note 备选 2 记录）。
- 未启动 resident/服务、未调用任何产品模型、未跑 24h、未复跑旧全模块 suite。
- G1 中断/接续仍未验；Memory 真实空库读取已证但未参与选择；V-C 恢复 FAIL、
  understanding UNKNOWN 边界不变。
