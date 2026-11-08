# 0.6 隔离世界往返与 G1 验证方案（A 已验收，B 待批准）

状态：本人本窗口直接「批准 A」后，零模型隔离往返已实现，父级固定 check 独立 exit0（14.399秒）。B 未批准、未运行；无真实模型请求。以下保留 A 的设计范围与实际交付，不能当完整 G1 或 0.6 整版完成。

## Problem

修好的 d 在 gate off 下缺 MOVE，并不说明所有世界供给为空。更重要的是，[新 e 结果](world-observe-e-result-20261031.md)已经记录临时 canonical：MOVE 在12次最终请求出现，5次全局 START 合法，但实际选择坐下及私人机会，没有移动。不能再把“第一次打开 gate”当新发现，也不能靠不选 MOVE 推断动机。

[G1 f 结果](g1-f-result-20261031.md)已有跨 PID 同作品/同 Activity 新决定续写的限定真实证据；UNKNOWN、版本冲突、明确 NO_ACTION 后续作没有在该批闭合。世界往返不替代这些项。

## Proposal

先 A，后 B。A 已获本人直接批准并验收；B 仍待单独预登记与批准，不自动启动。

### A：零模型隔离正式往返链（推荐先批这一块）

交付一个可运行的隔离 CLI：新 World/Supply/Life 装配、正式 Decision 提交与逐动作 gateway，通过已有合法路线出门，再通过新 Decision 返回原出发地点。Decision/内容使用明确标注的脚本替身；地点和合法路径从现场 owner 读取，不直接改位置、不强灌 ARRIVED、不绕路票据。脚本明确选择 MOVE，是机械链验证，不是自然选择。

先用同一场景条件的新独立根对照 gate off 明确拒绝、零新副作用；再用另一新根 gate on 验正式出发、在途、到达、返程。只比较这个受控门条件，不与历史 d/e 的自然行为跨批做因果结论。

每个新 Action 必须使用自己的 execution/一次性票；正确区分票已消费、Action 回执、旅程到达、Activity 结算。途中不能获得许可、营业条件不合法或没有返程路径时明确报阻断，不改规则凑回家。拒绝/UNKNOWN 按既有状态机保留；UNKNOWN 只对账原 execution，不裸重发。

运动业务时间允许显式测试时钟推进，权限 TTL/超时/预算仍真实时间；结论必须标“逻辑运动推进”，不能冒真实等候步行。若现成时钟接缝不足，不扩大生产时钟合同，交具名缺口。

本块最多600真实秒运行期，零产品请求。正常结束即停，不把600秒耗满当验收；worker外层普通3600秒/300轮。关门负控先于任何开放效果；正式往返结束后只核本根停服与状态，不启动额外自然日。

### gate 究竟怎么开、怎么关

开启是本次新进程树的运行参数，不是修改产品默认或权限规则：沿已有 `WORLD_BODY_INTENT_MUTATION_GATE=canonical`，只显式传给本块 runtime、phase 与自有 World child 的环境映射；默认 off、父进程与旧实例环境保持原值，不写 `.env` 或配置文件、不全局 export。

实现可能需要新隔离 driver 的参数接线代码；这不等于改 gate 判据。生产 `components/` 与既有授权/恢复合同冻结。引用：[逐段许可笔记](../../.agents/notes/implemented/bug-fix/2026-10-23-world-journey-segment-authorization.md)、[e 预登记](world-observe-e-prereg-20261031.md)。

关闭采用控制器停止新的步骤提交，按已有接口正规停止自有 runtime/World/Supply，验证自有 PID 和 socket listener；超时只处理身份匹配的本块自有进程，不能杀旧服务。未结算/UNKNOWN 保留，不为关门再发动作。进程退出意味着临时环境不再被该进程使用，不等于撤销所有已签发票，也不回滚位置、作品或已消费票；不承诺全机无其它授权。若现有停止路径不能排空，明确 STOP/UNKNOWN，不新增撤权合同。

### 完整写集（批准 A 后才施工）

