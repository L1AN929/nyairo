# G1 限定真实模型采用：用法与边界（2026-10-30）

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

入口：`scripts/g1_real_adoption.py`。计划见
[g1-limited-real-adoption-plan-20261030](g1-limited-real-adoption-plan-20261030.md)。

## 它做什么

在一个**全新实例**里，候选由既有 AG-0/owner 供给，由真实模型从中自己选择并自己写内容，
然后沿着已经接好的正式链经历：

1. 第一次真实 owner 写入 + 正式 Activity（作品 A 由模型自己产生，不预选）；
2. 实验 operator 用既有全局 pause 接口中断，P1 正规关 runtime、停自己的 Supply owner；
3. P2（不同 PID、同一根）读回暂停：暂停轮零请求；operator 解除后由**新的真实决定**决定是否续 A；
4. 下一次 A 写入在既有 adapter 边界丢一次回执：效果真落 owner，AR-0 保持 UNKNOWN，只对原
   operation 对账，不裸重发；
5. 再下一次 A 写入在 submission fence 之后、owner 调用之前，由同一 owner 的另一个真实 writer
   推一版，正式提交带旧 `expected_head` 被真 CAS 拒（各注入一次）；
6. 之后模型自选 NO_ACTION（必须真实枚举且无附带写）时原样记录；再之后仍只观察，不强行解除
   reconsideration、不跳 TTL。

任何环节模型没有那样选，就按窗口/额度停并记 `NOT_COVERED`，不补跑、不换 trigger 重来。

## 什么算「真的发生了」

覆盖不是按状态顺序倒推，每一项都要有对应的权威事实；证据不足一律记为未覆盖：

- **P1 的 A**：本轮 `owner_version_created=true`，本轮 receipt 指向这件作品和正式 Activity，
  且 owner 当前 head 就是本轮新建的版本号。不拿不相关的 changed 或落库前就存在的版本充数。
- **P2 续作**：作品与 P1 的 Activity 精确相同才推进；别的 Activity/别的作品原样记录、不推进。
- **UNKNOWN**：本次丢回执注入确实触发，Action 确为 UNKNOWN，原 operation 查询回
  `CONFIRMED_SUCCEEDED` 且有可核验证据。对账失败或仍 UNKNOWN 不算已对账，也不重发。
- **CAS**：本次并发注入真的写进 owner、head 真的推进，随后原 Action 的权威原因是
  `CONFLICT`（stale expected_head）。任意 FAILED/GATE_OFF/先验拒绝都不算。
- **NO_ACTION**：模型自己的枚举结果，且本轮无写入、无 Action、无 receipt；QUIET 不算。
- 发送口抛异常（含 `content.complete`）先停整批再抛出；停因落盘，后续阶段的新锁也读到并拒发。

## 固定参数

| 项 | 值 |
|---|---|
| root | `/root/nyairo-g1-real-20261030`（必须不存在） |
| run / 域 | `run:g1-real-20261030` / `g1:real:20261030` |
| 本批新增请求硬上限 | 20（决定 + 内容 + 失败预留都算，同一本持久账本） |
| 运行窗口 | 1800 真实秒，父进程首个请求前 `monotonic` 持久写，P1/P2 共用；另受**所选批次**的绝对截止约束（a/b：2026-10-30 17:00 CST；c：2026-10-31 01:00 CST） |
| 检查窗口 | `CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=1`（`prepare` 前固定的实例参数，不是每轮改 trigger） |
| provider | `custom` / `https://gw.example.invalid/v1` / `cn:deepseek-v4.1-flash` |
| 请求参数 | 温度 0、非 stream、决策输出 4096、内容输出 2048、不传 seed/top_p |
| 密钥 | 只来自执行 `run` 的父级 shell 环境（`NYA02_MODEL_*`），不读取、不回显、不落盘 |

## 命令

