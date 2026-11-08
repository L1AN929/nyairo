# Agent Note: v0.6 B 新封闭入口与零模型离线检查

Status: implemented

## Problem

[B 预登记](../../../../docs/engineering/v06-world-natural-b-prereg-20261102.md)要求一次新实例的
真实模型自然世界观测：新身份 `v06-natural-b`、191/300 历史、20 请求/1800 真实秒、
19:25 最晚启动/20:00 绝对截止、单 natural phase、临时 canonical gate。旧 e 的身份与截止
已经封闭并验收，不能改截止重跑；旧 CLI 的批元组与默认值也不能因为 B 而增长。需要一层
新薄入口和最小接线，让离线能证明身份、预算、gate 与环境隔离都接对了，同时**不**解锁
旧批、不改生产默认、不运行真实模型。

## Decision

* 新增 `scripts/v06_world_natural_b.py`：`offline-check` / `prepare` / `audit` / `run` 四命令。
  真实命令固定读写 `B_PROFILE.root`，没有 `--root` 参数；`run` 仍要求
  `--execute-approved`，并额外在 `run --capture-candidates` 下工作。真实 base URL 只从
  运行期 `NYA02_MODEL_BASE_URL` 取；缺失、`.invalid` 占位、非 http(s)、URL 自带凭据
  都在写任何东西之前拒绝。本轮只实现，未执行。
* `g1_real_adoption.py` 注册 `B_PROFILE`（`/tmp/nyairo-v06-natural-20261102-b`、独立
  authority、`run:v06-world-natural-20261102-b`、`v06:world-natural:20261102-b`、191、
  cap20/1800、20:00 截止、19:25 最晚启动），但**不**加入 `BATCH_PROFILES`、`BATCH_CHOICES`
  或 `CLI_BATCH_CHOICES`，也不加入旧的 `NATURAL_PROFILES`。`_is_natural` 额外认 B；
  `_world_intent_gate` 只在 B/e 返回 canonical；`_runtime_env`、`_phase_child_env`、
  `start_run` 与 `_phase_natural` 的严格 world_home 隔离分支加入 B。
* `latest_start` 是 `BatchProfile` 的末尾可选字段（旧批为 None），写进 prereg/audit，
  并由 `_verify_instance_identity` 跨 phase 核对；“最晚启动线”与绝对截止都在写窗口/
  started 之前拒绝。
* 离线网络守卫 `_offline_network_guard` 给 B 的父进程、带 `NYAIRO_V06_OFFLINE_GUARD`
  的 phase 子进程，以及 B 离线自己的 World/Supply owner 子进程：AF_INET/AF_INET6 socket、
  DNS 与 `create_connection` 直接失败，AF_UNIX 仍供本根 Supply/World owner。旧 d/e 不带
  标记，行为不变。首轮只守到 phase 子进程，owner 子进程的缺口在收尾单补齐（见下）。
* `_ledger_calls` 的坏账 fail-closed **只限 B 的身份分支**：旧 a～f 保持原 HEAD 语义
  （只有精确大写 `CORRUPT` 才算读不出来），B 才把小写 `corrupt`/`UNREADABLE` 也当不可读。
  纯替身负控在离线检查里实际跑：旧批 `corrupt`/`UNREADABLE` 仍取计数、只有 `CORRUPT`
  返回 None；B 三者都返回 None。B 的 20→21 与坏账发送前拒不放宽。
* B 离线的 World/Supply owner 子进程与父进程一样硬拒网络：守卫模块在本次 scratch 根生成，
  用 argv 引导（`python -c <bootstrap> scripts/phone.py owner-...`）装进**这两个子进程**，
  不改 components、不装全局 sitecustomize/stdlib patch、不影响真实 B/旧批；只拒
  AF_INET/AF_INET6/DNS/`create_connection`，AF_UNIX 本根 owner 通讯照常。两个 owner
  子进程启动时各自写一份自检（布尔/pid），离线检查读回并断言。
* B 的 natural phase 总收尾界限：窗口结束后最多再收尾 **60 真实秒**，父级到点终止本次
  自有 phase 树，World/Supply 随父进程死亡退出；旧 d/e 保持 120 秒。离线检查用纯函数
  断言 60 vs 120，并有一个受控短子进程超时负控及实际 close/socket 停止断言。
* 新增 `tests/test_v06_world_natural_b.py`：B 身份与旧映射不变、endpoint 四类拒绝、
  runtime/phase 子环境同值且根内、真实子进程探针、20→21 与坏账/过期发送前拒、
  latest_start/deadline 窗口前拒、错 root/缺 latest_start 前拒、坏账范围与 60 秒界限。
  不跑旧批套件。

