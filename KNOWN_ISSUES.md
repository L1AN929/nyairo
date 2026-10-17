# v0.3.0 发行范围与未完成项

v0.3.0（后端连续生活版）已作为 GitHub prerelease 发行：一键安装与可校验 ZIP 指向 v0.3.0，发行附件为 `nyairo-v0.3.0.zip` 与同名 `.sha256`；旧的 v0.2.0-rc3 标签与 ZIP 保持原样。本次不切换生产实例，隔离新装与 v0.2.0-rc3 升级/回滚链已有验证，未对现有实例/生产升级或真实模型一天/24h 验收。

- 新链路已用真实本地 owner 与确定性/loopback 替身完成 51 项定向检查；没有新链真实模型一天、24h 或安装/迁移证明。
- 未交付统一 Web 生活/模型配置台、Future/Agenda 生活接线、主动外发、营养生理、NPC 自主社会、多前台并发或完整设计。
- 营业预期仅只读窗口，不是未来保证或动作授权；停留 PAUSE 未提供暂停区间扣除后的恢复计时。
- Body 正向 AVAILABLE 事实、生产 S07 评价来源仍未证；旧 blocked journey 不自动迁移。

详细范围、真实进程退出码、已更正的测试/运行器失败与剩余边界见 [v0.3 最终验收](docs/engineering/v03-final-acceptance.md)。以下保留 0.2 的历史边界，不把它们冒称为本次新验收。

# v0.2.0-rc3 维护说明（2026-10-13）

本次只修安装/更新版本错配、说明与官网交互；v0.2 自主生活、World、记忆的产品行为未扩展。
rc1 包的 `MANIFEST.version` 为 `0.2.0-rc1`，`scripts/release.json.version` 却为 `v0.1.0-rc6`：
安装后更新登记会误报 rc6，更新器会拒绝该 rc1 包。rc2 统一这些版本并增加文件清单校验。
旧 rc1 标签和 ZIP 保持原字节。以下 rc1 的能力边界与历史证据继续适用；不是本次新运行结果。

# 第一版已知边界

本版供个人体验。自动化验收的结果见 `TEST_EVIDENCE.json`，没有据此宣称所有平台或所有外部服务都已验证。

## v0.2.0-rc1 公开测试版边界（2026-10-13）

发行提交以 `v0.2.0-rc1` 标签为准，来自 `v0.2-world-integration`，未合入 `main`，未切生产。
逐项证据见 [TESTING.md](TESTING.md)。下列限制保留，不将测试版描述为稳定交付。

1. **T12：原生生活经历没有 CORRECT 入口（未实现）。** `components/native/memory_control.py:131-133`
   要求替换事件来自直接用户输入（`speaker != 'user'` 或 `source_origin != 'USER_VISIBLE_INPUT'` 直接
   `ValueError`）。生活经历没有用户事件，伪造一条用户消息会违反 E07，所以本版不实现、也不伪造。
   已通过的只有 DELETE / 停止召回那一半。**停止召回不能代替纠正**：它让旧错误不再进入视图，但不能把
   一条记错的经历改写成正确内容。
2. **已被取代的版本可能拿不到 AR-1 的观察证据（结构性，不是缺陷）。** AR-1 的成功结算要求 owner 确认的
   那个版本**就是当前 head**（`resolve_operation` + `read_head` + operation_key + content_sha256 四条）。
   一个已经不再是 head 的旧版本拿不到这份证据，于是**正确地**保持 `OPEN / UNKNOWN_EFFECT`，而不是被
   猜成成功。看到一条长期 OPEN 的旧写入时，先确认它是不是已经被后续版本取代。
3. **24h 只验证冻结自主生活基线，不是最终整包长测。** 香港 `35df37f` 窗口已观察
   `144939s`（40h15m39s）、805 个连续 tick、最大间隔 204s、8 个真实新版本，无自醒或 UNKNOWN。
   按 RC1 第一阶段的连续性与 owner 闭环标准通过；该实例未配置 World，生成世界另做隔离融合验收。
   7 天仍在观察，不是本次 prerelease 前置条件。没有重启测试，不宣称最终整包 `SOAK_VERIFIED`。
