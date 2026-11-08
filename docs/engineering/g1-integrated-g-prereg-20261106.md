# G1 完整端到端新入口 g：预登记（2026-11-06）

本人本窗口直接授权「听你的，授权你到凌晨一点，自己和那个窗口判断，不需要问我」，
之后「继续吧，按照大纲走」。授权截点为 **2026-11-07 01:00 CST**；本预登记只覆盖
G1 完整端到端的新封闭入口 g。它不重开 a/b/c/d/e/f，不改旧 root/旧账本/旧日期。

## 1. 目标

复用 `scripts/g1_real_adoption.py` 已证明的双 PID p1/p2 链、`SubmissionInjector`、
`_apply_round`、`_coverage_from_states` 与已交付的 `_arm_unknown_pre_round`，新增
`scripts/g1_integrated_g.py`，在同一件真实 Supply 作品上完成一次完整 G1：

- 中断：operator pause 走既有接口，P2 暂停轮 0 请求、无效果；
- 重启：P2 是不同 PID、同 root、同 run；
- UNKNOWN：P2 解除暂停后**第一轮之前**武装，只由 A 的下一 `commit_version` 消费一次；
  真写落地、回执丢失，只对同 execution/operation 调 `reconcile_action`，零裸重发；
- 版本冲突：正式 Decision 提交 / AG-2 绑定 / 原 Action 冻 head 之后，由同一 Supply owner
  的另一 writer 推进 A，原提交带过期 `expected_head` 被 owner 以 `CONFLICT` 拒；
- NO_ACTION：模型自己明确枚举、无附带写/Action/receipt；
- 之后：新的 Decision 沿现规则实际继续**同一件 A、同一 Activity**，产出新 Action、
  新 owner 版本/新 receipt。

判定必须所有门都有证据，缺一项就 `NOT_COVERED`；只出现「曾经 CONTINUE + 曾经
NO_ACTION」不算。

## 2. 封闭身份

| 项 | 值 |
| --- | --- |
| batch key | `g1-integrated-g` |
| root | `/tmp/nyairo-g1-real-20261106-g` |
| authority | `/tmp/nyairo-g1-real-20261106-g-memory-authority/authority.db` |
| run / run_id | `g1-real-20261106-g` / `run:g1-real-20261106-g` |
| domain | `g1:real:20261106-g` |
| history（只记录，不读旧实例） | 507 |
| 本 run 新增请求硬上限 | 20 |
| UTC 真实日上限 | 300 |
| 跨批累计上限 | 1000（507 + 20 最坏 527，不触发） |
| 真实窗口 | 3600 秒（`monotonic`，与绝对线取较早者） |
| 检查节拍 / tick | 1 分钟 / 5 秒 |
| max_ticks / trace | 1000 / 10000 |
| 最晚启动 | 2026-11-06 23:30 CST |
| 停止新请求 | 2026-11-07 00:50 CST（所有时限更早者先停） |
| 收尾冗余 | 60 真实秒 |

`REQUEST_CAP=20`、`UTC_DAY_CAP=300`、旧 profile/CLI 含义一个都不改。累计门只对本 g
身份生效，不拿旧的 300 累计门误拒 507。新树 gate 显式 `off`，不启动 World 服务；
World 读写路径仍按实例 `world_home` 隔离在 root 内。

## 3. 判定与反例

`scripts/g1_integrated_g.py` 的 `gate_projection` 逐段摊开 12 个布尔：P1 首次 owner 写、
operator 中断、跨 PID 同根同 run、暂停轮零请求、A/actA 锚、UNKNOWN 第一轮前武装、
UNKNOWN 确证对账、CAS 真冲突、NO_ACTION 零写、**NO_ACTION 后同作品新续作**、
状态顺序、既有 coverage 完整。任一缺都 `NOT_COVERED`。

投影层自带纯数据反例（不重跑链）：错作品、错 Activity、旧 receipt 重放、UNKNOWN 未武装、
NO_ACTION 附带写、状态乱序、NO_ACTION 后没续、CAS 非 CONFLICT、UNKNOWN 未对账、
coverage 缺失，全部必须 `NOT_COVERED`。

