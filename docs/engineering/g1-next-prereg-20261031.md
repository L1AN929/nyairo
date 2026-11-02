# G1 下一次限定采用：预登记（离线准备完成，真实入口未批准）

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

2026-10-31。本文只是**预登记与离线准备**，不是运行授权，也不是 G1 通过声明。
本窗口只交付 [离线 harness](../../scripts/g1_next_offline.py) 与
[测试说明](../../.agents/notes/implemented/testing/2026-10-31-g1-next-offline.md)；
真实模型、G1 正式根、authority、正式 prepare/run 都没有创建、没有加载密钥、没有发 HTTP。
（离线 harness 里的 `odl.prepare` 是现成夹具在 `mkdtemp` 隔离根内的建实例，不是 G1 的 prepare。）

## 1. G1 原门与本次限定目标分开

G1 原门要求完整端到端：中断、重启、UNKNOWN、版本冲突、NO_ACTION，下一次继续**同一件真实作品**，
证据到 EL3＋限定 EL4。**不是**“再写一版作品”就算过。本次限定目标只回答其中一条窄链：

- **不可替换锚**：首次由真实模型自选、且 owner 成功提交的 `artifact=A`、`Activity=actA`。
  没有建出 A 就是 `NOT_COVERED`，不能换一件作品补。
- **重启后必须新 Decision**：第二次（跨真实 PID）必须由**新的真实 `mrun` Decision** 自选
  `CONTINUE`（或有既有合法资格的 `RESUME`），正式 AG-1 提交、AG-2 采纳，绑定**新 Action**，
  owner 产出**新版本/新 receipt**。
- **同 A / 同 actA**：目标必须精确是 A，Activity 必须精确是 actA；同源另建的 Artifact B、
  别的 Activity、只让 candidate/env/string 非空、或重放旧成功，都不算通过。
- **不发明资格**：PAUSE 没有 OPEN 就不造资格；不用 timer 替换、不注入 head 诱导、不为通过
  改 no_change/静默/来源。`RESUME` 必须走既有合法资格，且资格消费关系有证。
- **模型合法不作答**：`NO_CHANGE` 或合法静默都合法；失败覆盖就交未覆盖，不补试、不重放。

## 2. 五段故障如何接同 A，以及每段的标签

现有链已有正式接缝，本次只登记“怎么接同 A”，不承诺五段一定都自然出现：

| 段 | 接法 | 覆盖标签 |
| --- | --- | --- |
| 中断 | operator pause 走既有接口；PAUSE 释放前台，不自动恢复 | `PAUSED_BY_OPERATOR` / `NOT_COVERED` |
| 跨真实 PID | 新进程、同根、同 run；预算/lease 重新读回，同 run 不刷新 | `CROSS_PID` / `NOT_COVERED` |
| UNKNOWN | 只调 owner 的 `resolve_operation` 对原 operation 对账，零裸重发 | `RECONCILED` / `UNKNOWN_UNRESOLVED` |
| 版本冲突 | 正式链带旧 `expected_head` 过闸，owner 以 `CONFLICT` 拒；不造假回执/结果 | `CAS_REFUSED` / `NOT_COVERED` |
| NO_ACTION | 只有模型真的选了且无附带写才算；流程无 Decision 另记 | `NO_ACTION_CHOSEN` / `NO_DECISION` |

- operator pause/restart、丢答与 CAS 故障若在将来真实批使用，**必须明确标注为人为干预**，
  不冒自然选择/自然故障。本次离线 harness 只用脚本替身演示同一条接缝，**不是 EL4**。
- `NO_ACTION` 与“流程没有 Decision”严格分开：流水线 NO_ACTION/QUIET 不等于模型明确选择；
  脚本注入也不能冒真实模型采用。
- 不承诺强制凑满五段。整条 G1 若仍缺项，结论就是 `NOT_COVERED`，不能因为锚续作成功就自动全过。

## 3. 出口分类

- **成功出口（限定）**：A/actA 锚成立，且跨 PID 的新 Decision 在 A/actA 上产生新 Action、
  owner 新版本/receipt，且每项都有对应证据。只写“限定目标 COVERED”，不写“G1 已过”。
- **NOT_COVERED**：模型没选回 A、自然 PAUSE 后没有资格、合法静默、到额停、只覆盖了一部分；
  如实记录，不补跑、不造资格。
- **技术失败**：provider/transport 失败、坏账本、身份不符、安全拒绝；整批停，不第二次 run、
  不换根、不补余额，失败不退。
- **状态 exit0 不等于 G1 PASS**；缺证据记 `UNKNOWN`，不按成功计。
- **UNKNOWN 不裸重试**：只对原 operation 对账；对不上就保持 UNKNOWN。

## 4. 下一真实批预算与身份（全部为“仅建议，未创建/未批准”）

- 新增硬 cap **20**；总预算 **300**，历史 **168**，最坏 **188**，余 **112**。
  Decision＋内容＋失败共享这一本持久账本，跨 PID 累计；**不退款、不续跑、不挪旧批余额**。
- 窗口最多 **1800 真实秒**，并与未来本人明确给出的绝对截止取较早者。当前授权至
  **2026-11-01 01:00 CST**，不自动批准 run。
