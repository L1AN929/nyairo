# G2 A 自然采集 · 唯一真实批的封闭启动预登记（2026-10-31）

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

状态：**离线施工完成；真实 prepare / audit / run 尚未执行。** 本文件只登记新入口
`scripts/g2_next_live.py` 的固定身份与边界；真实运行仍须父级在本人批准范围内亲自操作。

## 1. 固定身份与预算

| 项 | 值 |
|---|---|
| 实验根 | `/root/nyairo-g2-natural-20261031-a` |
| 主实例 | `<root>/run/main` |
| Memory authority | `<root>/run/memory-authority/authority.db` |
| 承接分支 | `<root>/run/branches`（A 在主实例，B 在 `branches/b`） |
| run / domain | `run:g2-natural-20261031-a` / `g2:natural:20261031-a` |
| 历史 | 185 / 总 300 / 新增硬 cap 52 = 采集 8 + 影子 40 + 承接 4 + 诊断 0 |
| 采集窗口 | 1800 真实秒，最多 31 次纯读，至多每 60 真实秒一次 |
| 整批窗口 | 5400 真实秒（含采集/冻结/影子/承接），并与 2026-11-01 01:00 CST 取更早者 |
| 模型 | `custom` / `https://gw.example.invalid/v1` / `cn:deepseek-v4.1-flash`，temperature 0、stream false、Decision 4096 / 内容 2048 |

`run` 只在显式 `--execute-approved` 下执行；首次运行把窗口与 `started_at` 持久写盘，之后第二次运行、
失败后重启、中断重放一律 `RUN_ALREADY_STARTED`。进程崩溃不续跑。prepare 不启动也不会
把 run 标记为已开始。

## 2. 三个命令

```
python -B scripts/g2_next_live.py prepare
python -B scripts/g2_next_live.py audit
python -B scripts/g2_next_live.py run --execute-approved
```

- `prepare`：root 或任一子根已存在（含符号链接）拒绝；建 main 实例、两件中性作品、独立
  Memory authority、52 持久窗口；写预登记与 batch 状态。0 产品调用、不 source 密钥。
- `audit`：**纯读**——不建目录/库、不取账本锁、不起服务、不 claim lease、不加载模型配置。
  未开跑时报告 `prepared` / `started_at=null` / `window.present=false` / `ledger.run_window_calls=0`；
  坏账、错身份、半套状态失败关闭。
- `run`：自然采集走 `history_choice_natural_v2` 的原 controller + 正式 runner；冻结后按原
  `PAIR_ORDER` 发 40 次影子；承接从 A 任何写之前的同一冻结起点 clone B。全程同一个
  `LifeRequestBudget(run_window_max=52)` 与同一把持久发送锁。

夹具只允许 `--offline-fixtures --root <tempdir 下新 scratch>`；正式根永远不能夹具运行；真实路径
拒绝任何夹具替身。夹具运行把 `http.client.HTTPConnection.connect` 硬断，并使用虚拟时钟。

## 3. H、场景与出口

- **H**：首个 owner 确认 `version_created=True`、且同一 artifact/version 的正式 M0 经历可读。
  版本按 `artifactversion:` / `artifact-version:` 归一后精确比较；不一致不算 H。
- 无 H 到窗口/预算终点 → `NO_EFFECTIVE_HISTORY`。
- 有 H 但最终冻结结构里 H 不可执行、另一件不同作品不可执行或 `NO_ACTION` 不合法 →
  `SCENARIO_NOT_IDENTIFIABLE`（具名 gate reason 保留）。
- 结构门用现成 `freeze_and_check` 的默认离线 transport：**零真实模型请求**。
- 40 只改历史 text（B 臂中性替换）；最终渲染请求其余差异在发送前以 `SECOND_VARIABLE_DIFFERS` 拒绝；
  40 不完整 → `NOT_TESTED_INCOMPLETE`，不统计。
- 统计沿原双侧精确 McNemar `p<0.05` 且绝对比例差 ≥ 0.30；完整无检出是
  `NO_DETECTED_DIFFERENCE`，不是「记忆无效」。

## 4. 承接映射

- 影子选择 → 冻结候选目标/Activity 精确匹配后才执行；错目标 / 错 Activity / NO_ACTION /
  不可执行枚举 → `NOT_RUN`，**不 fallback 另一件作品**。
- RESUME 与 CONTINUE 都按影子实际选择的枚举映射：RESUME 要求冻结 Activity 为 PAUSED 且
  存在 OPEN 资格；CONTINUE 要求前台就是冻结 Activity。真实路径的承接是**新的真实 Decision**，
  与影子选择不一致时记 `MISMATCH_NOT_ACCEPTED`，不强制模型抄影子答案。
- B 臂在 A 任何写之前 clone；两臂共享同一本 52 账本，每臂 ≤2，第三次在 `reserve` 前拒。
- 如果 clone/隔离合同没有建立，承接 A/B 两臂记 `NOT_RUN`（`R_BRANCH_ISOLATION`），
  影子统计独立成立；不新增生产合同、不 fallback 另一件作品。

## 5. 失败与隔离

- 采集 / 影子 / 承接任一阶段首次 provider/transport failure → 立即持久停批
  `BATCH_ABORTED` + `PROVIDER_OR_TRANSPORT_FAILURE`；后续连预留/内层 transport 都不发生；
  跨进程重开仍因 `started_at`/窗口/停止文件被拒。不退款、不补样、不做第二次 run。
- gate 显式 `off`；World/Body home、registry、action lifecycle、activity/spool/self-maintenance、
  Memory DB/controls 必须在实例根内；authority 必须精确等于本批共享路径；缺/相对/根外/
  符号链接逃逸在起服务与写窗口之前拒绝。
- 新 suite 用真实子进程装配 phase，核对仓库默认 `data/world/...` 五处写面 size/mtime_ns
  零新增、父进程环境不变。

## 6. 离线已验 / 未验

已验（夹具脚本 Decision/内容 + 虚拟时钟 + HTTP 硬断；真 owner/runtime/AG1/AG2/AR0/M0/Supply）：
prepare/audit 布局与纯读、无 H / 有 H 不可比两个出口、双合法正控（采集→结构门→40 影子→
双分支 RESUME 承接，48 请求）、52 账本第 53 次拒、阶段余量不挪、每臂 2 拒 3、三种阶段失败
持久停批、分支隔离与 B 起点不被 A 污染、子进程 gate off 与默认写面零新增、输出白名单。

未验：真实模型会怎么选、8 次采集够不够、1800 秒内能否自然形成 H 与双合法场景、真实 provider
失败分类与抖动、真实影子统计功效。离线绿不等于 G2 通过。

固定离线验收命令（36 例，约 100 秒，0 产品请求、0 正式根）：

```
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest tests.test_g2_live_a'
```