```bash
# 1) 离线检查（0 产品调用；HTTP connect 硬拒；决策/内容是明确替身，owner/runtime 是真的）
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
    scripts/g1_real_adoption.py offline-check

# 2) 建实例（0 模型调用；新 Memory 为空；导入一份中性 operator 材料并留一件对照作品）
python -B scripts/g1_real_adoption.py prepare --root /root/nyairo-g1-real-20261030

# 3) 开跑前只读核对（必须 ledger 0、state=prepared、started 为空）
python -B scripts/g1_real_adoption.py audit --root /root/nyairo-g1-real-20261030

# 4) 唯一一次真实运行（同一个 shell 里先 source 既有密钥文件，再执行；只此一次）
python -B scripts/g1_real_adoption.py run \
    --root /root/nyairo-g1-real-20261030 --execute-approved

# 5) 跑完只读汇报
python -B scripts/g1_real_adoption.py audit --root /root/nyairo-g1-real-20261030
```

## 第二批（b）：同一入口的封闭身份

2026-10-30 批准的 b 批不改行为路线，只换身份。所有子命令加 `--batch b`；**缺省仍是 a**，
上面的 a 命令与历史结果原样保留，不重跑、不倒写。

| 项 | a（默认） | b |
|---|---|---|
| root | `/root/nyairo-g1-real-20261030` | `/root/nyairo-g1-real-20261030-b` |
| authority | 同根名后缀 `-memory-authority` | `/root/nyairo-g1-real-20261030-b-memory-authority` |
| run / 域 | `run:g1-real-20261030` / `g1:real:20261030` | `run:g1-real-20261030-b` / `g1:real:20261030-b` |
| 历史基线（只记录） | 98 | 101 |

```bash
# 离线检查（父级验收命令；0 产品调用）
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
    scripts/g1_real_adoption.py offline-check --batch b
python -B scripts/g1_real_adoption.py prepare --root /root/nyairo-g1-real-20261030-b --batch b
python -B scripts/g1_real_adoption.py audit   --root /root/nyairo-g1-real-20261030-b --batch b
python -B scripts/g1_real_adoption.py run     --root /root/nyairo-g1-real-20261030-b --batch b \
                                              --execute-approved
```

- 只有 `a|b|c` 三档，不开放任意 root/run/预算/截止；未知批次直接拒绝。
- 批次身份贯通 prepare/run/audit 与 phase 子进程：子进程从 config 读回批次，缺身份/错批/
  另一批的 prereg 一律 `BATCH_IDENTITY_MISMATCH`，不会出现父级 b 而子进程按 a 记账。
- `prepare` 在写入前核 root 与 authority 都新鲜；已有 authority 直接
  `MEMORY_AUTHORITY_NOT_FRESH`，不默默复用、不删除重建。
- 两批仍是同一本持久账本上的 20 次预留（跨 PID 共享，不是每人 20）。

## 第三批（c）：同一入口的第三个封闭身份

2026-10-30 批准（[c 计划](g1-limited-real-adoption-c-plan-20261030.md)）。除身份外多一条独立的
绝对截止，运行时一律从所选 profile 取，a/b 的 17:00 不动。

| 项 | c |
|---|---|
| root | `/root/nyairo-g1-real-20261030-c` |
| authority | `/root/nyairo-g1-real-20261030-c-memory-authority` |
| run / 域 | `run:g1-real-20261030-c` / `g1:real:20261030-c` |
| 历史基线（只记录） | 121 |
| 绝对截止 | 2026-10-31 01:00 CST |

```bash
# 父级验收命令（0 产品调用）
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_g1_batch_c
python -B scripts/g1_real_adoption.py prepare --root /root/nyairo-g1-real-20261030-c --batch c
python -B scripts/g1_real_adoption.py audit   --root /root/nyairo-g1-real-20261030-c --batch c
python -B scripts/g1_real_adoption.py run     --root /root/nyairo-g1-real-20261030-c --batch c \
                                              --execute-approved --capture-candidates
```