4. **systemd 范围有限。** 香港独立 owner/resident 单元实际 enabled/active、`NRestarts=0`；
   没有做机器重启或故障重启注入。WSL 的 systemd offline，不能把香港部署当所有平台验收。
5. **旧供应商密钥仍需账户持有人轮换。** 代码侧已经改好（0600 的 `model_env` 交接，命令行里不再出现
   密钥），但**旧密钥的撤销只能在供应商后台做**，代码修复不能代替。这条是账户持有人的动作，本版未做。
6. **自主 Telegram 联系不会投递。** `components/life/scripts/autonomous_life_resident.py:269` 硬编码
   `proactive_enabled=False`。本版没有任何"自主主动向用户发送消息"的能力；只有你主动发起时才有对话。
7. **自主生活第一次自主选择 World 动作不是每次都成（本次新发现，未定性）。** 同样的隔离实例连跑 5 次，
   3 次在第 1 个检查窗就选中世界候选并完成 MOVE，2 次同一个窗口给出**空候选集**
   （`RULE_RESOLVED` + `NO_LEGAL_CANDIDATE` + `candidate_refs=[]`），随后 120 分钟的间隔让那一轮不再
   重来。进程内诊断显示 World provider 连续 40 次都正常返回同一条机会、记录环节也没有报错，**根因本次
   没有定位**。所以：集成是通的，但"首次自主尝试出门"存在失败概率，不要当成稳定行为。
8. **模型观测有口径限制。** 从 `model_env` 文件载入凭据时，启动 probe 在载入前执行，可能显示
   `NOT_CONFIGURED`；环境中已有凭据则不一定如此。`model_calls` / 日预算主要计认知调用，
   内容 writer 的 `call_count` 仅成功返回时增加，失败请求会漏计，不能拿 summary 总数当完整费用。
   香港快照 summary 为 9 认知 + 8 成功内容；另有 1 次内容超时，即至少 18 次请求尝试。
9. **经历不是每个作品版本各记一条。** 香港 8 个真实版本写入仅形成 1 个 Activity milestone handoff，
   M0 有 1 条 `LIFE_VERIFIED_OUTCOME`，后续重复投递不增行。这是已知 checkpoint 粒度限制。
   通用结算 Memory 消费者仍 fail-closed：8 条 `ProductionExecutionRefused` / 8 个 pending outbox；
   正式生活经历走 AG-2 → M0 handoff，已 ACCEPT、pending 清零。不能把两条投递路径混淆。
10. **安装与发布边界。** 源码包及安装器是 Linux / WSL 路线；macOS、原生 Windows、完整 Docker
    与所有外部服务未完成同范围验收。清单和 `.sha256` 证明内容完整性，不证明发布者身份或凭据撤销。
    `scripts/bootstrap.sh` 与 `website/install.sh` 的 rc6/rc9 漂移已在 `35df37f` 修复，不是当前缺陷。

## 历史边界（v0.1 及更早）

