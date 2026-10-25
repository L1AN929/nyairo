# Agent Note: 多段旅程逐动作正式许可 —— 后段不得绕过 World 授权口

Status: implemented

## Problem

首段 MOVE 走 `Intent Executor → gateway.sock → Resolver`，每段一张一次性授权；
但 `world_journey.advance_journey` 的第二段起，所有生产调用点都没有传 `submit_port`，
`_submit_segment` 落到 else 分支直接调 `world_living_skeleton.apply_move_to`：

* `autonomous_life_cycle.settle_world_journey`（Life resident 的结算入口）
* `chiyo_bundle.world_runner.tick`（前台推进器 / `scripts/life_heartbeat.py`）
* `chiyo_bundle.world_move_tool.move_handler`（模型的 world_move 工具）

于是同一趟旅程的第一段有 gate、真实 TTL、fresh snapshot、gateway 消费，
第二、三段却只在 Life/工具进程里直写 owner：mutation gate、一次性授权与
gateway 的记录面全部缺失。设计原文（v2.0 L12392–L12652:205/264）明确：
one-shot 不跨 Action 复用，计划时校验不能替代 dispatch 时校验。这不是新规则，
是既有正式口的接线缺口。

## Decision

1. **正式口实现下沉到 World 侧**：新增
   `components/world/service/bridge/journey_submit_port.py`
   （`FormalOwnerMoveSubmitPort` / `default_submit_port`），只组装一段 MOVE 的
   intent 并交给现成的 Intent Executor；不铸造授权、不改 registry、不改 Resolver。
   Life 的 `world_journey_action_adapter.FormalOwnerMoveSubmitPort` 变成惰性别名，
   旧调用方与注入的 `bridge` 对象形状不变，不再保留第二份实现。
2. **引擎失败关闭，且没有直写参数**：`world_journey._submit_segment` 在没有显式
   端口时用 `chiyo_bundle/journey_authorization.py` 按环境构造正式口；构造失败直接
   `blocked / SEGMENT_SUBMIT_PORT_UNAVAILABLE`。`accept_journey` / `advance_journey`
   的公开签名里**已经移除** `direct_owner`：生产调用者无法选择进程内直写。
   需要 patch `apply_move_to` 观测故障窗口的底层引擎测试，改为在
   `tests/test_world_journey.py` 里显式注入测试专用 `DirectOwnerSubmitPort`
   （只存在于测试文件，不做授权、不铸票）。
3. **五个生产入口全部接线**：Life settle、runner tick、move 工具的
   accept/advance、`life_heartbeat.advance_once`。resident 经 cycle 覆盖。
   每段沿用该段自己的 `execution_id`，因此每段各自 mint/consume 一张新票；
   业务 now 仍只用于运动时间，授权 TTL 与 gateway 消费仍是真实墙钟。
4. **签发与消费定位对齐**：Intent Executor 用进程环境的
   `WORLD_ACTION_AUTH_REGISTRY_DIR` / `CHIYO_WORLD_HOME` 决定票落哪本 registry；
   CLI 用 `--world-home` 装配的请求环境未必等于进程环境。端口在**调用窗口内**
   只补这一个定位键（socket 的父目录，或显式值优先），try/finally 还原。
   授权规则、TTL、门禁与 registry 结构都没有改。`execute_intent` 目前只接受
   进程环境这一条通道，端口用模块级 `threading.Lock` 把这个窗口串行化；
   剩余风险仍在：窗口内其他线程若自行读取该变量会看到临时值（gateway 在独立
   进程，不受影响）。没有更窄的现成参数通道，因此不改协议。
5. 新增 `tests/test_world_journey_authorization.py`（8 例正控 + 5 例负控：到期票被
   真实 gateway 以 `AUTHORIZATION_EXPIRED` 拒绝且票仍 ISSUED、段间旧观察被
   `STALE_STATE` 拒绝、重启后 UNKNOWN 不重发不铸票、服务不可用无直写、
   pause 后 gate off 只合法结算已出发段），以及零模型冒烟
   `scripts/journey_authorization_smoke.py` 与只读短日摘要
   `scripts/journey_authorization_live_status.py`。

## Alternatives considered

* **journey 级 lease（route 作为 typed target、maxUses=段数）**：表面上更省往返，
  但每段的 payload（destination/observation revision）不同，与设计
  “payloadbinding 变化即新 Action” 直接冲突，且要改授权契约与设计条款。否决。
* **只补 Life settle 一处**：会留下 runner / move 工具 / heartbeat 三条更直接
  的模型或部署入口继续直写，等于把同一个洞挪个位置。否决。
* **缺 port 时退回旧直写**：会让“正式口不可用”变成静默降级，正是本单要消除的
  失败面。否决。

## Consequences

* gate 关闭发生在第二段提交时，旅程按既有语义进入终态 `blocked`；
  已到达的第一段是合法结算，不回滚。要避免把一条旅程锁死，部署必须保证
  gate 与正式口可用。
* `world_runner` / `life_heartbeat` CLI 现在需要显式的
  `WORLD_BODY_SOCKET` 与 `WORLD_BODY_INTEGRATION_MODE=canonical`
  （或用默认 socket）才能推进下一段；缺省时失败关闭而不是直写。
  这是部署面变化，RUNBOOK 未在本单更新。
* 未验证 / 未承诺：真实模型自然采用多段旅程、显式 revoke 接口
  （registry 仍只有 consumed/expired，没有 revocation）、旧实例的 blocked 旅程
  不会被自动解冻。本单只用测试夹具真实拒绝到期票与旧观察版本，不能据此声称
  已具备完整撤权治理。
* B 回归原 10 红已定位为**夹具歧义**，不是业务选择逻辑缺陷：v0.4 C 之后，同一件
  已绑定作品同时给出「续写」与「closeout」两个 PERSONAL_OPPORTUNITY 候选，而
  `StubCognition` 原来按列表首项挑，候选顺序来自集合迭代（随进程哈希变化）。
  同一个用例会随机落到 closeout（archive → `ALREADY_DONE`，内容写入器还没被叫）
  或续写；`step_path` 也因 closeout 成为 live path 变成
  `AR0_CLOSEOUT_WORLD_ROUTED`。修法是让夹具按语义表达原目标：`StubCognition`
  显式排除带 `closeout:` target 的候选；w05r 的混合夹具显式关闭 closeout 缝
  （该缝由 `test_life_closeout_cycle_v04` 单独覆盖）。原断言全部保留（真的走
  Supply owner、版本 +1、世界零移动、`AR0_WORLD_ROUTED`、候选集两条路），
  AG0/AG1/AG2/cycle 的业务选择逻辑未改。父级复跑 B：94 例 OK。
