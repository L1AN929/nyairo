# G1既有同作品闭环：限定真实模型采用计划

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

状态：本人2026-10-30本窗口统一授权至17:00；监工据此选定本单实现、离线检查后唯一运行。不是昨日历史因果实验续跑。

## 目标与结论边界

在全新实例中，让真实模型自己选择并真实写出作品，再经历明确的operator中断、有序跨PID重启、真实写入丢回执UNKNOWN原操作对账、一次并发head冲突，以及模型自选NO_ACTION后的合法续作。所有决定与内容均真模型，不能脚本指定CONTINUE/NO_ACTION或事后改决定。
G1并不要求两个作品同时可START；不修改生产规则来制造它。历史干预因果属G2，本单不做影子40次比较。

## 固定运行边界

- 新root候选 `/root/nyairo-g1-real-20261030`，run `run:g1-real-20261030`，域 `g1:real:20261030`；创建前现场核不存在，存在就停，不换名追成功。
- 同根两PID连续，所有决定/内容/失败共用持久请求账本，总新增硬cap20；历史保守98/300，最坏118，不退款。其它窗口若并发或历史漂移先停核。
- 真实业务时钟不快进；总运行窗口1800真实秒（包括跨PID停启和等待），受今日17:00更早截止约束。未用请求不续杯，唯一run不得自动重启/补跑。
- provider沿既有custom，base `https://gw.example.invalid/v1`，model `cn:deepseek-v4.1-flash`，温度0、非stream、decision输出cap4096/content2048，不传seed/top_p。
- 密钥仅父级真实run同shell source `/root/.nyairo-model.env`，不读取/回显/落报告；worker全程0产品。
- 默认World gate不动；无World动作必需。本单不外发消息、不碰旧root、不删数据、不提交推送部署、不改安全设置。

## 阶段与故障注入

1. 标准新实例/真实Supply/空Memory装配，使用事前固定的中性operator材料，不灌过去。模型未创建可续结果则按cap/时限停。
2. 首个真实owner新版本且有正式Activity的作品机械定义A；模型选择不符、自然PAUSE/ABANDON/终态/无变化都原样保留，不清资格或强制改状态。B若作为对照必须明确operator创建，不能称自然成果。
3. A有真实结果后由实验operator用既有全局pause接口中断，正规stop/drain/释放lease退出P1。P2同根读回pause/停点，暂停观察0请求，operator解除后由新模型决定是否续同Activity A。此中断是实验干预，不是模型自主PAUSE。
4. 只在下一次真实模型决定产生A的合法写入时，既有adapter单次丢回执注入；效果必须真落owner，Action保留UNKNOWN。只查询同execution/operation，不裸重发，不凭正文hash认领。没有触发就未覆盖。
5. 之后另一合法A写入的submission fence后、owner调用前，一次性由同owner另一writer推进head（固定operator并发内容，明确非主体经历），使原真实提交携旧expected_head并被真CAS拒。早预检拒绝就记CAS未触达，不造结果。
6. 由后续真实新决定自然续A，不强制。NO_ACTION只有模型实际选择且无附带写才计相应场景；静默QUIET不算。其后续作也只观察，不强行解除reconsideration或跳TTL。
7. 连续组合中缺任一场景则分别标未覆盖，整体G1不升绿；网络/供应商故障、权限安全拒绝、坏账、错绑定立即停，不诊断额外调用、不重试追绿。

## 发送安全与最小检查

所有真实transport attempt发送前同账本reserve，跨PID不重置/复制cap；无自动schema/network重试。最小安全白名单只记IDs、枚举、计数、参数及故障注入标志，不输出prompt/completion/reasoning/私文/headers/env或key。
离线只跑本单新入口offline-check，假transport硬断HTTP、实际既有owner/runtime/runner门与跨PID装配，检查cap到限零发送、deadline每发送前拒、同根账本不退、UNKNOWN/CAS注入只作用于合适A写且各一次、模型非目标选择不被覆盖、QUIET不冒NO_ACTION、正规stop与safe输出。无全库/旧绿重复矩阵。
父级检查通过后prepare一次、audit0、唯一run。此前接收的旧G1脚本链不改，用来复用端口而不是伪装真模型。

## 停止与报告

旧root/状态机/owner合同/准入规则/授权或安全变更均为拐点，具名停报。若现成接口不能做到不操纵模型选择，交未覆盖/受阻，不改研究问题。
最终分列：自然决定与真实结果、operator故障干预、跨PID承接、未覆盖环节、实际持久预留/可证发送/响应。新根与所有失败保留；不因部分成功报完整G1，不把失败说模型没有能力。

## 实装（2026-10-30，只同步细节，不改上面的路线）

- 新入口 `scripts/g1_real_adoption.py`（`offline-check` / `prepare` / `audit` / `run`），
  用法与边界见 [runbook](g1-limited-real-adoption-runbook-20261030.md)。
  root/run/域、20 cap、1800 真实秒窗口、provider/温度/非 stream/4096·2048、
  “模型自选、不覆盖、没出现记 NOT_COVERED”全部按本计划；没有改生产默认或 owner 合同。
- 运行窗口用 `monotonic` + 墙钟双写，父进程在首个请求前持久写到根里，P1/P2 都只读；
  `started` 同样先落盘，任何中断/失败之后 `run` 都拒绝重开（不换 root、不换 run）。
- **固定的实例参数**：`CHIYO_AUTONOMOUS_CHECK_INTERVAL_MINUTES=1`，在 `prepare` 的 pre-reg 里
  写明。真实 run 的唯一决策触发是生产 `runner_tick` 自己的 due/事件门；1 分钟是让 1800 秒
  窗口里存在多个**合法**检查窗口，而不是每轮换 trigger。业务时钟不快进。
- 故障注入严格落在既有 `adapter.submit` 边界：丢回执沿用
  `fault_injection_drop_response_after_write`，并发 head 用同一 owner 的另一个真实 writer；
  两者各只一次，只对作品 A 的 commit 写入生效，没触发记 0。
- **判定必须带对应权威事实**（2026-10-30 收尾补齐，不改本计划的路线）：
  P1 的 A 必须是本轮 `owner_version_created=true`、本轮 receipt 指向该作品与正式 Activity、
  且 owner 当前 head 就是本轮新版本；不拿不相关 changed 或预存版本冒成果。P2 只有作品与 P1
  Activity 精确匹配才推进。UNKNOWN 必须本次注入确实触发、原 operation 查到
  `CONFIRMED_SUCCEEDED` 且有可核验证据；CAS 必须注入的并发写真的落 owner、head 真的推进，
  且原 Action 的权威原因是 `CONFLICT`；任意 FAILED/GATE_OFF/先验拒绝都不算。NO_ACTION 要
  模型自己的枚举 + 无写入/无 Action/无 receipt。
- 发送口抛异常（含 `content.complete`）先关整批锁再抛出；停因同时落盘，P2 的新锁读到同样
  拒发，跨阶段失败不因换进程复活。
- 离线检查：HTTP connect 硬拒（AF_UNIX owner 通信照常），决策/内容为明确替身；覆盖跨 PID
  共享 20 cap、每次发送前的截止/失败门、UNKNOWN 只对原 operation、CAS 真拒且只注入一次、
  prepare/run 开关、audit 安全字段与正规停 owner（exit 0、无活 listener）。离线用夹具时刻
  直接走正式 `evaluate_once`，不据此声称自然等待或真实模型成功。

