# 0.6 B：一次真实模型自然世界观察（已结束）

当前状态：本人分别直接批准离线实现、地址核对与「批准 B 实跑」后，唯一真实run已结束、run/audit均exit0。新增1次请求、明确NO_ACTION，移动/返程未覆盖，共同192/300；不补跑。实际结果见[本批报告](v06-world-natural-b-result-20261102.md)。下文保留事前参数与各阶段当时状态，不把历史“未批准/未建根”当当前结论，不以本文续期或支用未用19次。

## 离线实现状态（2026-11-02）

父级最终独立固定 `offline-check` 实际 exit0、ok=true，37项全真、23.605秒，新根 `/tmp/v06-natural-b-offline-53eu932t` 保留。首轮父级31项/19.944秒通过但未满足的旧批兼容与owner守卫缺口，经限定收尾补齐后才接收。18个单元例仅worker自测；worker收尾额外自跑三次的偏差保留。60秒总收尾只验参数分支与0.152秒受控超时进程终止，未挂起整套服务60秒；正常runtime closed、两owner exit0且listener不活，graceful_stop_line未见，不冒完整故障排空。真实root/正确authority仍ABSENT，产品请求0，endpoint/真实运行仍未批准。

离线实现已完成：新增 `scripts/v06_world_natural_b.py`（`offline-check` / `prepare` / `audit` /
`run`，固定本预登记身份，真实命令只实现未执行）与 `tests/test_v06_world_natural_b.py`；
`scripts/g1_real_adoption.py` 只加新封闭 profile、natural 路由、canonical gate 三处 env 映射、
严格 world_home 隔离、latest_start 跨 phase 字段与离线 AF_INET 硬拒。旧 a～f 身份、旧 CLI
元组、默认值与旧行为不变；B 不进入旧 `NATURAL_PROFILES`/`BATCH_CHOICES`。

收尾补齐三项（2026-11-02，仍只离线）：

- 坏账 fail-closed **只限 B**：`_ledger_calls` 对旧批保持原 HEAD 语义（只有精确大写
  `CORRUPT` 才算读不出来）；B 的身份分支才把小写 `corrupt`/`UNREADABLE` 也当不可读。
  纯替身负控与 B 的 20→21/坏账发送前拒一起在离线检查里实际执行。
- B 离线的 World/Supply owner 子进程也装同一份局部网络守卫（argv 引导，模块生成在
  本次 scratch 根；不改 components/不装全局 sitecustomize）：父进程与两个 owner 子进程
  都实测 AF_INET/AF_INET6/DNS/`create_connection` 拒绝、AF_UNIX 正常。
- 总收尾界限：B 的 natural phase 在窗口结束后最多再收尾 **60 真实秒**（父级到点终止本次
  自有 phase 树；World/Supply 随父进程死亡退出），旧 d/e 仍是 120 秒；离线检查有受控
  超时负控与实际 `close_runtime`/`close_world`/`close_supply` 断言。

零模型离线检查实际 exit 0，约 20.7 秒，37 项全 PASS：新身份/cap/窗口/截止一致；prepare 零模型；
共享账本 20→21 在发送前拒；坏账、过期窗口、19:25 后启动、20:00 后启动、错 root、错批 prereg、
缺 latest_start 均在写入前拒；真实 phase 子进程起标准 Supply/World/runtime，World READY、
gate 与 child env 都是 canonical，World home/registry/lifecycle 全在本根；脚本 Decision 的
START 产生一条正式 POSE Action（SUCCEEDED、1 attempt、1 owner receipt、同一 execution 的
lifecycle APPLIED）；HTTP/产品模型请求为零；repo 默认 World 台账零新增；真实 root 与 authority
仍 ABSENT。证据见 `.agents/worker-runs/20261102-v06-natural-b-offline-finish-out.json`、
同前缀 `.log` 与 scratch `/tmp/v06-natural-b-offline-8hbtutys`（首轮 scratch
`/tmp/v06-natural-b-offline-057jy6ya` 保留）。

真实 `prepare` / `audit` / `run` 均未执行；真实 base URL 尚未核明；20 请求/1800 秒与
19:25/20:00 窗口仍是待批建议。离线 POSE 不是自然选 MOVE、出门/回家或完整 G1 的证据。
上方参数未因本次实现改动。

## 目标与结论边界

看标准新实例在临时 canonical 下是否自然选 MOVE，若出门，是否自然返回原地点，且每次正式动作的票据、效果和结算能配对。允许不出门、不回家或安静；不把没选解释为动机，不强制 MOVE/回家、不加提醒/事项/控制作品，不修改候选、排序、静默或恢复条件凑覆盖。

