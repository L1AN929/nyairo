# 历史影响选择 v2（A 路线）离线观察控制器 · 运行手册

状态：**离线实施已完成；没有真实运行入口，没有任何产品调用，没有新预算。**
路线与参数仍以 [A 细化稿](history-choice-v2-proposal-20261029.md) 为准；本文件只记录「已经实现成
什么、怎么跑、哪一层是真的、哪些还没验证」，不改方案里任何一个参数。

## 1. 一条命令

```bash
python -B scripts/history_choice_natural_v2.py offline-check
```

WSL 下的父级验收命令（本次实际用这条）：

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 \
  /tmp/rc104-venv/bin/python -B scripts/history_choice_natural_v2.py offline-check'
```

它做四件事，全部在「最底层 HTTP `connect` 一碰就炸」的硬断网下：

| 段 | 内容 | 真假 |
| --- | --- | --- |
| `unit_tests` | `tests/test_history_choice_natural_v2.py`（29 例） | 真代码，假推进 |
| `request_parameters` | 决策/内容两条路实际会发出的 `max_tokens`/`temperature`/`stream` 实测 | 真 adapter，离线 transport |
| `live_seam` | 两段 scratch 实例演示（见 §3） | runtime/runner/owner/M0/门是真的；transport 与时钟是替身 |
| `product_run_closed` | `prepare`/`run` 子命令一律返回 `PRODUCT_RUN_NOT_AUTHORIZED` | 真拒绝 |

退出码：0＝四段都过；1＝有阻断面（会打印 `阻断面: ...`）。**没有** `prepare`、`run`；真实执行
入口是关着的，`python -B scripts/history_choice_natural_v2.py run` 的退出码是 2。

## 2. 控制器到底怎么走

`NaturalObservationController` 每轮先核门、再看，顺序固定：

0. **循环头先核门**：硬截止、观察次数、请求 cap、monotonic 窗口。已经越界就**不观察、不推进**
   （睡眠过冲、重开时账本已满、上一次观察太久都会走到这里）。落在窗口终点的那一轮只允许
   最后一次纯读，`advance` 置成 `NOT_ADVANCED`。
1. `ReadOnlyObserver.snapshot()`：纯读 `Activity` 状态、reconsideration 记录数、资格数、请求账本
   计数、wake 尾部。**不做运行时全树扫描/哈希**——那既有性能代价，也会把并发的正常 owner 写入
   误算成观察器的错。零写由离线测试用受控 read port ＋ 有限指定业务文件前后 bytes/元数据比较证明
   （`ObserverTest`）。它不取账本锁、不建目录、不调模型。
2. `advance()` **前再核一次窗口/截止/账本**：观察本身跨过窗口就不推进，本轮记
   `advance_skipped=OBSERVATION_WINDOW_ELAPSED`。推进是生产 runner 的一次 `runner_tick`
   （`maintain` → 事件轮询 → 原 `run_due`）：窗口未到是 `NOT_DUE`、同一状态已经考虑过是
   `QUIET`，两者都是 0 请求。

观察节奏：第 1 次立刻，之后每 **60 真实秒** 一次，最多 **31** 次，窗口 **1800 monotonic 秒**。
结束条件按固定顺序判定，任一先到即停：中止（供应商失败/账本不可读/硬截止）→
请求 cap 8 → H＋结构门冻结 → 观察 31 次 → 窗口到期。下一个观察点只向前对齐到真实时间之后的
格点：错过就跳过，**不追赶**，因此不会出现连续密集的观察/推进。

结论只有四种，且不混用：

* `H_AND_SCENARIO_FROZEN`：H 成立且结构门过，当场冻结；
* `NO_EFFECTIVE_HISTORY`：窗口/次数/上限到了仍没有 H；
* `SCENARIO_NOT_IDENTIFIABLE`：有 H，但没有「H＋另一件不同作品都真可执行＋NO_ACTION 合法」；
* `BATCH_ABORTED`：批次被中止（不是结论，不许写成上面三种）。

## 3. 两段离线演示各自证明了什么

**阶段一 `instance-quiet`**：正式决定链采用替身输出 `NO_ACTION`，第2至30次观察推进为 `QUIET`
（`SAME_STATE_ALREADY_CONSIDERED`），请求数始终是1；第31次观察落在窗口终点，只是纯读
（`NOT_ADVANCED`）。这一段证明静默不会因观察次数增加而重复请求，也不再因连续两次静默提前退出。

**阶段二 `instance-h`**：裁决替身选一次 `START` → 真实 content writer → 真实 Supply owner 提交
一个新版本 → 正式 M0 准入可读 → 机械 H 规则认出这件作品 → 冻结 → **正式 AG-1 结构门**。
本次实测的结论是 `SECOND_WORK_CANDIDATE_MISSING`：H 这件占着前台，另一件只剩 `DEFER` 合法，
`h_executable=true / other_work_executable=false / no_action_legal=true`。这就是本离线样本里
「H 占前台 → 另一件只能 DEFER → 不可识别」这一条已观察到的路径；它**不**给出真实模型下的概率，
也不说明缺自然 head 更新源就是结构门失败的唯一原因。

同一条时间线还演示了三件事（都在真实 runner 上）：

* 第 1 次观察：`ACTED`，2 次请求（决定＋内容），owner 版本真的写出来了；
* 第 2 次观察：真实新版本改变了情境指纹，原 runner 的 due/consideration **自己重新开放**，
  `NO_ACTION` 又花掉 1 次请求（没有任何人调 `release_reconsideration`）；
* 第 3 次观察：新情境被记为已考虑，回到 `QUIET`，0 请求。整段 3 次请求，cap 8 未破。

这两段都是**夹具**：决策答复、内容答复、时钟是替身；owner、writer 端口、M0 准入、冻结与结构门
是真的。它不是模型行为证据，也不是 1800 秒自然经过的证据。

## 4. 硬门都在哪（改代码前先读这一节）

| 门 | 位置 | 语义 |
| --- | --- | --- |
| 请求 cap 8 | `LifeRequestBudget.reserve()` | 持久账本计数；决定/内容/失败预留都算；第 9 次在 `reserve` 处被拒，到不了 transport；观察、重开、换业务时钟都不刷新，失败不退 |
| 阶段 room | `PlanGateLedger.assert_room()` | 发送前给出具名 `ACQUISITION_REQUEST_CAP`，只是让拒绝更早更清楚，不放宽 reserve |
| 观察零写（测试层） | `tests/...::ObserverTest` | 受控 read port ＋ 有限指定业务文件前后 bytes/mtime 比较；生产运行时不扫全树、不算哈希，也不把并发 owner 写入算成观察器的错 |
| 循环头／推进前边界 | 控制器 monotonic 检查 | 越界不观察、不推进；恰好窗口只末次纯读；迟到格点跳过不追赶 |
| 发送锁 | `SendLatch`（决策 adapter 与内容 client 两个发送口） | 供应商/传输失败或到期即关死，后续请求连预留都不发生；控制器读同一个锁，把它的原因翻成结束码 |
| 观察窗口 | 控制器 `time.monotonic` | 真实路径必须是 monotonic；`VirtualTime` 只在离线 |
| H | `HistoryDetector` | 首个 owner 确认 `version_created=True` 且该 artifact 的正式 M0 经历可读；checkpoint/receipt 不算 |
| 结构门 | `structural_gate()` | 正式 AG-1 请求＋正式校验，底层 transport 是离线替身：H 可执行、另一件作品可执行、`NO_ACTION` 合法，三条缺一不可；0 网络 0 预算，只写实验自己的 shadow store |

控制器**没有**任何 `release_reconsideration`、改 TTL、写 head、注入事项/资格、强制
START/PAUSE/ABANDON/RESUME 的入口；PAUSE、条件 OPEN、新 Decision 才 RESUME 这些仍归原 owner。

## 5. 本次没有验证的（不要拿来当证据）

* 真实模型会怎么选、8 次够不够、窗口内能不能建起历史——全部没做，没有产品调用。
* 两件中性作品没有已证独立自然 head 更新源；不保证窗口内自然换版，实验也不补造更新源。
* `VirtualTime` 推进的 1800 秒不代表自然经过 1800 秒；观察间隔、窗口到期的真实抖动未验。
* 承影（shadow 20 对）与真实承接没有重跑；本单不碰 v1 的 20 对、provider 参数 suite。
* 两段演示共用 `/tmp/histchoice-v2-offline-*` 两个 scratch 根，全部保留、不清理；同一台机器上
  跑多次会留下多个根（每个都只有自己的 owner 与状态）。

## 6. 输出白名单

时间线只出现：观察序号、经过秒、`status`/`kind`/`trigger_ref`、`llm_calls`、事件数、
`waiting` 状态、打开的活动数、打开资格数、reconsideration 情境数、请求计数、H ref 与门的
reason code。**没有** prompt、completion、reasoning、私文正文、环境变量、密钥或请求头。