- `scripts/v06_world_roundtrip_check.py`：新隔离 CLI，封闭 offline/check/audit 入口，零模型装配、运行600秒硬限、子进程 gate 与根内路径映射、正规停止、固定安全摘要。
- `tests/test_v06_world_roundtrip_check.py`：只测新 driver 的独占/隔离、关门、往返、逐段票据与停止边界，复用正式 gateway，不造 direct owner 提交口。
- 本方案文档及一篇对应新 testing note：实现时说明脚本选择、测试时钟和自然行为边界；不改历史决定。
- ignored worker-runs 工单/短结果及本块新 scratch。必要既有 scripts 接线若需修改，先具名报出再圈定；`components/`、默认 flags、预算、Memory 准入/恢复权威、旧 root、已发布 tag/包与系统配置不在写集。

不先重复全库、旧矩阵或已绿的多段许可套件。新功能若碰产品缺陷，严格反例加具名停止，不临场改产品规则。

### 固定验收命令

以下入口现已实现。worker 自测后，父级独立同命令一次，实际 exit0、ok=true，14.399秒，新根 `/tmp/nyairo-v06-roundtrip-2d61f901`；两场景全部 checks 为真。驱动边界10例仅为 worker 自测证据，父级不另重跑：

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/v06_world_roundtrip_check.py check --offline --max-wall-seconds 600'
```

每次在新隔离根自行创建数据并保留。输出必须含实际 gate、出发地点/到达地点/返程地点、每段 execution 和票据状态、最终 owner 位置、各结算结果及自有关闭结果；只输出安全元数据，不读写完整 prompt/私文/密钥。未执行断言不得 PASS，CLI 非零保留。若隔离不成立、需要新协议、同一检查两轮红原因未知、实际环境或预算异常即停。

### A 实际交付（2026-11-02，零模型隔离往返已跑通）

本人本窗口直接「批准 A」后，A 已按上面的写集落地：

- 新入口 `scripts/v06_world_roundtrip_check.py`（`check --offline`，内部 `phase` 子进程）与
  `tests/test_v06_world_roundtrip_check.py`（驱动边界 10 例）。
- 每次 `check` 在 `/tmp/nyairo-v06-roundtrip-*` 新根下建两个独立子根，同一 `clock_base`、
  同一默认世界包（seed 929711 / coastal_town）。
- `off` 子根：先按既有 `world_refusal_result_shape` 的同一接法在 canonical 下只读取一次真实
  MOVE 候选（gate 关闭时 action space 本来就不提供 MOVE），随即把 gate 关掉再走正式提交。
  实测 `STEP_REFUSED`、attempt `PRE_SUBMIT_FAILED/GATE_OFF`、Action 仍 `PREPARED`、零回执、
  零票、零 travel、`world_state.json` 字节不变、最终位置仍在 home。不是“无候选所以零执行”。
- `on` 子根：脚本 Decision 选 `home → street_1`（默认图里唯一的合法邻近室外地点，kind=street），
  经正式 Decision → AG-1/AG-2/AR-0 → World gateway/registry/Resolver 出发；到达由
  `settle_world_journey` 按 World owner 自己的位置结算，第一段 Activity 经既有 LR-5
  完成合同变为 `COMPLETED` 并释放前台；再用**新的**正式 Decision 返回 home。
  两段各一张一次性票 `CONSUMED`、各自 travel `ARRIVED`、最终位置 home，两张 Activity 均
  `COMPLETED`。Action `SUCCEEDED` 只表示出发，未在出发轮冒认到达或完成。
- 运动业务钟显式注入 `WorldBodyService(clock=...)` 并推进到旅程自己的 `expected_arrival_at`；
  真实墙钟的票 TTL/预算/权限未改。输出标 `clock_basis=TEST_LOGICAL`，真实耗时约 14–17 秒，
  不是真实步行时间。测试时钟用 `datetime.fromisoformat` 解析，不冻结全局 `time`/`datetime`，
  不新增持久 clock 字段。
- 子进程只允许本根 AF_UNIX；AF_INET/AF_INET6 与 `create_connection`/`getaddrinfo` 一律拒绝并计数。
  `HOME`、World/Supply/预算/authority/socket，以及 bridge 的 lifecycle/registry/activity/
  experience/self-maintenance 五个写面都显式钉在本子根内，缺失/越界在写前拒绝；
  父进程不 patch 自己的 gate 或正式根。
- 固定命令实测 exit 0（同一命令 fresh 根各次都可重跑）：

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/v06_world_roundtrip_check.py check --offline --max-wall-seconds 600'
```