- 单独的统一核心候选仍未接管现役聊天；本体验版采用标准 Hermes 与千代插件装配。Alpha 的独立策略验收代码包含，不等于自主执行已启用。
- 自主活动执行、主动 Contact、N8 撤销权限的正式 owner 接口未交付。认知只运行 Shadow，判断不改变活动、发送消息或执行动作；通用安装默认关闭模型费用。
- Life Supply 新 Open Inquiry 入库返回 `INQUIRY_ADMISSION_OWNER_STUB`。个人 Workspace 和经正式 Governance 授权的资源文档保存/读取可用；机会集合可以为空。
- World/Body 在标准插件中提供同账号只读观察，不开放动作和管理员端口。独立初始世界为卧室、站姿、空物件集合，不能把源码中已有动作模块当作体验号已开放的功能。
- Telegram 独立体验实例已接入。QQ、个人微信、企业微信、飞书没有用真实账号完成收发与重连验收。Windows、macOS、Hermes 桌面端、Docker、JavaScript 全套以及所有可选外部服务未完成整套验收。
- 个人长期记忆只把用户入站作为直接证据。Hermes 生成完成钩子不等于平台送达回执，不伪造助手消息送达证据。
- 一个进程装配一个个人配置；开关和服务地址修改后需要重启。群聊、未绑定用户、委派 Agent 不获得个人记忆写入权限。多模态消息暂不形成文本长期记忆。
- 纠正/删除停止采用旧事实，保留原始聊天与审计。存在控制记录时保守排除旧短期历史，可能同时失去该段历史里未删除的近期话题。资源文档独立管理，记忆删除命令不删除文档。
- 启用记忆后，模型工具默认全部拦截，包括终端、文件、浏览器和委派；主动选择 unrestricted 后不能承诺遗忘不会被这些工具绕过。本地管理员仍能访问原始聊天、审计或备份。
- 标准插件默认保守组织经历；模型驱动 M1/M2 的通用安装需要额外配置。原日常 Native 实例的自然聊天最终验收按作者要求暂缓。
- 平台未提供稳定消息标识时，漏掉完成钩子后的相同文本重发与重试不能完全区分。第一版不宣称跨平台严格 exactly-once。
- rc6 内置 Hermes 的直接更新会被整包保护拦住。Linux / WSL 使用 `nyairo update` 接收配套发行；0.21.5 尚未完成适配，macOS / 原生 Windows 暂用手动方式。独立 World / Supply 服务与外部数据根需要单独处理，见 [更新教程](UPDATE.md)。
- 原有凭据在供应商和 BotFather 的撤销状态未验收。包不含凭据或私人数据。作者已确认新增代码（包括 Life Supply）使用 Apache-2.0，来源与第三方范围见 NOTICE。

普通账号运行有一个需要 root peer 的底层 socket 测试按权限条件跳过；已用隔离 root 进程另行执行。上游默认套件也包含环境/可选依赖跳过，数量在验收记录中独立列出。

代码维护方面，保留独立组件与 owner 边界，用标准插件装配，记录所有宿主改动。标准实例装配已改为显式传递配置，独立组件的兼容入口仍可读取进程环境与旧目录布局；这是技术债，未把第一版做成支持多个人 profile 热切换的新框架。

## 公开候选的使用问题

