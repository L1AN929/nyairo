# v0.6 D 预登记：ambient 背景自然观察（2026-11-03）

本文件是 D 批的封闭预登记。范围由本人本窗口直接批准：「继续，批准授权到凌晨一点，
不需要问我，按照大纲和你和那个窗口的决定」，随后「真实模型的需要也不需要问问，直接批准」。
D 是一个**新的、独立的新批**，不重开 B/C，不改默认配置、旧根、Memory 权威或发行物。

## 身份与参数（封闭值）

| 项 | 值 |
| --- | --- |
| key | `v06-natural-d` |
| root | `/tmp/nyairo-v06-natural-20261103-d` |
| authority | `/tmp/nyairo-v06-natural-20261103-d-memory-authority/authority.db` |
| run | `run:v06-world-natural-20261103-d` |
| domain | `v06:world-natural:20261103-d` |
| history | 212（211 确认 + 相机未知 1） |
| 新增共享 cap | 20（decision / content / 失败预留都算，不退） |
| 跨批累计记账 | 212 + 20 = 232，最坏仍在本人批准的 1000 以内；单根 UTC 日 300 保险丝不动 |
| 真实窗口 | 1800 秒；检查 1 分钟；tick 5 秒 |
| 上限 | max_ticks 900；trace 4000 行 |
| 最晚启动 | 2026-11-04 00:15 CST |
| 绝对截止 | 2026-11-04 00:50 CST |
| 收尾冗余 | 窗口后最多 60 秒 |

窗口在 `started + 1800` 与绝对截止中取较早者，过线不缩短窗口、不移动日期、不补第二次 run。

## 本批唯一新增的接线

D 只在自己这份 phase / runtime / World 子进程环境里显式设置：

- `WORLD_BODY_INTENT_MUTATION_GATE=off`（本批只观察 ambient 背景；真实世界动作走既有
  gate-off 预提交拒绝链，不靠父 shell export 覆盖代码映射）
- `CHIYO_WORLD_AMBIENT_EVENTS_ENABLED=1`（B/C/旧批不设）

> 历史说明：上一单按**原任务要求**曾把 D 写成 `canonical`；本批改回 `off` 是父级在施工途中
> 明确的新选择（本轮只观察 ambient），不是对上一单结果的倒写。e/B/C 仍保持 `canonical`，
> 旧 d/f 仍保持 `off`，旧集合一个未扩。

父 shell、实例默认、`.env`、旧根都不改。ambient 事实仍然只是背景：不新增候选、不授权动作、
不强制 MOVE、不制造事件，也不会自动变成个人任务。若本地时刻没有 ambient 事件，按真实 0 报告，
不改时刻或模板求绿。

## 安全与隔离

- 新根正常 `prepare` 现有 world/package，不读旧 C 实例；可复用仓库世界包与既有生成方法，
  不平移日期、不造内容。
- 所有 World 写面 / world_home / registry / lifecycle、HOME / Memory / Supply / 预算 / socket
  都在新根或独立 authority 内；symlink、根外、错 phase 映射在写入前拒绝。
- 继承 B/C 的坏账 fail-closed、父进程与两个 owner 子进程的 AF_INET/AF_INET6/DNS/
  `create_connection` 硬拒（AF_UNIX 保留）、正式授权链、正规 close/shutdown。
- gate off 的正式 START 必须走既有预提交拒绝：`STEP_REFUSED`、Action `PREPARED`、
  attempt `PRE_SUBMIT_FAILED`/`GATE_OFF`、零回执、无 APPLIED、票不消费、世界位置与
  Body 姿态不变、`pending_unknown` 0。
- 没有 CONTROL / operator 材料 / 注入器 / 强制暂停重启 / UNKNOWN·CAS 注入 / 清静默 / TTL 跳时 /
  账本 release。
- 缺真实 endpoint 在写前拒绝；不提供默认 `.invalid` 占位。

## 验收与「未覆盖」

离线入口在**脚本 client 真正收到的输入**上核对：ambient 开关、事件数量、事件 ID，以及
每个事件的 `event_id` 与公开 `summary` 是否以原文精确出现在同一份输入里；prompt 正文不输出。
当前 World 无事件时如实报 0；另有固定非产品 fixture 只验证边界透传，不拼成真实 World 非空证据。
同一离线入口还核对 gate off 的正常拒绝链（见上节）；这是离线脚本 client 的边界证据，
不等于真实 run 已经发送或模型已经理解。

真实 run 只执行一次。自然选择（包括是否因 ambient 改变选择）没有出现就记「未覆盖」，
不补跑、不为了样本强行造事件或改变静默规则。本批不证明 G1 整体通过、不证明模型理解或行为因果。

## 运行方法（由父级执行，worker 不运行）

```bash
bash .agents/worker-runs/20261103-v06-d-prepare.sh   # 只 prepare，0 模型
bash .agents/worker-runs/20261103-v06-d-run.sh       # 唯一一次真实 run
```

`prepare.sh` 不 source 密钥；`run.sh` 只在同一个 shell 里 `set -a; source /root/.nyairo-model.env; set +a`，
然后显式固定 provider / v1 / `cn:deepseek-v4.1-flash`，不回显密钥。两个脚本都不在本次 worker 范围内执行。
