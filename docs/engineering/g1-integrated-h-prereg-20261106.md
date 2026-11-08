# G1 完整端到端新隔离入口 h：预登记（2026-11-06）

父级在 2026-11-06 直接授权继续推进到 **2026-11-07 01:00 CST**。本预登记只覆盖
G1 完整端到端的新隔离实例 h；它不重开 a/b/c/d/e/f/g，不改旧 root/旧账本/旧日期，
不把任何旧批的余量、历史或结论移植过来。

## 1. 目标

复用 `scripts/g1_real_adoption.py` 已证明的双 PID p1/p2 链、`SubmissionInjector`、
`_apply_round`、`_coverage_from_states` 与提前武装的 `_arm_unknown_pre_round`，新增
`scripts/g1_integrated_h.py`，在同一件真实 Supply 作品 A、同一 Activity actA 上完成
一次完整 G1：

- 首次真实写入：P1 的 owner 版本与 receipt 都指向同一 A；
- 中断：operator pause 走既有接口，P2 暂停轮 0 请求、无效果；
- 重启：P2 是不同 PID、同 root、同 run；
- UNKNOWN：P2 解除暂停后**第一轮之前**武装，只由 A 的下一 `commit_version` 消费一次；
  真写落地、回执丢失，只对同 execution/operation 调 `reconcile_action`，零裸重发；
- 版本冲突：原提交带过期 `expected_head`，被同一 Supply owner 的另一 writer 以
  `CONFLICT` 拒；
- NO_ACTION：模型自己明确枚举、无附带写/Action/receipt；合法 nochange 不算失败；
- 之后：新的 Decision 沿现规则实际继续**同一件 A、同一 Activity**，产出新 Action、
  新 owner 版本/新 receipt。

判定必须所有门都有证据，缺一项就 `NOT_COVERED`；只出现「曾经 CONTINUE + 曾经
NO_ACTION」不算。UNKNOWN/CAS 是**操作方按既有接缝各注入一次**的故障，不是自然发生；
模型是否采用 CONTINUE/NO_ACTION 仍是自由选择，不强制、不造历史或资格、不缩 TTL、
不清 reconsideration。

## 2. 封闭身份（不可挪旧批）

| 项 | 值 |
| --- | --- |
| batch key | `g1-integrated-h` |
| root | `/tmp/nyairo-g1-real-20261106-h` |
| authority | `/tmp/nyairo-g1-real-20261106-h-memory-authority/authority.db` |
| run / run_id | `g1-real-20261106-h` / `run:g1-real-20261106-h` |
| domain | `g1:real:20261106-h` |
| history（只记录，不读旧实例） | 524 |
| 本 run 新增请求硬上限 | 20（decision/content/retry/失败预留都算） |
| UTC 真实日上限 | 300 |
| 跨批累计上限 | 1000（524 + 20 最坏 544，不触发） |
| 真实窗口 | 3600 秒（`monotonic`，与绝对线取较早者） |
| 检查节拍 / tick | 1 分钟 / 5 秒 |
| max_ticks / trace | 1000 / 10000 |
| 最晚启动 | 2026-11-06 23:30 CST |
| 停止新请求 | 2026-11-07 00:50 CST（所有时限更早者先停） |
| 收尾冗余 | 60 真实秒 |

`REQUEST_CAP=20`、`UTC_DAY_CAP=300` 与旧 profile/CLI 含义一个都不改；h 只进
`MODE_PROFILES`，不进旧 `BATCH_PROFILES`/`BATCH_CHOICES`/`CLI_BATCH_CHOICES`。
累计门只对 h（与 g）生效，不拿旧的 300 累计门误拒 524。新树 gate 显式 `off`，
不启动 World 服务；World 读写路径仍按实例 `world_home` 隔离在 root 内。

## 3. 判定与反例

`scripts/g1_integrated_h.py` 直接复用 `g1_integrated_g.gate_projection` 的 12 个布尔：
P1 首次 owner 写、operator 中断、跨 PID 同根同 run、暂停轮零请求、A/actA 锚、
UNKNOWN 第一轮前武装、UNKNOWN 确证对账、CAS 真冲突、NO_ACTION 零写、
**NO_ACTION 后同作品新续作**、状态顺序、既有 coverage 完整。任一缺都 `NOT_COVERED`。

投影层自带的纯数据反例（错作品、错 Activity、旧 receipt 重放、UNKNOWN 未武装、
NO_ACTION 附带写、状态乱序、NO_ACTION 后没续、CAS 非 CONFLICT、UNKNOWN 未对账、
coverage 缺失）在 h 的测试里同样全部必须 `NOT_COVERED`。

`real_g1_evidence` 不由离线夹具冒认；离线绿只证明入口与门，真实模型是否自然走到
由唯一真实 run 决定，不出现就如实 `NOT_COVERED`。

## 4. 预算与失败边界

P1/P2 共一本持久账本；Decision、内容、失败预留都算。第 21 次在 reserve 前拒；
坏账 `corrupt`/`UNREADABLE` fail-closed，不当作 0 次放行。真实 provider/transport
失败由既有 `SendLatch` 持久锁停：P1 落盘的停因对 P2 的新锁同样有效，失败后退、
不补第二 run、不续未用额度。窗口 3600 秒与 00:50 绝对线取较早者，60 秒收尾冗余
不拖过绝对线。

## 5. 唯一离线验收

```bash
MSYS_NO_PATHCONV=1 wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/g1_integrated_h.py offline-check'
```

该命令只在新 `mkdtemp` scratch 根内跑：父进程 AF_INET/AF_INET6/DNS/create_connection
硬拒，phase 子进程与自有 Supply owner（含 prepare 的 owner）各装同一份局部守卫
（AF_UNIX 放行）；包含 6 个聚合新 case 与一条正式双 PID 同根短链，不重跑旧 g
套件或旧 G1 19/28/37 套件。scratch 全部保留，不使用 `TemporaryDirectory`/`rmtree`/
裸 unlink，不读写任何旧 root。

## 6. 真实入口（父级执行，worker 不跑）

```bash
bash .agents/worker-runs/20261106-g1-h-prepare.sh
bash .agents/worker-runs/20261106-g1-h-run.sh
```

prepare 不 source 密钥，只沿既有非密钥启动配置取地址；run 只在父级 shell 里
`source /root/.nyairo-model.env`，不读取/回显、不落盘。真实 prepare 之前必须再次核对：
新 root/authority 未存在且非 symlink、无并发 writer/产品、账本 0、身份/截止一致；
任一不满足就停，不换名追成功。只调用唯一 run 一次；实际预留含失败照扣、不补用。
没发出的 UNKNOWN 不造回执；UNKNOWN 只能对原 operation 对账，不裸重发。

## 7. 未覆盖分类

- 场景完成：12 门全 true → `COVERED`。
- 正常到窗 / 到额 / provider 失败 / 输出截断 / 结构或身份故障：按既有
  `stop_reason` 如实记录，不用离线绿替代。
- 模型合法地选 NO_ACTION 后的不同路线、或没有再次续写同一 A/actA：`NOT_COVERED`，
  不归咎模型“不配合”，也不改判据重跑同一批。