- 截止在 `prereg.deadline`、窗口、phase config 与 `audit.deadline` 里是同一个值；缺截止或
  写成别的批次一律 `BATCH_IDENTITY_MISMATCH`。
- 候选观测只有 c 的真实 run 显式加 `--capture-candidates`；默认仍关闭，不加不建文件。
- 20 次预留仍是整批共享，不是每个 phase 20 次。

`run` 的拒绝条件（全部 fail-closed，不碰任何请求）：

- 没有 `--execute-approved`；
- 根不是计划里的唯一路径；未 `prepare`；账本不是 0；
- 已经写过窗口文件或 `started`（中断/失败之后也不许重开）；
- 当前时间已过所选批次的绝对截止（a/b：2026-10-30 17:00 CST；c：2026-10-31 01:00 CST），
  或写下去的窗口已经过期；
- 解析出的 provider/base_url/model 与固定值不一致，或没有可用配置。

## 运行期约束

- 业务时钟不快进；P1/P2 每个子进程在**每次发送前**都查同一份窗口文件与同一本持久账本。
- 决策口与内容口共用一把「立刻停发」锁：截止到点或出现供应商/传输失败，后续请求连预留都不发生。
- 决策口拒绝任何 `schema_retry_index>0` 的自动重试（一次尝试一次计费）。
- 故障注入只出现在既有 `adapter.submit` 接缝上，各只一次；没有触发就是 0，不补造。
- 离线用夹具时刻直接走正式 `evaluate_once`；真实 run 才由生产 `runner_tick`（原 due 门 +
  原事件门）驱动。两者共用同一个状态机与同一套注入/对账代码。

## 实验候选观测（`--capture-candidates`，默认关闭）

下一次获得独立批准的真实批次如果想知道「这一回合候选从哪来、排序是什么、哪些真的送进了
模型」，加这个开关：

```bash
python -B scripts/g1_real_adoption.py run --root <profile.root> --batch <a|b|c> \
    --execute-approved --capture-candidates
```

- **默认关闭**：不加开关不会建观测文件，也不读任何额外状态。
- **不改变业务**：候选生成、排序、裁剪、资格门、prompt 构造、模型参数、预算与 Decision
  一律照原样；观测只读同一次调用已经产生的结构，不二次 `build_prompt`、不重跑排序。
- **文件**：`<root>/experiment-g1/g1-candidate-trace-p1.jsonl` 与 `-p2.jsonl`，每行一个
  stage，权限尽量沿既有 private 写法。stdout/audit 只多一个 `candidate_trace` 摘要
  （enabled / records / error），不含内容。

每个 stage 的含义与**它当时能拿到什么**：

| stage | 内容 | scope / 局限 |
|---|---|---|
| `CANDIDATE_SET_MATERIALIZED` | 本次请求那个 `candidate_set_id` 的 `candidate_refs` 与顺序 | 是各源 materialize（每源上限 25）→ 去重 → 按 `candidate_id` 字典序 → 全局上限 100 之后的集合，**不是**原始 discover 全量；`truncated_sources` 只带计数 |
| `REQUEST_LEGAL_SET` | AG-1 硬约束/容量评估后真正交给 adapter 的 `request.candidates`，附 `current_activity` 与 `pause_condition` 白名单字段 | 顺序即请求顺序；资格 id/状态本轮没有现成结构化快照，写 `UNKNOWN`，不为补记录去查 owner |
| `PROMPT_FINAL` | **同一次** `build_prompt` 返回的结构化 payload 里的 candidates，附 `send_status`/`sent`/`send_evidence` | 已经过 `MAX_CANDIDATES_IN_PROMPT=16` 与结构性字符裁剪；不解析 prompt 文本，拿不到就写 `NO_STRUCTURED_PROMPT`。`send_status` 与「有没有拿到 payload」是两件事：正常返回只写 `UNKNOWN`（响应不等于证明实际网络发送），`SENT` 只在有直接证据时出现 |
| `PROMPT_BUILT_NOT_SENT` | 发送前就被**明确拒绝**（预算原因码 / `RequestNotSentError`） | `send_status=NOT_SENT`、`sent=false`；普通异常也可能是发送后超时，一律写 `PROMPT_FINAL` + `send_status=UNKNOWN` |
| `POST_TICK_SNAPSHOT` | 原 tick 之后的只读旁证：PAUSED Activity IDs、已提交 Decision 的 `reevaluation_hint_ref`（若有）、每条 PAUSED 的条件/资格 id 与状态 | 明确是 `POST_TICK_READ`，**不是**发送前原子快照；只调用既有 `activity_read` / `waiting_coordinator.list_*` / `decision_read.get_decision` 纯读，不 maintain/reconcile/算资格/写 owner。读失败写 `UNKNOWN`/`ERROR`，不当 EMPTY |
| `ROUND` | 每 tick 一行：`llm_calls` 总和、wake 白名单、`request_present`（有请求进入 adapter，含未发出的）、`model_decision_present`、各 stage 计数 | `QUIET`/`NOT_DUE` 零请求也照实记录；`model_decision_present=false` 表示不是模型枚举 |
| `EVENT` | operator pause 设置/解除的布尔与 revision | 不作为行为证据 |

