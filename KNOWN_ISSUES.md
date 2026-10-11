# 第一版已知边界

本版供个人体验。自动化验收的结果见 `TEST_EVIDENCE.json`，没有据此宣称所有平台或所有外部服务都已验证。

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

## v0.2 自主生活（开发候选）的已知边界

这一节针对分支 `v0.2-autonomous-life`（本批记录提交 `e471241`）的自主生活能力。**它没有发行**：没有标签、没有 Release、没有部署给任何用户；逐能力证据等级见 [验收与复测](TESTING.md) 的 E13 门禁。以下都是实测到的真实边界，不因为"是开发分支"就放宽。

- **T12 未完成：原生生活经历没有 CORRECT 入口。** 现有 Memory Control 只支持**用户撰写事件**的纠正（`memory_control.py:132-133` 要求替换事件来自用户）。生活经历没有用户事件，伪造一条用户消息会违反 E07，所以本批不实现、不伪造。已通过的只是 DELETE / 停止召回那一半（`MEMORY_CHECK [5]`）。**DELETE / 停止召回不能代替 CORRECT**：它让旧错误不再进入视图，但不能把一条记错的经历改写成正确内容。要补的是一个受治理的生活经历纠正入口，属独立工作包。
- **T06 / T07 仍有验收缺口。** 有前台 Activity 时 AG-1 会按规则先解析（走 `CONTINUE` 路径），不会把"暂停 / 放弃"交给模型。因此只证明了 T06 的 checkpoint 保留 + 聊天正常，以及 T07 的作品保留 + 不自动恢复；**"由一次真实决策触发暂停 / 放弃并让 Activity 退役"这条路径没有被覆盖**。要走通它需要一条 `decision=ABANDON` 且 Activity 转为非 ACTIVE 的真实回执。
- **T13 仍需测试：已发授权被在线撤回之后的完整路径。** 已验证的是"owner 不认识的 grant 不能写、历史作品仍在"（受控替身）与"未获授权的写入被拒"；E13 另外实测了**在线撤回一枚已发 grant**：撤回本身返回 `REVOKED`，随后同一授权写请求被资源服务拒绝（`GOVERNANCE_REFUSED` / `reserve_use refused`），作品 head 不变、0 次模型调用。**但**这枚"明确拒绝"经 Life 侧窄端口（`life_artifact_port._outcome()`）分类时是 `effect_state: UNKNOWN` —— 端口只把 `BLOCKED` / `DENY` 当明确拒绝，`REVOKED` 落进了"未确认"。所以"撤回后整条自主路径会怎样报告、是否会走对账并最终定性为拒绝"**没有验证**，不得写成"撤回路径已验证"。
- **长测未达 24 小时 / 7 天。** 实时状态是 `SOAK_RUNNING_24H` / `SOAK_PENDING_7D`（观察起点 `2026-10-11T01:07:06+00:00`）。**任何文档都不得写 `SOAK_VERIFIED`**；修复前发现的自我唤醒故障记录不与修复后的时段拼接成一段"完整无故障长测"。
- **旧供应商密钥仍待轮换。** 早期驱动脚本把 provider 密钥放在进程命令行上（`runuser … env NYA02_MODEL_API_KEY=…`），`ps` 对全机可见。代码路径已改为 0600 文件交接（`model_env` + `load_model_env_file()`），但**旧密钥的撤销与轮换由账户持有人在供应商后台完成**，代码修复不能代替它，本批也没有动任何凭据。
- **systemd 单元未在真实硬件验证。** 本机 systemd 为 `offline`，所以常驻进程用的是 nohup + pidfile；生产用 systemd 单元只是建议稿，没有做过开机自启、崩溃重启、权限与依赖顺序的实际验收。
- **三个环境相关失败套件保持 FAIL。** `python3 scripts/test.py` 的 `FAILED_SUITES: ['host-boundaries', 'cognition-faults', 'Hermes']`：`cognition-faults` 因为 `vendor/hermes` 没有 pytest 虚拟环境；`host-boundaries` 是安装器 / 更新 / 文件系统安全测试；`Hermes` 是 vendor 测试运行器本身失败（插件加载正常）。**没有为了报告变绿而添加无条件跳过或重构安装器**，也没有把这三个套件的失败改写成通过。
- **正式发布 / 生产切换未获授权。** 本批没有创建标签或 Release，没有改动 `main`，没有部署任何东西；发布与切换需要单独授权与单独验收。

### E13 走通中另外实测到的报告与接线缺口（未修）

- `NYA02_SUPPLY_SOCKET` 不在 `autonomous_life_assembly.SUPPLY_SOCKET_ENV` 接受的名字里（只接受 `CHIYO_NATIVE_LIFE_SUPPLY_SOCKET` / `LIFE_SUPPLY_SOCKET` / `CHIYO_LIFE_SUPPLY_SOCKET`）。`run_resident.sh` 只显式传前者，一直能跑是因为 `runuser -- env` 保留了启动者环境里的 `LIFE_SUPPLY_SOCKET`；在没有 `env.sh` 的新目录里，常驻进程会以 `SUPPLY_CONFIGURATION_MISSING` 拒绝启动。
- 常驻进程启动记录里的 `model.content_writer` 恒为 `false`：`autonomous_life_resident.start()` 读 `assembled.get("writer")`，而装配函数的返回字典没有这个键。写作者实际存在（本轮写出的正文是真实模型散文）。
- 每日预算与 `counters.model_calls` 只统计**决策**调用；一次成功写入还会再发生一次内容生成调用（`purpose=artifact_edit`），不计入该数字。`RoundBudget` 的注释说两者都计入，与实际不符。
- 生活经历写入 M0 时使用事件自己的 `subject_id`，而 AG-2 的 handoff 不带它，于是回落到 `chiyo`。隔离 profile 的 Supply subject 不是 `chiyo` 时，按 subject 读经历会读到空视图（实测 `subject=chiyo-install → 0`，`subject=chiyo → 1`）。单一主体的真实产品里两者同名，不会碰到。