- **建议身份（未创建、未批准，仅是预留建议）**：
  - root `/root/nyairo-g1-real-20261031-f`
  - authority `/root/nyairo-g1-real-20261031-f-memory-authority`
  - run `run:g1-real-20261031-f`
  - domain `g1:real:20261031-f`
  这些名字不构成可运行配置；未来批准后仍须重新核对 history 是否已漂移，不能把 168 固化后
  无视新增占用。
- 未来启动前必须再次核：新路径/authority 新鲜、无并发 writer、身份/batch 一致、账本 0、
  started 锁只写一次、所有写面隔离在本根。任一项不满足就停。
- **World 默认 off**，本目标不申请 canonical；本目标是作品续写，不强迫世界动作。以后若确需
  World 动作，另批申请。

## 5. Provider 与证据边界

- 沿原参数：provider `custom`、`https://gw.example.invalid/v1`、模型 `cn:deepseek-v4.1-flash`；
  温度 0、非 stream、Decision 4096 / 内容 2048，无 seed/top-p。**本次只公开参数，不加载密钥**；
  真实启动由本人批准后另做受控适配。
- 只存安全 ID/枚举/计数/时间；不落 prompt、标题正文、headers、异常原文；最终结构不冒“已送达”，
  owner 结果与 Decision 的关联证据必须明确。
- 不对旧批 a/b/c/d/e 做因果改判；它们的结论保持原样。

## 6. 离线 harness 的边界

[docs 对应脚本](../../scripts/g1_next_offline.py) 只提供 `--offline-check`：

- 实际 `mkdtemp` 隔离根 + 真实 Supply/Memory/Activity owner + 正式 runtime/cycle；
  START A 成功 → operator 中断 → 不同真实 PID 重开 → 同 run 预算读回 → 新脚本 Decision
  CONTINUE A → 同 actA / 新 Action / 新 owner 版本。
- 判据反例：同源 Artifact B、错 Activity、旧成功重放、无模型选择，全部 `NOT_COVERED`。
- 最小负控：共享 20 请求上限 → 第 21 次零发送拒绝；坏账本 → 零发送拒绝；重复启动 → 拒绝；
  HTTP 守卫真的拦住普通网络连接（AF_UNIX owner 通信放行）。
- UNKNOWN/CAS/NO_ACTION 能复用现成正式 API 的同单串上；接不上的项如实记 `NOT_IMPLEMENTED`，
  不用空集合 pass。
- **Decision/内容是脚本替身**，不证明真实模型自然采用；脚本替身不等于 EL4。
- `--prepare`/`--run`/`--execute-approved` 被明确拒绝且不写任何文件；`--execute-approved`
  本身不是授权。

离线全绿只说明判据/接缝在本机隔离夹具里成立；**真实 G1 仍是 `NOT_COVERED`**，等本人另行批准。

## 7. 2026-10-31 批准后的 f 启动入口（未执行）

本人在三项一页决策单后直接回复“批准”。批准范围**仅** G1 必要启动适配与随后的一次
限定真实采用；本节只登记已交付的入口与实际命令，**不是已运行声明**。真实
prepare/audit/run 仍由父级在唯一一次运行前执行，且以现场新鲜/并发/账本核对为准。

已交付的新入口是 [g1_next_live.py](../../scripts/g1_next_live.py)：f 批的 prepare/audit/run
薄包装，复用 `g1_real_adoption` 的现成 p1/p2、共享账本、operator pause、正式 AG-1/AG-2/AR-0
与 owner 版本；不另造生活循环。`run` 必须显式 `--execute-approved`，且只能用一次。

```bash
# 1) 写前新鲜：root 与独立 authority 都必须不存在（父级先核对，再执行）
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/g1_next_live.py \
    prepare --root /root/nyairo-g1-real-20261031-f

# 2) 只读核对：state=prepared / started_at=null / ledger calls=0 / gate=off / cap20
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/g1_next_live.py \
    audit --root /root/nyairo-g1-real-20261031-f

# 3) 唯一一次真实运行：密钥只由父级 shell source，入口不读取、不回显
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/g1_next_live.py \
    run --root /root/nyairo-g1-real-20261031-f --execute-approved
```

- 离线验收（不碰正式根、不加载密钥、不发 HTTP）：
  `PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest tests.test_g1_batch_f`。
- `run` 输出中的 `verdict.result=COVERED_LIMITED` 只表示**本批窄锚**成立（A/actA、跨 PID、
  新 Action、新 owner 版本）；完整 G1 五段在 `verdict.segments` 分开报，仍然不是 G1 通过。
- `prepare` 对 root 与 authority 写前新鲜拒绝；跨 phase 携带 f 身份/截止/预算；同一本
  20 请求账本跨 PID 累计，第 21 次在发送前拒；provider/内容/schema 失败关锁并把停因持久化；
  `started` 只能写一次，重开不延长窗口。
- World：gate 只在本进程树显式 `off`，所有写面按实例 `world_home` 严格隔离（同 e 的守卫），
  **不启动** World 服务，也不做世界动作验收；父 shell / 默认配置 / 仓库默认台账都不改。
- 缺失覆盖仍记 `NOT_COVERED`；不补跑、不重放旧成功、不造资格。