[A 的机制验证](v06-isolated-gate-g1-proposal-20261102.md)使用脚本选择和测试业务钟；B 不使用该 driver 或测试时钟。历史[e批](world-observe-e-result-20261031.md)在约673秒用完20预留，最终候选有MOVE却未选择；这次不是首次开门、不是单变量因果实验，不保证补到自然返程。完整 G1 的同目标中断/跨PID/UNKNOWN/版本冲突/NO_ACTION后续作不在本批，不注入这些故障，不拼历史证据宣布G1闭合。

## 建议批准的固定参数

- 批次键 `v06-natural-b`，与旧G1 b、G2 b不同。
- 新隔离 root `/tmp/nyairo-v06-natural-20261102-b`；run `run:v06-world-natural-20261102-b`；域 `v06:world-natural:20261102-b`。
- 独立 authority `/tmp/nyairo-v06-natural-20261102-b-memory-authority/authority.db`。使用既有Memory显式启用/权威绑定，不改准入或恢复合同；路径隔离不冒整机回滚新鲜度。离线检查使用不同mkdtemp根，不提前创建这个真实根。此前只查过root与一个不同的`-authority`候选不存在，最终`-memory-authority`尚未现场检查，启动前须重新核正确路径，不能冒已经全新鲜。
- 历史保守占额191/300（190确认+相机未知1，沿用历史非全机新总账），本批Decision+内容+失败预留共享新增硬cap20，最坏211/300、余89。不退失败、不挪额、不续杯、不换run/根补样；现场若其他窗口已新增占额，停止重新核定，不自动改本基线。
- 单natural phase，运行最多1800真实秒，固定检查窗口1分钟、tick5秒，沿现有实验参数。这个1分钟是新实验实例配置，不是改生产默认；NOT_DUE/QUIET仍零请求，不清ledger或TTL，20是请求上限而非20次唤醒承诺。
- 固定建议窗口：2026-11-02 19:25 CST前完成预检并启动，停止新提交绝对线2026-11-02 20:00 CST；单批仍取started+1800秒与绝对线较早者。正常收尾最多额外60真实秒，不得借收尾发新Decision/Action。按19:25启动可在19:56前完成正常收尾。赶不上不启动、不滚动延期、不自动移到明天。
- 全部业务时间与权限TTL/预算/截止走真实时间，不压缩日、不跳到arrival/营业时刻。旅程在窗内未到达或未返回就是未覆盖/在途，不强行结算或注入到达。

## gate 与真实模型参数

复用现有 `WORLD_BODY_INTENT_MUTATION_GATE=canonical`，仅新phase/runtime/自有World child的env映射。父进程gate原值不变，不改 `.env`、用户设置、生产默认或旧实例。World home、registry、lifecycle与其它bridge写面、Supply、Memory、账本、HOME/socket均指本次根或明确独立authority；缺参/越界/symlink逃逸写前拒绝。

模型沿已有 `custom`、`cn:deepseek-v4.1-flash`、temperature0、非stream、Decision输出cap4096/内容cap2048；不换供应商/模型/请求参数、不新增schema重试。真实base URL必须由运行期显式 `NYA02_MODEL_BASE_URL`提供，无默认值；`gw.example.invalid`是脱敏占位，禁止运行。

**地址核对已完成，真实运行未批准**：本人单独批准地址核对后，静态白名单提取在既有项目外环境文件中找到非占位HTTPS endpoint、路径`/v1`；实际内部地址只留本地ignored结果，不写公开候选文档。未source/执行配置、未加载密钥、未发网络或模型请求，不证明连通性/模型可用。返回字段名被宿主替换，不能断言运行期`NYA02_MODEL_BASE_URL`已经设置；未来获批真实启动仍须显式传入已核地址。真实prepare/run、最新并发/预算/根新鲜和参数一致性仍须按前置核对，不把静态地址存在当可直接开跑。

密钥只在父级唯一真实run的启动shell source既有项目外 `/root/.nyairo-model.env` 到进程环境，不读取或输出文件正文，不写仓库、报告、配置。worker只做离线；不source该文件。除固定provider模型请求，不新增外发/Telegram/网站部署；普通本地交互也不注入实验。

## 实现方式与完整写集（已离线实现，真实运行未批准）

旧 `g1_real_adoption.py`的d/e是单natural phase，现成gate与全写面隔离可复用，但身份/截止封闭且已结束，不能把旧e重新run或只改旧截止。已按新薄入口实现，不重造Life循环：