记录边界：

- 只落固定枚举/已知 ID 形状/布尔/数字/计数/顺序号；枚举表逐字段取自现成正式类型
  （来源/候选类型/决策/活动状态/中断级别/条件/资格/wake/动作状态/原因码）。表外值写
  `UNKNOWN` 并计 `unknown`，原值不落盘；任意 ASCII 串不再被当成合法枚举。
- ID 只接受已知前缀 + 已知载荷形状（`cand:`/`semcand:`/`cset:`/`dec:`/`actn:`/`rcond:`/
  `relig:`/`waitbasis:`/`wait_dep:`/`artifact:`/`artifactversion:`/`actv_` 等）；未知前缀
  或形状不落盘并计 `unknown`。
- 不落 prompt 原文、正文、标题、reasoning、token、密钥、headers、env。
- 每阶段观测副本最多 40 条，超过只截副本并标 `total`/`truncated`（源集合本身不被裁剪）；
  `candidate_refs` 里的非法 ref 不缩 `total`，而是计入 `unknown`；整个 phase 文件最多 4000 行。
- 观测写失败只把**有界异常类型名**写进 phase 报告的 `candidate_trace`，不再重复尝试，
  业务继续；异常消息、参数与未知类名不落盘。这份数据因此要按「不完整」读。
- 三段之间只记录集合差异（谁不在谁里面），**不推断**裁剪/拒绝/资格原因；相邻集合差异
  不等于裁剪原因。
- `pause_condition.condition_ref` 是本轮**被提供**的 PAUSE 条件；模型实际写进
  `reevaluation_hint_ref` 的值只在 `POST_TICK_SNAPSHOT` 里从正式 Decision 记录读取，
  读不到就 UNKNOWN，不从条件 id 反推。

## 跑完必须看

- `audit` 输出里的 `ledger.run_window_calls`（真实持久预留，不等于全部成功返回）、
  `coverage`（六个场景分别是 true/false，`not_covered` 列出没出现的）、
  `phases.p1/p2.close_supply`（exit_code 与 `socket_live_after`）、`injections`（各只一次）。
- 新根里的 `experiment-g1/g1-phase-p1.json`、`g1-phase-p2.json` 是完整本地报告；
  标准输出只有白名单字段（id/枚举/计数/布尔），没有 prompt、completion、reasoning 或私文。

## 什么不算

- 离线全绿不等于真实模型会那样选；它只证明链路、门与注入是真的。
- 单次真实 run 没覆盖全部六项就是 `NOT_COVERED`，不升级成完整 G1，也不说模型没有能力。
- 本入口不碰旧 root、不改生产默认、不开 World gate、不外发、不提交推送。
