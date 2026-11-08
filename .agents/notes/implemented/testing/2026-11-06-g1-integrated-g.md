# Agent Note: G1 完整端到端的新封闭入口 g，复用同一条双 PID 链

Status: implemented

## Problem

`2026-10-30-g1-limited-real-adoption` 的 a/b/c 与 `2026-10-31-g1-next-offline` 的 f 已经
把「中断 → 跨 PID 重启 → UNKNOWN 对账 → CAS 冲突 → NO_ACTION → 继续」接在同一件真实
Supply 作品上，`2026-11-05-g1-pre-armed-unknown` 又把 UNKNOWN 注入提前到 P2 第一轮之前。
但这些入口的 profile 截止都已过去，而且 f 的 `_verdict` 是「锚续作」窄判据，明确
`ANCHOR_ONLY_LIMITED` / `real_g1_evidence=NOT_COVERED`。要回答的是完整 G1：所有门都要有
证据，特别是 **NO_ACTION 之后还要真的再续同一件作品、同一 Activity**；只扫「曾经
CONTINUE + 曾经 NO_ACTION」拼不出通过。

## Decision

新增封闭身份 `G_PROFILE`（`g1-integrated-g`）与 `scripts/g1_integrated_g.py`：

- 身份固定 root `/tmp/nyairo-g1-real-20261106-g`、独立 authority、run
  `run:g1-real-20261106-g`、域 `g1:real:20261106-g`、history 507、窗口 3600 秒、
  max_ticks 1000/trace 10000、最晚 23:30 启动、00:50 停止新请求。只进 `MODE_PROFILES`，
  旧 `BATCH_PROFILES`/`BATCH_CHOICES`/`CLI_BATCH_CHOICES` 一个值都不动。
- `prepare`/`run` 薄包装 `g1_real_adoption` 的 `prepare`/`start_run`；p1/p2 仍是
  两个不同 PID、同一本账本、正式 AG-1/AG-2/AR-0 与真实 Supply owner。Decision/内容在
  离线夹具里仍是有标注的脚本替身。`audit`/`status` 在收尾后改为**本入口纯读**：直接读
  prereg/batch/phase/运行报告与账本文件本体，不调用 `g1.audit`、`budget.view` 或
  `_load_locked`，不建根/锁、不推进窗口、不更新账本；旧 `g1.audit` 默认不动。
- `gate_projection` 把完整 G1 拆成 12 个独立布尔：P1 首次 owner 写、operator 中断、
  跨 PID 同根同 run、暂停轮零请求、A/actA 锚、UNKNOWN 第一轮前武装、UNKNOWN 确证对账、
  CAS 真 CONFLICT、NO_ACTION 零写、**NO_ACTION 后同作品新续作**、状态顺序、既有
  `_coverage_from_states` 完整。缺一项就 `NOT_COVERED`。
- g 的累计门是本批局部算术：507 + 已发 >= 1000 停；旧批仍不进入这个判断。g 的坏账
  `corrupt`/`UNREADABLE` fail-closed；gate 显式 `off`，不启动 World 服务，写面按实例
  `world_home` 隔离。
- 离线验收只跑 `scripts/g1_integrated_g.py offline-check`：8 个聚合 case + 一条正式
  双 PID 同根链；父进程与 phase/owner 子进程各装局部网络守卫，AF_UNIX 放行；不另跑旧
  offline-check 或新旧大矩阵。
- **终端安全收尾（2026-11-06 本人直接授权）**：所有 CLI stdout 经过递归白名单投影
  （固定枚举/已知形状 id/路径/计数/布尔），`detail`、`model_params`、嵌套 `window`、
  `recent_tokens`、材料正文/提示词不出终端；预算只留 `run_window_calls`/`utc_day_calls`/
  `run_window_max`/`utc_day_max`/`state` 五元，缺失或坏账为 `UNKNOWN` 且计数 `null`，
  不当 0；身份校验追加 caps 五个封闭值与 `world_intent_gate=off` 精确比对；测试收集与
  顶层异常只回固定 code + 异常类型，`_check` 失败只输出定型后的枚举/布尔/计数，
  `SystemExit` 透传；本收尾不改 G1 成功门、不改模型选择/quiet/资格/覆盖标准。