- 已新增 `scripts/v06_world_natural_b.py`：封闭offline-check/prepare/audit/run，固定本预登记身份；真实入口先拒未配置/过期/已started/坏账/身份或隔离错配，安全摘要分列自然覆盖。
- 已新增 `tests/test_v06_world_natural_b.py`：只测新身份、共享20预算/发送前截止、实际子env canonical与根内映射、真实入口与离线隔离，旧批不解锁；复用现成正式子进程/owner链，不再复跑A往返。
- `scripts/g1_real_adoption.py`：只加本新封闭profile/单natural路由/gate映射与必要同一接缝参数传递，不动a～f旧值、默认选择、旧CLI集合、旧根/窗口、生产组件。phase子进程/World child/runtime环境与准备/运行/audit身份已一次圈齐。
- 本预登记、现有[分层proposed note](../../.agents/notes/proposed/testing/2026-11-02-v06-isolated-world-roundtrip.md)，已新增对应implemented testing note并迁入B当前事实；不改A结论或历史准则。ignored工单/十行结果/日志与全新离线scratch保留。
- 不在写集：components、旧tests/fixtures、A driver、默认flags/预算、Memory准入/恢复权威、依赖、系统/网络/代理/权限配置、网站、git发布。新规则/协议/落盘合同或写集外实现需要时STOP。

施工由唯一dispatch worker，普通3600秒/300轮/fallback空。它只离线，不建正式root、不加载产品密钥、不跑真实模型。零模型离线接线已通过；**真实run仍等本人在本窗口单独明确批准**，本预登记不构成真实运行许可。

## 验收命令与执行顺序

以下接口已实现；真实命令在本轮仍未执行，参数不能当授权开关：

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/v06_world_natural_b.py offline-check'
```

worker与父级各一次此新入口，离线HTTP硬拒、真owner子进程/脚本Decision；仅新接线，不旧全库/矩阵/重复A。之后身份、正确root/authority新鲜、同机无并发产品、最新共同占额、源码冻结、父gate原值、endpoint参数核完且获本人真实运行批准，才依次执行：

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/v06_world_natural_b.py prepare'
```

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/v06_world_natural_b.py audit'
```

prepare的非秘密endpoint只来自显式进程配置，密钥不需要在准备阶段加载；若既有接口要求密钥才prepare，须限定为不发请求的既有门，否则STOP说明，不用探测请求求绿。audit必须prepared/started=null/window absent/ledger0/身份、路径、cap与截止匹配。

唯一run由父级在同一启动shell按获准方式source项目外密钥并显式提供已核endpoint；不在文档拼真实秘密命令。核心命令拟为：

```bash
/tmp/rc104-venv/bin/python -B scripts/v06_world_natural_b.py run --execute-approved --capture-candidates
```

run仅一次；记录started即不重开，中断先读本批状态/原operation，不重放。结束只audit与本批安全元数据，不再请求模型。

## 观察与停止

每轮只记已存在的安全IDs/固定枚举/布尔/顺序：原始供给可用段、合法集、最终结构分开；无接缝UNKNOWN，不完整prompt/正文/reasoning/token/header。候选可见不冒送达理解、流程NO_ACTION/Decision空不冒模型选NO_ACTION。

分层报告：①MOVE是否在最终合法选择；②真实模型是否选MOVE；③正式出发/在途/到达；④到达后合法回程是否可供给与是否选择；⑤是否到原地点；⑥逐execution票据/Action/owner状态/Activity结算；⑦真实预留数、失败及自有停止。没有出门时回程为NOT_APPLICABLE/NOT_COVERED，不虚报回家失败。中途去了其它合法地点不算异常，也不删路径只留成功样本。

到20预留、1800秒、绝对截止、首provider/transport故障或身份/隔离异常即锁停新请求；坏输出按现有失败规则，不加重试，异常细因未知如实列出。UNKNOWN只查原execution，不裸重发、不造回执。控制器停新提交，runtime/World/Supply正规close/shutdown/drain，核身份匹配自有PID/listener；超时只终止本批自有树。不能用关门/结束为理由再走一步回家；位置、票和在途/未知效果保留，不回滚、不删根、不裸unlink，不承诺全registry撤销。

## 取舍与待批准范围

20请求/1800秒成本可控且与e的上限相同，但不保证足够观察往返；120分钟仍可能先到20次，也不能解决没选择问题。扩大额度和时长可提高机会，却更贵且不保证覆盖，因此本稿不选。压缩业务钟能更快抵达，却改变自然时序，本B不选；强制回家更易出结果，但不回答自然选择，否决。

本写集的最小实现与离线检查已经批准并完成；真实run仍要在endpoint核明后由本人单独批准20/1800与上述固定窗口。即便之后真实批未覆盖，也不自动续跑或把G1/0.6整版标完成。窗口过期只保留方案/离线成果，不自己延时。
