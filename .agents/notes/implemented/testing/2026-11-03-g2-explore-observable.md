# Agent Note: G2 explore-c 维护可读与探索入口

Status: implemented

## Problem

G2 A 路线的“历史到候选”机制早已交付，但真实自然采集没有形成合格 H：10-29 有一次原批
（历史选择批，3 次预留后 ACQUISITION_STALLED_NO_REQUEST），10-31 另有两次（G2 a 与 G2 b，
各自 1/5 次预留后 NO_EFFECTIVE_HISTORY）；不是“10-31 只跑了一次”。每次只能从静态读面和
旧日志事后推断“没产生”还是“产生了但被指纹抑制”，因为每个 tick 的维护/事件结果没有留下
可读记录。要回答“这一 tick 维护了什么、QUIET 是哪个固定原因”，现有输出不够；同时还需要
一个能在自然 H 与同轮双合法成立时做历史 text 对照的新封闭入口，但不能再复制第三套实验
仪器、不能放宽 H/合法集，也不能动旧 a/b 身份和根。

## Decision

1. `scripts/g2_next_live.py` 新增 `C_PROFILE`（`g2-explore-20261103-c`）：历史 234、
   总 30 = 采集 8 + 影子 20（10 对）+ 保留 2、不做两臂承接；根为
   `root/{main,memory-authority,branches}` 平铺布局，绝对 21:45 CST 停新请求、19:30 CST
   最晚启动。a/b 的常量、默认 profile、20 对/52 口径一条未改。
2. 实验层只加可选参数：`run_shadow(pair_count=..., pair_order=...)` 缺省仍走完整
   `PAIR_ORDER` 20 对；`shadow_statistics(pair_count=...)` 缺省仍按 20 对原判据，
   显式更小样本只出 `EXPLORATORY_ONLY` 且不计算 McNemar；`freeze_and_check(h_version=...)`
   缺省保持旧调用语义（artifact 子串身份），给了 owner 版本后改为 artifact 整体相等 +
   version 精确对齐（行内无版本不算命中）。
3. `autonomous_life_assembly.runner_tick` 增加可选 `observer`（缺省读
   `assembled["tick_observer"]`）：维护与 feed 完成后交出一行固定字段（tick/真实观测时间/
   checked/changed/satisfied/emitted_eligibilities/feed 事件类型/World revision 三态/
   World 签名三态/QUIET 固定 reason/status）。它只读已有返回值，不额外读端口、不写产品
   状态；observer 抛错只记类型，不改变控制流。
   - **revision 与签名分开**：`world_revision_changed` 只在当次维护返回里同时有真实的
     `world_revision_before` / `world_revision_after` 时才比较，否则恒为 `UNKNOWN`；
     `WORLD_OPPORTUNITY` 签名事件受去重/启用/错误影响，**不等于** revision 变化，改记在
     另名字段 `world_signature_changed`（CHANGED/NOT_CHANGED/UNKNOWN）+ basis 上。
   - **两个 list 字段按 len 计数**：`maintain_waiting` 实际返回的 `satisfied` /
     `emitted_eligibilities` 是 list（逐条列出本次满足/新发的资格），投影时取 `len`，空列表
     就是真实的 0。`checked`/`changed` 本是 int，原样保留；bool、字符串、dict、None 和缺字段
     仍是 `UNKNOWN`，不把「没读到」写成 0。代价是这一投影只在读取时多一层类型判断，不新增
     owner 读取、维护调用或写入口，也不改控制流；`world_revision_changed` 缺真实前后值时
     依然恒为 `UNKNOWN`。
4. 新 `scripts/g2_explore_c.py` 只做 `offline-check / prepare / audit / run --execute-approved
   / status` 薄包装，复用 `LiveProfile`、`LiveStageLedger`、唯一 run 包装与正式控制器；
   维护行写实验根内 `maintain_trace.jsonl`（`status` 纯读最后一行），不进产品持久格式。
5. C 的 `offline-check` 真收集并运行 15 例新测试 + 两条真实 Supply/M0/runtime 短链：
   脚本 START→PAUSE→夹具时间维护→结构门，再跑 10 对影子（20 请求）并核 20→21、30→31
   发送前拒绝；一条装 observer、一条不装，比较控制流轨迹必须等价。脚本 Decision/内容
   只作离线装配，不是自然采集。
6. `offline-check` 复用 `g1_real_adoption._offline_network_guard` 包住整个检查（单元/短链/
   清理），并把同一守卫写成继承给 owner 子进程的 `sitecustomize`：父进程与子进程都实际
   拒 AF_INET/AF_INET6/DNS/`create_connection`，AF_UNIX 保持可用。异常输出只留固定
   `reason_code` + 异常类型名 + 计数，不输出正文/traceback/任意 detail。

## Alternatives considered

- **复制第三套 harness**：能完全隔离改动，但会立刻产生第三个需要同步的影子/预算/隔离实现，
  和“薄入口”目标相反；否决，改为在既有文件加可选参数。
- **把维护行写进产品 wake journal**：最省一个新文件，但会改变产品持久契约并被业务读取，
  也让每 tick 的噪声进入既有日志；否决，改为实验自己的 observer 文件。
- **每 tick 新增产品级维护日志**：唤醒只读结果已明确不为此扩建观测系统；否决，改为可选
  observer，只有显式装配的实验才写。
- **本轮就做两臂执行承接**：更容易给出“效果”证据，但最低 4 请求与 30 的 cap 不相容，
  拿保留的 2 个额度去凑会把完整单改成分段凑数；否决，停在影子选择边界，承接另批。
- **用 10 对套 20 对统计判据**：样本不足会制造假通过；否决，10 对只出计数/比例差并标
  `EXPLORATORY_ONLY`。**原 20 对/40 请求方案没有被替代**，仍是完整协议的默认路径。

## Consequences

- 旧 a/b 的预算、路径、20 对、统计判据与真实入口状态保持；新测试直接断言这些默认值。
- 维护行只在真实 tick 路径产生；`UNKNOWN`（无 revision 读口、读失败、缺字段）不会被写成
  `false`/0。签名事件只进 `world_signature_changed`，不会被冒充成 revision 变化。
- `history_contains_exact` 缺省 `h_version` 时恢复旧调用的 artifact 子串语义（前版曾把缺省
  路径也改成整体相等，本单纠正并加负控）；给了 owner 版本后仍是精确 artifact + version。
- observer 的 on/off 由两条真实短链对照：`off_trace_rows=0`，控制流摘要（Decision/请求/
  影子计数/owner 版本）完全一致，读侧旁路不改变行为。
- 真实模型自然 H、同轮双合法、历史 text 送达与 10 对选择差异仍未验；离线绿只是接线与门位置
  的证据，不能写成模型采用或历史因果。worker 自验不代替父级验收；父级只跑固定入口一次。
- 未改 Memory 准入、AG2/AR1/owner 状态机、默认 gate、安全策略与旧实例；产品请求 0。