只改 `scripts/g1_real_adoption.py` 的 g 身份接线（profile 注册、gate/isolation/offline
守卫、账本/累计/audit/prepare/phase 相邻接缝）与新增入口、测试、预登记、本笔记。
`components/`、a/b/c/d/e/f 的 profile/CLI/输出形状与行为都不动。

## Alternatives considered

- **直接把 f 的 `_verdict` 改成完整门语义**：最强的理由是一处改动、f 的链已经跑通。
  否决原因：f 的窄判据是它那一批的已交付结果，改旧口径等于事后重写历史证据；新 g 另起
  投影，旧 f 保持原样。
- **另建一套完整仪器矩阵，每段单独 fixture 再拼 PASS**：最强的理由是覆盖最全。否决原因：
  这正是 G1 要排除的拼凑——各段之间没有同一根、同一作品的持久状态接力；复用现成双 PID
  链，只补投影层和负控。
- **在投影里只看 `_coverage_from_states.g1_chain_complete`**：最强的理由是现成字段最短。
  否决原因：它不直接摊开「UNKNOWN 是否第一轮前武装」「CAS 是否真 CONFLICT」这些具名证据，
  也无法单独反例；改为逐段布尔 + 状态顺序 + NO_ACTION 后新续作。
- **把 g 塞进旧 `BATCH_CHOICES` 让 `g1_real_adoption.py --batch` 直接认**：最强的理由是
  少一个脚本。否决原因：那会改变旧 CLI 的封闭集合；g 由自己的入口通过 `_use_batch` 进入，
  旧元组不动。

## Consequences

- 收益：完整 G1 有了一个可重复执行的封闭入口；P1/P2 精确同 A/actA、UNKNOWN 只消费一次、
  CAS 真冲突、NO_ACTION 后同作品新续作都有独立证据与反例。
- 代价与上限：仍是「有序停启 + 有界故障注入」，不是 crash/强杀矩阵；operator pause 与两处
  故障是人为干预，不冒自然发生；UNKNOWN/CAS 的注入各一次。离线绿不等于真实模型自然采用，
  `real_g1_evidence` 仍由唯一真实 run 决定；真实模型没选回原 A/没触发后半链就 `NOT_COVERED`。
- 累计上限 1000 对 g 生效但本 run 最坏 527，不会触发；它只是把「不要误用旧 300 累计门」
  写成代码事实。窗口 3600 秒与 00:50 绝对线取较早者，60 秒收尾冗余不拖过绝对线。
- 生产业务代码零改动；新增的是一个入口脚本、一个测试、预登记与这篇笔记。

## Verification

- `MSYS_NO_PATHCONV=1 wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/g1_integrated_g.py offline-check'`
  → `RESULT: PASS`，8 个聚合 case 全 PASS，exit 0。第一次跑时 `status` 用了默认预登记根，
  离线 scratch 被误拒；改为 `status(..., expected_root=root)` 后第二次全绿。另一次失败是
  p1/p2 没有把 `offline` 传给 `_start_supply`，owner child guard 未装；补上后 guard
  自检 `found>=2`、`ok=true`。随后把错 root / 别的批 prereg / 已占用 authority 三个
  拒绝并入身份 case（仍是 8 个聚合 case），复跑仍 `RESULT: PASS`、exit 0。