`real_g1_evidence` 仍不由离线夹具冒认；离线绿只证明入口与门，真实模型是否自然走到
由唯一真实 run 决定，不出现就如实 `NOT_COVERED`。

## 4. 预算与失败边界

P1/P2 共一本持久账本；Decision、内容、失败预留都算。第 21 次在 reserve 前拒；
坏账 `corrupt`/`UNREADABLE` fail-closed，不当作 0 次放行。真实 provider/transport
失败由既有 `SendLatch` 持久锁停，失败不退、不补第二 run、不续未用额度。

## 5. 唯一离线验收

```bash
MSYS_NO_PATHCONV=1 wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/g1_integrated_g.py offline-check'
```

该命令只在新 `mkdtemp` scratch 根内跑：父进程 AF_INET/AF_INET6/DNS/create_connection
硬拒，phase 子进程与自有 Supply owner 各装同一份局部守卫（AF_UNIX 放行）；包含 8 个
聚合新 case 与一条正式双 PID 同根完整链，不重跑旧 37/28/18 套件。scratch 全部保留，
不使用 `TemporaryDirectory`/`rmtree`/裸 unlink，不读写任何旧 root。

## 6. 真实入口（父级执行，worker 不跑）

```bash
# 先 prepare + audit（零模型、零请求、账本 0）
/tmp/rc104-venv/bin/python -B scripts/g1_integrated_g.py prepare --root /tmp/nyairo-g1-real-20261106-g
/tmp/rc104-venv/bin/python -B scripts/g1_integrated_g.py audit   --root /tmp/nyairo-g1-real-20261106-g

# 唯一真实 run：只由父级 shell source 密钥，不读取/回显、不落盘
set -a; source /root/.nyairo-model.env; set +a
/tmp/rc104-venv/bin/python -B scripts/g1_integrated_g.py run --root /tmp/nyairo-g1-real-20261106-g --execute-approved
```

真实 prepare 之前必须再次核对：新 root/authority 新鲜、无并发 writer、账本 0、
身份/截止一致。任一不满足就停，不换名追成功。

## 7. 终端安全输出与只读 audit/status（2026-11-06 收尾事实）

- **递归白名单，不透传**：`prepare`/`audit`/`run`/`status` 的 stdout 只由定型投影组成
  （固定枚举、已知形状 id、路径、计数、布尔）。`detail`、`model_params`、任意嵌套
  `phase.window`、`recent_tokens`、operator 材料正文与提示词不会出现在终端；模型参数只回
  `provider_matches` / `model_matches` / `api_key_present` 等布尔，不回 endpoint/key/provider
  原值。内部正式 phase 文件仍按原格式落盘，只是终端不再整份 dump。
- **预算五元**：`ledger` 只允许 `run_window_calls` / `utc_day_calls` / `run_window_max` /
  `utc_day_max` / `state`。缺失/坏账返回 `state=UNKNOWN` 且计数为 `null`，不当作 0 次；
  `run_window_max` 恒为 20、`utc_day_max` 恒为 300，累计门仍是 `total_accounting_cap=1000`，
  三者都不因收尾改动。
- **audit/status 纯读**：新入口不调用 `g1.audit` / `budget.view` / `_load`，而是直接读
  prereg、batch、phase、运行报告与账本文件本体；不创建不存在的根、目录或锁，不推进窗口，
  不更新账本高水位。身份校验不弱化：run/domain/root/deadline/latest_start、caps 五个封闭值、
  `world_intent_gate=off` 任一不符即拒绝。
- **失败输出安全**：测试收集/顶层异常只回固定 code 与异常类型；`_check` 失败只打印检查名和
  定型后的枚举/布尔/计数，不以截断正文充当安全；`SystemExit`（含 argparse）正常透传，
  失败退出码保持非零。
- **离线正负控**：新增注入 `PRIVATE_SENTINEL` 的 endpoint/key/detail/tokens 嵌套结果不得
  出现在 stdout；不存在 root 的 status/audit 前后目录集合不变；已准备 root 的相关文件
  size/mtime 与目录名集合不变、且不产生 `life_request_budget.lock`；坏账读回 `UNKNOWN`
  而不是 0。