离线检查实测（最终）：`wsl ... scripts/v06_world_natural_b.py offline-check` exit 0，
约 20.7 秒，37 项全 PASS。真实 phase 子进程起标准 Supply/World/runtime，World READY、
gate 与 child env 都是 canonical，World home/registry/lifecycle 全在本根；脚本 Decision
的 START 产生一条正式 POSE Action（SUCCEEDED、1 attempt、1 owner receipt、同一
execution 的 lifecycle APPLIED）；HTTP 与产品模型请求为零；repo 默认
`data/world/run/action_lifecycle.jsonl` 前后零新增；两个 owner 子进程与父进程的网络
自检都是四拒一允；runtime closed、world/supply `socket_live_after=false`；真实
root/authority 仍 ABSENT。证据：
`.agents/worker-runs/20261102-v06-natural-b-offline-finish-out.json` 与同前缀 `.log`，
scratch `/tmp/v06-natural-b-offline-8hbtutys`；首轮 scratch
`/tmp/v06-natural-b-offline-057jy6ya` 与首轮证据保留。

## Alternatives considered

* **直接改 e 的截止/复用 e 入口**：最省代码，且 e 已有完整离线链。但 e 的身份/截止是
  封闭值并已验收，改它会污染历史证据和旧断言；预登记也明确要求新身份。否决。
* **重写一份 Life 循环或自造新 driver**：自由度高，但会重复既有 natural phase，并绕开
  正式 AG-0/AG-1/AG-2/AR-0 与 owner 链；离线就证明不了真实接线。否决。
* **把 B 塞进 `BATCH_PROFILES`/旧 CLI 元组**：`--batch` 可直接用，但会改旧枚举断言与
  公开 CLI 面，违反“旧值/旧行为不动”。否决。
* **离线用真实 HTTP 替身或跳过 World child**：更快，但无法证明 canonical gate、正式
  动作和同 execution 结算。否决。
* **把共享 `_ledger_calls` 直接改成大小写不敏感**：最省事，且能让 B 的坏账立刻
  fail-closed。但它会同时改旧 a～f 的坏账语义，违反“旧行为不变”；收尾改为 B 身份分支，
  旧批保持原 HEAD。否决了第一版的共享改法。
* **给 owner 子进程全局装 sitecustomize / 改 components**：实现短，但会污染其它进程或
  越出写集；改为 argv 引导 + scratch 内生成的局部守卫，只作用于本次 B 离线两个 owner。否决。
* **扩大窗口/额度换覆盖**：更贵且不保证自然选 MOVE/回家，预登记已选 20/1800。否决。

## 父级独立运行

首轮31项/19.944秒通过但两项范围缺口未接收；限定收尾后父级固定offline-check实际exit0、ok=true、37项全真、23.605秒，根 `/tmp/v06-natural-b-offline-53eu932t` 保留。真实phase的POSE/owner回执/APPLIED、父与两个owner网络自检、正常关闭及真实root/authority缺失均由该输出确认。18个单元例仅worker证据；worker收尾三次自跑的偏差保留。短进程0.152秒超时终止和60秒参数检查不等实际整套服务挂起60秒，graceful_stop_line未见不冒drain日志证明。

## Consequences

* 只证明离线接线。真实 endpoint 尚未核明；真实 `prepare/run` 未获批准、未执行；B 的
  20/1800 与 19:25/20:00 仍是待批建议，不是可支用授权。
* 自然是否选 MOVE、是否出门/回家、完整 G1、理解与 24h 都未验证；离线 POSE 不等于自然
  选择或 World 往返。不得把 A 的脚本往返或本次离线 POSE 拼成 B 已覆盖。
* 坏账 fail-closed 现在只在 B 身份分支生效；旧批的原 HEAD 语义由纯替身负控固定。
  旧批没有因此重跑，旧绿结论仍按各自历史范围成立。
* 网络守卫覆盖父进程、phase 子进程与 B 离线自有 World/Supply owner；不是“所有系统
  进程都禁网”，也不改真实 B 或旧批。首轮 note 已承认的两个缺口（共享坏账改法、owner
  子进程无守卫）在本收尾单补齐，首次结论不倒写为当时已满足。
* 60 秒是**窗口停新提交后的收尾冗余**，不是运行窗口。父级到点终止本次自有 phase 树，
  不延长、不补跑、不发新请求；旧 d/e 的 120 秒不变。
