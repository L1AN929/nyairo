# Agent Note: 最小可撤销披露许可（revocable disclosure authority）

Status: implemented

## Problem

本地分享已经有完整的草稿生命周期和发送前检查：来源版本、草稿 CAS、固定受众/渠道、重复投递拒。
但「哪一件作品的哪一个版本被允许披露给本地主人」这件事没有可撤销权限：现有 governance 只有
三种写 capability，`send_local` 前的 owner 重读只是确认来源仍可读，不是一份可撤销的披露授权。
于是「先同意、后反悔」无法在 owner 层表达：撤权只能撤回写许可，而普通读取不应被牵连。

## Decision

在既有 `GovernanceAuthority` 上增加第四种窄 capability `DISCLOSE_MANAGED_ARTIFACT`，不新建权限
owner、不新建分享正文库：scope 是结构化字符串 `disclosure:{"artifact_id":...,"version_id":...}`
（`authority.disclosure_scope` 唯一构造），target 固定 `local:owner`，authority scope 为
`life-supply.disclosure.v1`，payload 不绑定正文/草稿 id。签发/查询/撤销走既有
`issue_grant` / `list_active_grants` / `get_grant` / `revoke_grant` 与 host-operator 身份规则；
新增 `scripts/disclosure_authority_cli.py` 作为本机 operator 入口。CLI 用 `--instance` 从实例
自己的 instance 文件读 supply socket 与 subject（不读环境/密钥）；显式 `--subject`/`--socket`
与实例不符时拒绝，`--socket` 仅保留为隔离根/测试的低层入口。

owner 最终准入放在 `LifeSupplyService._op_commit_markdown`：提交内容若解析为
`nyairo.share.v1` 且新状态是 `LOCAL_DELIVERED`，owner 先从信封头得到绑定（subject、来源 artifact、
精确版本、`local:owner`、`local_inbox`），但**不把这抬头当权威**：

- 来源作品必须属于该主体，且 `source_version_id` 必须真的属于该来源作品
  （`DISCLOSURE_SOURCE_VERSION_MISMATCH`）；
- 新信封的 source / version / subject / audience / channel 必须与 canonical head 上已保存的
  草稿信封逐项一致（`DISCLOSURE_DRAFT_BINDING_MISMATCH`）；没有可发送的草稿头（第一版、
  DROP/EXPIRED/已投递头）则 `DISCLOSURE_DRAFT_BINDING_MISSING`；
- 首行声称 schema 但解析失败（重复 header / 缺 `text:` / 未知 state）具名拒绝
  （`DISCLOSURE_ENVELOPE_MALFORMED`），不把坏信封当普通文本放过。

随后在同一协调事务里对披露许可与写许可各做一次 reserve → begin_execution → consume。
`GovernanceAuthority._connection()` 在 UOW 内加入 `LifeSupplyDB.active_unit_of_work` 的连接，
所以许可读取、预留与写版本是同一个 `BEGIN IMMEDIATE` 事务；撤销是另一条 `BEGIN IMMEDIATE`，
因而在数据库写锁上串行。撤销在 begin_execution 之前落地则投递被具名拒绝；之后落地不改变已
记录回执。没有显式 grant id 时按精确 scope/target 查唯一有效许可，撤销过则区分
`DISCLOSURE_REVOKED`，绝不自动发放，也不把撤权误解成「来源读失败」。`NO_CHANGE` 重放路径只做
只读 `validate_grant`，不 reserve、不写。

等价入口同规则或失败关闭：`create_artifact` 拒绝用投递信封做第一版；`import_projection`
拒绝把外部投影文件导入成投递信封；旧 `service/server.py` 没有 operator 查询端口，对投递信封
（含坏信封）显式 `DISCLOSURE_ADMISSION_UNAVAILABLE`，普通文本行为不变。

Life 侧只做透传：`LifeArtifactPort.commit_version` / `PrivateProjectService.commit_project` /
`LifeSharingService.apply_action` 增加可选 `disclosure_grant_id`，正式 Decision/AG1 形状不变；
不传时由 owner 精确查找。分享信封 v1 格式不变，旧草稿可读、可延后、可丢弃，发送需要许可。

## Alternatives considered

1. **新建披露许可库/owner（B 方案）**：最强理由是披露语义与写许可确实不同域，独立库可以独立 schema、
   独立迁移、独立审计，不必让写路径认识分享信封。否决原因：这会成为第二个权限 owner，而本轮明确
   要复用既有 grant 生命周期；revoke/CAS/revision/receipt/fence 已经齐全，另建一套只会制造两处
   真理源和新的跨库原子性问题。
2. **只在 Life 进程缓存/预检许可**：最强理由是无需 owner 改协议，改动最小。否决：撤销后缓存仍在，
   且 owner 的实际提交没有准入；这正是要排除的 TOCTOU（监工单也明确不能只做前置 check）。
3. **把许可绑到分享草稿 id**：最强理由是同一来源版本可以有多份不同文本，绑草稿更精确。否决：批准
   范围只要求 subject + 源作品 + 精确版本 + 本地目标；绑草稿会让同版本的新草稿无法复用，且草稿 id
   在 prepare 后才存在，反而把「许可」变成「发送意图」。
4. **把披露权塞进 `COMMIT_MANAGED_ARTIFACT` 的 scope**：否决，写权即披露权会扩大现有写许可的含义，
   违反「披露权不授写权、写权不等于可披露」。

## Consequences

- 收益：撤销可以在 owner 层真实阻止后续投递；已投递回执不受影响；普通读取完全不变；没有新库、
  没有新表、没有迁移、没有第二 owner；旧信封兼容；绑定以 canonical 草稿为准，客户端自报 header
  不能换来源/版本/主体；create/import/旧 server 等价入口不再绕过许可。
- 代价/边界：本机 `SO_PEERCRED` 信任边界不变，不声称真人认证或同 uid 管理员防护；只支持
  `local:owner` / `local_inbox`；没有外发、多受众、关系语义；`life_supply.py` 的 owner 现在需要
  识别分享信封头（只读头部，不读正文，不改格式），并多读一次 canonical head 做草稿绑定。
  旧 `service/server.py` 入口没有 operator 查询端口，因此对 `LOCAL_DELIVERED` 信封**失败关闭**
  （`DISCLOSURE_ADMISSION_UNAVAILABLE`），不提供一条绕过许可的投递路径。
- 数据保留偏差：首轮施工的 `tests/test_revocable_disclosure.py` 的 `OwnerPermitTest` 曾用
  `TemporaryDirectory` + `addCleanup`，worker 实际跑过一次，该用例自动清理过自己新建的库；
  本轮已改为 `mkdtemp` 且不再注册删除，但**不能声称整个特性从未删除过测试数据**。被删的那个
  首轮 root 未恢复、未重跑。
- 未验证：跨 uid 管理员、并发撤销/提交的极端交错（只证同一事务内的串行点与真实先撤后发）、
  其它受众、完整隐私/F150/EL4、G1/24h；真实链与 CLI 见 `scripts/revocable_disclosure_demo.py`
  与 `tests/test_revocable_disclosure.py`，用法边界见
  `docs/engineering/revocable-disclosure-contract-20261029.md`。旧分享生命周期见
  [2026-11-14-v04-life-sharing-service-local-inbox](../feature/2026-11-14-v04-life-sharing-service-local-inbox.md)。