验收命令只跑这一条；新 driver 的 10 例只测 CLI/隔离/判据/审计，不重复整链。输出只含
root/run、gate、clock_basis、起点/室外到达/返程点、逐段 execution/票/结算、最终位置、
服务关闭与零网络/零产品请求；不含 prompt、正文、reasoning、token、header 或异常任意正文。

A 的边界不变：这是脚本明确选择的机械链，不是自然选择；不证明完整 G1、24h 或真实步行。
进程退出只代表临时环境不再被使用，不撤销已消费票、不回滚位置或作品；数据全部保留。
`off` 子根会留下 Life 侧那次拒绝的 Activity 记录（World 零效果、零票），已在审计中如实列出。

### B：真实模型自然选择（另批，不在 A 内顺手跑）

A 通过后，才提交新 root/run/authority 的唯一真实批次预登记。建议起点沿标准实例，不加“必须回家”的提示或保底任务，不强制 MOVE/返程。预算建议新增硬cap20、1800真实秒、失败不退/不续跑/不第二次抽样；当前历史保守191/300，最坏211/300，但这是建议，不是现成可支用额度。

已有 e 原定1800秒上限，实际约673秒便用完20请求，没有选 MOVE；真实走出去再回来还可能超窗。因此未选、未到达或没回来都记 NOT_COVERED，不为了结果延时/加额度/制造资格。若要更长窗口，由本人事前选择固定值与最新启动线，不运行后追加。provider/参数/密钥来源也需在预登记明确，不能把历史脱敏占位 URL 当真实配置。

### G1 收口与本块的关系

A证明正式世界动作往返机制，B最多证明该样本自然选择及效果。完整G1仍需同一目标链中的中断、跨PID重启、UNKNOWN原操作对账、真实版本冲突、明确NO_ACTION与后续继续真实作品，且各项区分脚本故障干预和真实模型行为。不能把世界往返与历史f/其它分散绿拼成完整G1；A后再圈一个完整G1工单，避免本次把世界验证与所有治理重造搅在一起。24h不是G1本身的前置，但仍是其它发行Exit缺口。

## Alternatives considered

- **直接运行自然模型批次**：最接近“她自己选”的问题，能省去机械验证；但已有e未选移动的样本，盲加一次可能只重复未覆盖，不能定位票据或返程接线，暂不推荐先做。
- **改默认 gate 或要求她一定出门回家**：容易得到移动轨迹；但会扩大写权限或改变待观察选择，违反隔离及自然行为目标，不采用。
- **保留 gate off 再测 d**：最安全，但无法验证被关闭的正式世界动作，已知缺口不靠重复运行解决，不采用。

## Acceptance criteria

A仅当新根隔离、off拒绝零副作用、on正式出门并返程、逐段票据/效果/结算匹配且自有停止明确时通过；若返程或结算缺口则交具名红，不称完整G1或自然行为通过。

## Risks

WSL/root/现有Python环境、测试业务时钟都有限定；不代表普通用户、真实步行时间、生产运行或完整G1。临时 gate 退出不抹实际效果/撤销全部票；数据必须保留。不开模型请求不意味着可随意删数据、加依赖或改系统。

A 已限定验收，不再重复申请或运行。下一步仅可先准备 B 的完整预登记，写明新身份、provider 参数、预算、固定真实时间窗与失败不补跑边界；实际模型运行仍须本人单独批准。自然选择与完整 G1 不与 A 混报。