2026-10-04 的公开候选复核还发现：旧 rc4 在关闭全部附加模块时可能丢失短期上下文，记忆帮助前缀也有错误；两项已在 rc5 修复。Supply 默认 socket 的访问安排仍阻碍两个不同普通身份连接。具体影响、配置条件和处理办法见 [遇到问题怎么办](https://nyairo.com/#troubleshooting) 与 [资源服务连接问题](https://nyairo.com/#modules-section-3)。这些是使用问题，不把有意延期的自主活动等列成第一版缺陷。

历史 v0.1.0-rc4 标签和 ZIP 保持原字节；主分支的改动不代表旧包中的问题自动消失。网站与 README 的说明更新也不等于运行时修复。

rc5 的 Native HTTP 接口需要访问口令，默认只监听本机。日志不再输出上游错误正文或原始 Telegram 身份引用；受限私有账本仍保存去重所需的引用。清单与扫描证明已定义范围内的文件一致和规则通过，不证明发布者身份、凭据撤销或所有未知隐私问题都已排除。详见 [本次隐私复核](PRIVACY_REVIEW.md)。

## v0.2 自主生活（v0.2.0-rc1 公开测试版）的已知边界

这一节针对分支 `v0.2-autonomous-life`（本批记录提交 `c4671c9`，即 RC1-02 之后的 HEAD；B8-6A 那一轮
的提交是 `e471241`）的自主生活能力。**它已作为 v0.2.0-rc1 公开测试版发布**（2026-10-13），但没有切换到任何现有用户的实例；
逐能力证据等级见 [验收与复测](TESTING.md) 的 E13 门禁。以下都是实测到的真实边界，不因为"是开发
分支"就放宽。每条都标了**当前状态**（RC1-01/RC1-02/RC1-03 之后），历史发现的原话保留不改写。

- **T12 未完成：原生生活经历没有 CORRECT 入口。** 现有 Memory Control 只支持**用户撰写事件**的纠正（`memory_control.py:132-133` 要求替换事件来自用户）。生活经历没有用户事件，伪造一条用户消息会违反 E07，所以本批不实现、不伪造。已通过的只是 DELETE / 停止召回那一半（`MEMORY_CHECK [5]`）。**DELETE / 停止召回不能代替 CORRECT**：它让旧错误不再进入视图，但不能把一条记错的经历改写成正确内容。要补的是一个受治理的生活经历纠正入口，属独立工作包。
- **T06 / T07 仍有验收缺口。** 有前台 Activity 时 AG-1 会按规则先解析（走 `CONTINUE` 路径），不会把"暂停 / 放弃"交给模型。因此只证明了 T06 的 checkpoint 保留 + 聊天正常，以及 T07 的作品保留 + 不自动恢复；**"由一次真实决策触发暂停 / 放弃并让 Activity 退役"这条路径没有被覆盖**。要走通它需要一条 `decision=ABANDON` 且 Activity 转为非 ACTIVE 的真实回执。
- **T13：已发授权被在线撤回之后的完整路径（当前状态：代码口径已变，但撤回路径仍未复测）。** 当时（E13）
  的发现原话：已验证的是"owner 不认识的 grant 不能写、历史作品仍在"（受控替身）与"未获授权的写入被
  拒"；E13 另外实测了**在线撤回一枚已发 grant**：撤回本身返回 `REVOKED`，随后同一授权写请求被资源服务
  拒绝（`GOVERNANCE_REFUSED` / `reserve_use refused`），作品 head 不变、0 次模型调用。**但**那枚"明确
  拒绝"经 Life 侧窄端口（`life_artifact_port._outcome()`）分类时是 `effect_state: UNKNOWN`（端口当时只把
  `BLOCKED` / `DENY` 当明确拒绝），所以"撤回后整条自主路径会怎样报告"没有验证。**RC1-03 复核 HEAD 代码
  后的现状**：B8-8 给 `_outcome()` 加了 `reason == GOVERNANCE_REFUSED → REFUSED` 这一支
  （`life_artifact_port.py:440-451`），而撤回授权在 reserve 步返回的正是
  `status=REVOKED, reason=GOVERNANCE_REFUSED`（`life_supply.py:1184`），**按现在的代码它会落进
  `REFUSED`，不再是 `UNKNOWN`**。但这条**在 B8-8 之后没有重跑过**，所以只能说"当前代码会这样分类"，
  仍然**不得**写成"撤回路径已验证"。
- **长测未达 24 小时 / 7 天（当前状态：仍在跑，判定 RUNNING）。** 实时状态是
  `SOAK_RUNNING_24H` / `SOAK_PENDING_7D`。RC1-03 只读复核（快照 `2026-10-11T09:56:56Z`）：真实起点
  `2026-10-11T01:41:31+00:00`、本进程 `01:46:04`（`starts=2`，中间只断了 53 秒）、最后 tick
  `09:55:28+00:00`，`observed_seconds = 29637s ≈ 8.23h`，进程仍在跑（pid 2728）。
  **这个标签只由墙钟时长推出，不构成"24 小时已满足"的证据**：真实有效观察范围是
  `01:41:31 → 09:55:28`（唯一空档 53s，无进程退出、无"没人跑"的长空档），所以按真实记录判定仍是
  **RUNNING**，不是 PASS。另外，正在跑的这份实例用的是 `e471241` 那份代码
  （`/srv/nya02/resident2/app`，见 `CODE_COMMIT`），**不含** RC1-01/RC1-02 的修复，所以即使它跑满
  24 小时也不能用来验证 RC1。修复前发现的自我唤醒故障记录不与修复后的时段拼接成一段"完整无故障
  长测"。7 天观察不提前判定。
- **旧供应商密钥仍待轮换。** 早期驱动脚本把 provider 密钥放在进程命令行上（`runuser … env NYA02_MODEL_API_KEY=…`），`ps` 对全机可见。代码路径已改为 0600 文件交接（`model_env` + `load_model_env_file()`），但**旧密钥的撤销与轮换由账户持有人在供应商后台完成**，代码修复不能代替它，本批也没有动任何凭据。
- **systemd 单元未在真实硬件验证。** 本机 systemd 为 `offline`，所以常驻进程用的是 nohup + pidfile；生产用 systemd 单元只是建议稿，没有做过开机自启、崩溃重启、权限与依赖顺序的实际验收。
- **三个环境相关失败套件保持 FAIL（当前状态：RC1-03 已把原因查清，两个是环境、一个是真实缺陷）。**
  `python3 scripts/test.py` 的 `FAILED_SUITES: ['host-boundaries', 'cognition-faults', 'Hermes']`。
  RC1-03 在**只装测试依赖的隔离 venv** 里按原命令重跑（未删测试、未改产品行为、未加无条件跳过），
  真实结果：
  * `cognition-faults`：缺 `pytest`。装上后 **1 passed**。
  * `Hermes`（4 个 context-engine 测试文件）：缺 `python-dotenv`（连带 `requests` /
    `httpx[socks]` / `pytest-asyncio` / `ruamel.yaml` 警告）。装上后 **29 passed, 0 failed**。
  * `host-boundaries`：缺 `httpx` / `psutil` / `PyYAML`（装上后错误从 11 降到 0），其余失败来自
    **工作区 CRLF**：仓库根目录没有 `.gitattributes`，检出成 CRLF 后 `scripts/bootstrap.sh` 在 bash
    下直接报 `line 3: set: pipefail / : invalid option name`（6 个测试），
    `patches/baseline.sha256` 校验失配（1 个），封存组件
    `sealed_component_mismatch:production_consumption_evaluator_v0_3.py`（2 个；把 CRLF 归一成 LF 后
    哈希与 `production_resolver.SEALED_SHA256` **逐字符相等**）。把 HEAD 导出成 LF 树（`git archive`）
    后只剩 **1 个真实失败**：`test_website_and_repository_installers_are_identical` 要求
    `scripts/bootstrap.sh` 与 `website/install.sh` 逐字节相同，而前者固定 `v0.1.0-rc6`、后者固定
    `v0.1.0-rc9`（只差 2 行）。这是**真实的一致性问题**，不是环境问题，且改它等于做一次发行决定，
    本批只记录、不改。详情与命令见 `docs/engineering/v02-rc1-release-readiness.md`。（RC1-04 已修：`scripts/bootstrap.sh` 与 `website/install.sh` 现在都固定 `v0.2.0-rc1`，逐字节相同，`host-boundaries` 的安装器一致性用例相应通过。）
  **没有为了报告变绿而添加无条件跳过或重构安装器**，也没有把这三个套件的失败改写成通过。
- **正式发布 / 生产切换未获授权。** 本批没有创建标签或 Release，没有改动 `main`，没有部署任何东西；发布与切换需要单独授权与单独验收。

### E13 走通中另外实测到的报告与接线缺口（逐条标当前状态）

以下四条是 E13 当时的原始发现，原话保留；每条前面加了 RC1-03 复核的**当前状态**。

- **[仍未修] `NYA02_SUPPLY_SOCKET` 不在 `autonomous_life_assembly.SUPPLY_SOCKET_ENV` 接受的名字里**
  （只接受 `CHIYO_NATIVE_LIFE_SUPPLY_SOCKET` / `LIFE_SUPPLY_SOCKET` / `CHIYO_LIFE_SUPPLY_SOCKET`）。
  `run_resident.sh` 只显式传前者，一直能跑是因为 `runuser -- env` 保留了启动者环境里的
  `LIFE_SUPPLY_SOCKET`；在没有 `env.sh` 的新目录里，常驻进程会以 `SUPPLY_CONFIGURATION_MISSING` 拒绝
  启动。
- **[已修 · RC1-02] 常驻进程启动记录里的 `model.content_writer` 恒为 `false`。** 当时的现象（原样
  保留）：`autonomous_life_resident.start()` 读 `assembled.get("writer")`，而装配函数的返回字典没有
  这个键；写作者实际存在（那一轮写出的正文是真实模型散文）。**现在**：改读已装配 cycle 自己的
  `writer`（`getattr(assembled.get("cycle"), "writer", None)`），配了真实模型时报 `true`，没有可用
  写作者时装配在 `NO_CONTENT_WRITER` 处 BLOCKED 且返回记录里没有 `model` 键。修的是**报告**，生成链
  未动；零次真实模型调用即可复现（证据：`docs/engineering/v02-autonomous-life-implementation.md §11.1`）。
- **[仍未修 · RC1-03 只加了显示标签] 每日预算与 `counters.model_calls` 只统计决策调用。** 一次成功
  写入还会再发生一次内容生成调用（`purpose=artifact_edit`），不计入该数字；`RoundBudget` 的注释说
  两者都计入，与实际不符。RC1-03 用真实记录量到的数是：真实长窗 4 次决策 + 4 次内容生成 = **8 次真实
  provider 请求**，而 `counters.model_calls = 4`、`cognition_used = 4`。**没有加新的预算框架**，只在
  `resident_summary.json` 里加了 `model_calls_detail`（`cognition_calls` / `content_generation_calls` /
  `total_requests` / `daily_budget_counts: "cognition calls only"`），让"6 次认知调用/天"不会被读成
  "一天总共 6 次请求"。预算语义本身没有改。
- **[仍未修] 生活经历写入 M0 时使用事件自己的 `subject_id`**，而 AG-2 的 handoff 不带它，于是回落到
  `chiyo`。隔离 profile 的 Supply subject 不是 `chiyo` 时，按 subject 读经历会读到空视图（实测
  `subject=chiyo-install → 0`，`subject=chiyo → 1`）。单一主体的真实产品里两者同名，不会碰到。

### RC1-03 只读复核（2026-10-11）新增的记录

- **[已修 · RC1-01] AR-1 现在能结算一次真实写入。** 当时的现象（原样保留）：owner 真的提交了版本、
  AR-0 报 `FILE_ACTION / SUCCEEDED`，AR-1 却停在 `UNKNOWN_EFFECT / OPEN`；真实长窗的 4 次写入在
  `result_settlement_journal.jsonl` 里**全部**是 `outcome_kind=UNKNOWN_EFFECT`、
  `settlement_status=OPEN`。原因是 AR-0 的回执是 `PROVIDER_REPORTED`，而 AR-1 的 FILE_ACTION 成功
  分支只接受 `AUTH_GRADE_OBSERVER_VERIFIED`。**现在**：owner 确认版本后，ManagedArtifact 适配器用同一
  个官方读端口重读该版本（`resolve_operation` + `read_head`），四条全中才记成已验证，AR-1 再按自己的
  规则结算；AR-1 的判定没有被放宽。**保留的边界**：一个**已经不再是 head 的旧版本**可能拿不到这份
  观察证据（旧写入正确地不产生证据、保持 OPEN，而不是被猜成成功），所以"每一次历史写入都能补结算"
  这句话不成立。
- **[仍未修 · 仓库工具链] 仓库根目录没有 `.gitattributes`。** `website/.gitattributes` 写了
  `* text=auto eol=lf`，所以 `website/**` 在 Windows 检出后仍是 LF；根目录没有，于是同一棵树里
  `scripts/bootstrap.sh` 是 CRLF、`website/install.sh` 是 LF。后果是真实可复现的：CRLF 的
  `bootstrap.sh` 在 bash 下直接报 `set: pipefail / : invalid option name`（6 个安装器边界测试），
  `patches/baseline.sha256` 与封存组件哈希失配（3 个测试）。**这不是产品缺陷，是检出配置缺陷**，
  但在 Windows 上会把 `host-boundaries` 变成红的；修法是给根目录加同样的 `* text=auto eol=lf`
  并重新检出，属独立工作包。
- **[仍未修 · 发行决定] `scripts/bootstrap.sh` 仍固定 `v0.1.0-rc6`，`website/install.sh` 固定
  `v0.1.0-rc9`。** 两个文件除这 2 行外逐字节相同，而测试要求它们完全相同。用户从仓库里跑
  `bash scripts/bootstrap.sh` 会装到 rc6，从网站装的才是 rc9。改它等于决定"仓库内脚本指向哪个发行"，
  RC1-03 只记录、不改。（RC1-04 已修：两份脚本统一固定 `v0.2.0-rc1`，逐字节相同；从仓库跑 `bash scripts/bootstrap.sh` 与从网站装到的现在是同一版。）