- 链路实测（最终复跑）：p1/p2 两个不同 PID 31669/31848、stop_reason
  `G1_CHAIN_COVERED`，projection `COVERED`、`missing=[]`；A 锚 `4a3b5601...` 与
  actA `actv_aa5bb7a9...` 精确一致，`unknown_fired=1`、`concurrent_fired=1`、CAS owner
  `CONFLICT`，NO_ACTION 后新 Action（不等于首个 Action `actn:02defee9...`）与新 owner
  版本，最终 `COVERED`；
  两个 Supply child 的守卫 AF_INET/INET6/DNS/connect 全拒、AF_UNIX 可用。
- `tests/test_g1_integrated_g.py` 10 例在 WSL 实跑 OK（约 0.002 秒）；负控 10 个变体全部
  `NOT_COVERED`，既有 `_apply_round` 反例仍一步不推进。
- 未 prepare/run 真实 g 根，未 source 密钥，未访问旧 root，未跑旧 37/28/18 套件或全库。

安全收尾（2026-11-06，同一唯一验收命令）：

- 首次 `offline-check` 仍全绿，但 `tests/test_g1_integrated_g.py` 单独跑时两例失败：新增的
  纯读用例在 `_use_batch` 之外调用 `status`，被按默认 a 批身份判 `BATCH_IDENTITY_MISMATCH`；
  在用例里显式 `with g1._use_batch(g.BATCH)` 后 19 例 OK。
- 最终 `offline-check` → `RESULT: PASS`，8 个聚合 case 全 PASS，exit 0；新增的坏账
  `UNKNOWN`、ledger 五元键、`PRIVATE_SENTINEL` 嵌套投影都并入原 case 断言。
- 另手工核对：`status --root /tmp/nyairo-g1-real-20261106-g`（根不存在）只读拒绝
  `EXPERIMENT_NOT_PREPARED`、根仍不存在；对 scratch 根跑 `audit --offline-fixture` 前后
  `experiment-g1` 与账本文件 size/mtime 完全一致，ledger `run_window_calls=11`、`max=20`，
  anchor 保留 `actv_...` / `actn:...` 已知形状 id。

## H 复用（2026-11-06，同机制、同决定、不翻转）

- 新增封闭身份 `H_PROFILE`（`g1-integrated-h`）：root `/tmp/nyairo-g1-real-20261106-h`、
  authority `-memory-authority/authority.db`、run `run:g1-real-20261106-h`、域
  `g1:real:20261106-h`、history 524、窗口 3600/max_ticks 1000/trace 10000、最晚 23:30、
  00:50 停止新请求、收尾 60 秒、累计门 1000（524+20 最坏 544）。旧 g 与 a/b/c/f 的
  profile/CLI/默认值不动；`g1_integrated_g.gate_projection` 的 12 门与反义判据原样复用。
- `g1_real_adoption` 只把 `H_PROFILE.key` 加进同一批既有接缝：`MODE_PROFILES`、累计门、
  gate=`off`、坏账 fail-closed、runtime/phase 的 `world_home` 隔离、离线子进程硬禁网、
  60 秒收尾、p1/p2 的 gate/isolation 记录、prepare 的 prereg 事实、audit/phase 校验与内部
  `_reserve` 的批集合。prepare 的真实 Supply owner 也显式传 `offline`，同一次离线调用里
  装同一份局部守卫。`g1_integrated_g` 的 status/audit/读账/caps 只增加可选 `profile`
  参数，缺省仍是 g；默认读面、12 布尔成功标准和只读行为不变。
- 新入口 `scripts/g1_integrated_h.py`、`tests/test_g1_integrated_h.py`（6 个聚合 case，
  含一条正式双 PID 短链）与 `docs/engineering/g1-integrated-h-prereg-20261106.md`。
- 唯一离线验收 `... scripts/g1_integrated_h.py offline-check` → `RESULT: PASS`、6 个 case
  全过、exit 0；首轮已知红是测试把 `_cross_phase_stop` 误写成 `g1.` 前缀，定点改到
  `g.` 后复跑全绿。未 prepare/run 真实 h 根，未 source 密钥，未跑旧 g/G1 旧套件。
