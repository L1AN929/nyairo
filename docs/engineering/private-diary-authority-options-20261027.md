# 私人日记可信身份：现状核对与待批方案（2026-10-27）

状态：**方案 B 已由本人直接批准并实施（2026-10-27）**。落地决定与取舍见
[phone-diary-capability note](../../.agents/notes/implemented/feature/2026-10-27-phone-diary-capability.md)。
本文保留批准前的核对事实与方案边界；实施只覆盖手机日记的正文读取与主体写入，
不是 S14/S13 整体完成，也不承诺“仅她可读”。
范围：S14 F-149～F-154、S13 F-140～F-142，落到手机日记的读写链。

## 一、结论

- **写入侧已有真实可信机制，且已经接在日记上**：日记写入走既有
  `Governance permission_grants` 授权 + `ManagedArtifact` 的 `WriterFence` epoch 围栏，
  不是靠 `X-Phone-Actor` 决定能不能写。S13 F-142、S14 F-149/F-151 的写入部分不需要新造。
- **读取侧没有服务端可核验的 model/operator 身份**。`X-Phone-Actor: model` 仍是客户端声明；
  电话 HTTP 面唯一的服务端秘密是 CSRF token，但它由 `GET /api/phone/state` 发给任何回环客户端，
  只能防跨站请求，不能证明调用者是谁。
- 因此按任务停止门：**不新增 token/auth store/认证 issuer/权限规则**，交本方案待批准。
  没有写“伪 header 仍可读”的绿色 demo 冒充修复。

## 二、已核事实（带路径）

### 2.1 电话 HTTP 面

- `components/phone/server.py:588-596` 只校验 `Host` / `Origin` / `Sec-Fetch-Site`，
  都是传输/浏览器边界，不是调用者身份。
- `components/phone/server.py:609-618` 的写请求要求 `X-CSRF-Token` 与进程内
  `csrf_token` 常量时间比较；该 token 在 `components/phone/demo.py:1212` 每次 serve 随机生成，
  并由 `components/phone/server.py:1425-1432` 的 `GET /api/phone/state` 返回给**任何通过回环
  Host/Origin 检查的客户端**。所以它是 CSRF 防护，不是身份凭据。
- `components/phone/server.py:657-661` 的 `_actor()` 只读 `X-Phone-Actor` 请求头；
  未知值一律落成 `user`。
- `components/phone/server.py:1684-1697` 的日记正文开关就是
  `include_text = self._actor() == ACTOR_MODEL`。没有第二道服务端校验。

### 2.2 模型工具侧

- `chiyo_bundle/phone_tool.py:430-470` 的入口授权（`cli_owner`、`local_owner_uid`、
  gateway DM 绑定）在**工具自己的进程里**执行，电话服务端看不到、也不复核。
- `chiyo_bundle/phone_tool.py:492-503` 只发送 `X-Phone-Actor: model`（以及有 CSRF 时的头），
  没有可被服务端验证的签名或共享秘密。

### 2.3 写入侧已经接好的 owner 机制

- 手机服务把 `workspace_id` / `artifact_grant_id` 传给 `PrivateDailyItems`
  （`components/phone/service.py:588-593`），日记提交经 `LifeArtifactPort`
  （`components/life/scripts/life_artifact_port.py`）走 Life Supply Unix socket。
- Supply 的 `commit_markdown` / `create_artifact` 在同一个 unit of work 里先 `_reserve`
  Governance grant，再提交 artifact
  （`components/supply/lifesupply/service/life_supply.py:1793-1850`、`:1407-1411`）。
  grant 字段包含 grantor、subject、capability、scope、target、payload_binding、
  valid_from/expires_at、revision、revocation_state/revoked_at、use_mode/max_uses、
  authority_scope、meaning_version、caller_principal、writer 字段
  （`components/supply/lifesupply/governance/authority.py:151-175`）。
- `ManagedArtifact` 的 `WriterFence` 在服务启动时取得单调 epoch
  （`components/supply/lifesupply/service/life_supply.py:506-521`），
  并对 `managed_artifacts` / `artifact_versions` 等表安装 SQLite 语句级写守卫
  （`components/supply/lifesupply/artifact/store.py:228-229`）。旧 epoch/旧实例/第二 writer
  的写入会被 `STALE_WRITER` / `WRITE_FENCE_HELD` 拒绝，拒绝发生在业务提交前。
- 这解释了为什么日记写入已经受 grant + fence 保护：日记是 private-items artifact 的一个
  item type，不新建 owner、不旁路 Supply。

### 2.4 服务端已有的其它身份（都不是 phone 的 model/operator 身份）

- Supply Unix socket：`SO_PEERCRED` 推出 peer uid，再映射成
  `KIND_HOST_OPERATOR` / `KIND_SERVICE_PRINCIPAL`
  （`components/supply/lifesupply/service/life_supply.py:451`、`:609-624`、`:277-281`）。
  它只说明“调用者是电话进程这个 uid”，不区分该进程里是模型工具还是操作者页面。
- 原生运行时 HTTP：`components/native/src/app/server.py:20-30`、`:140-149` 有
  `Authorization: Bearer <CHIYO_NATIVE_AUTH_TOKEN>` 的真实服务端校验，但那是**另一个
  服务、另一套配置**，电话服务没有接它。
- World 服务：`SO_PEERCRED` uid 门，同样是进程级，不区分模型/操作者。

### 2.5 设计六项对照（只写现状，不冒全过）

| 条目 | 现状 | 缺口 |
|---|---|---|
| F-149 长期范围授权 | 写面已有 Governance grant（capability / scope / target / payload binding / revision / expiry / revocation） | 读面没有对应 grant；grant 的 grantor 是服务 principal，不是模型/操作者身份 |
| F-150 披露 audience 绑定 | grant 表没有 `audience` 字段；日记当前没有分享/披露入口 | 未实现；不能拿日记“没有受众”冒充六项全过 |
| F-151 撤权与执行竞态 | 写面已有 revoke/revision/reserve/begin/consume，owner 在 commit 前重验 | 本轮未对日记单独演示撤权；不把既有机制当成已验收 |
| F-152 读取范围和私密边界 | operator 默认扣留；主体正文只靠 `X-Phone-Actor` 声明 | **阻塞点**：服务端没有可核验的 model/operator 凭据 |
| F-153 忘记请求的授权接纳 | 未找到 forget/hold 的正式接口 | 未实现；其身份前提也缺 |
| F-154 删除 generation 屏障 | Memory 侧有 retained deletion authority 概念；日记/ManagedArtifact 未接 | 未实现；任务明确禁止本轮改 Memory 权威 |

S13：F-140 只在 Supply/World 的进程级端点上存在，phone HTTP 没有；F-141 有
`WriterFence.instance_id + epoch`，但没有 `originBranchId / runId`；F-142 已在日记写路径上，
本轮实测了“当前 writer 经真实日记写路径可写”，以及既有 fencing 测试里的第二 writer 拒绝和
旧 epoch `STALE_WRITER` 零副作用；没有单独对 `artifact_versions` 做 stale 写入演示。

### 父级收口限定

以上源码定位和两条命令结果是 worker 的核查与运行报告；父级读取了交付与改动范围，没有逐行复核业务实现，也没有重跑这些已绿套件。现有 grant/fence 证明 owner 的提交边界，不证明 HTTP 发起者身份，不能据此宣称未授权客户端无法借 phone 的既有 grant 写入日记。读取鉴权漏洞本单未修复。

方案 B 推荐每次 serve 轮换、无凭据时私人正文 fail closed、先只绑定 subject 与实例 endpoint，不新增 gateway DM 绑定。能力凭据不等于“模型身份”；能读取凭据的同 uid 进程或管理员仍在保护边界外。0600 只有在实际文件系统与 ACL 确实强制执行时才有效，不能未经验证宣称 Windows/WSL 目录具备同样隔离。

## 三、方案 A：复用当前可信进程/owner 的最窄桥接

**结论：作为身份证明不成立，只能算传输硬化。**

- A1 日记改走一个 0600 的 Unix socket，靠 `SO_PEERCRED` 认调用者。问题是模型工具和电话
  服务通常同 uid；同 uid 的任意本地进程都能连，**不能单独证明主体模型身份**（任务也写明
  同一 OS uid 不能单独证明）。它能挡住浏览器/网络客户端，但挡不住同 uid 本地进程。
- A2 复用原生运行时的 `CHIYO_NATIVE_AUTH_TOKEN`。那是另一个服务的配置和 bearer 校验，
  电话服务没有接；把它接进 phone 就是新增 phone 认证合同，不是复用现成机制。
- 因此 A 不作为推荐方案。若只想先降低回环 HTTP 的暴露面，A1 可以单独另开硬化单，但不能
  宣称解决了 S14 F-152。

## 四、方案 B（推荐）：每实例本地能力凭据

**提案，未实施。**

- 电话服务每次 serve 生成一个不透明能力凭据（至少 32 字节随机），写入实例根下 0600 文件；
  模型工具从 profile 指向的文件读取，请求带 `Authorization: Bearer`（或等价的
  `X-Phone-Capability`）。服务端用常量时间比较校验。
- 绑定：subject、phone 实例指纹/endpoint、valid_from/expires、revision、撤销状态。
  轮换或撤销后旧凭据立即失效；同根重开是否轮换需拍板（建议每次 serve 轮换，旧凭据失效）。
- 行为：operator 页面继续不带凭据、默认只看元数据；只有声明 `model` 且凭据通过的调用
  才能取日记正文或写主体日记。凭据缺失时明确返回“未验证的 actor”，不是 500，也不把
  伪造声明落成正文。
- 不新增数据库、不改 Supply/Memory/AG1/Activity 合同；凭据生命周期由 phone 进程管理。
- 威胁范围：能挡住回环 HTTP 上的其他客户端（浏览器、其他进程的裸 HTTP 请求、伪造
  `X-Phone-Actor` 的请求）；**挡不住能读 0600 文件或同 uid 的本地进程**。所以它提升的是
  “任意声明”→“持有实例能力凭据”，仍不承诺“仅她可读”或管理员隔离。
- 代价：凭据签发/轮换/撤销、profile 传递、server 校验、模型工具兼容、测试与文档；
  旧配置没有凭据时私人正文读会 fail closed，需要在批准时明确接受或给迁移步骤。
- 拟议写集（仅提案）：`components/phone/server.py`、`components/phone/service.py`、
  `components/phone/demo.py`、`chiyo_bundle/phone_tool.py`、新增测试/演示脚本、
  `docs/DAY_START.zh-CN.md` 与 phone-diary note。不碰 canonical `db.py` / `contract.py`、
  AG1、Activity、Memory 治理/删除权威。

## 五、待本人批准的事项

1. 接受方案 B 的威胁边界：同 uid 本地进程仍可读 0600 凭据；只挡回环 HTTP 上的伪造者。
2. 凭据轮换策略：每次 serve 轮换，还是同一实例跨重启复用；撤销是删文件、改 revision，还是两者都有。
3. 旧配置没有凭据时，私人正文读 fail closed（正文不可读直到配置）是否可以接受。
4. 是否把凭据同时绑定 gateway DM 会话，还是只绑定 subject + phone 实例 endpoint。

在明确批准前，不新增身份系统、不改日记默认扣留、不动 Memory 准入/删除权威。

## 六、本轮未做 / 未验

- 没有新增认证代码、token、数据库或权限规则；没有改日记默认扣留和写入门。
- 没有演示“伪 `X-Phone-Actor` 能读正文”的绿色脚本，避免把缺口包装成修复。
- 没有对日记做撤权/在途竞态的真实演示；只核到 Supply 写入侧已有 grant 重验机制。
- 没有碰 Memory 删除 generation、恢复权威、旧实例；没有真实产品模型调用。

## 七、批准后的实施（2026-10-27）

- 新增 `components/phone/capability.py`：每次 serve 用 `secrets.token_urlsafe(32)` 生成，
  绑定本实例 `subject` 与**实际绑定**的 `http://127.0.0.1:<port>`，写入实例根
  `run/phone-capability.json`；POSIX 0600 + `O_EXCL|O_NOFOLLOW` 临时文件 + fsync +
  `os.replace`，目录不得被 group/other 写；正常停服删除，重启轮换。
- `components/phone/server.py`：日记正文 GET 在 actor=model 时必须持有凭据；缺失/错误/
  本 serve 不可用分别 403，不降级为空正文。日记 POST 在解析 body 前就要凭据，缺失零条目、
  零版本。operator 元数据扣留和其它手机面不变。
- `components/phone/demo.py`：真实绑定端口后才签发；同根 `flock` 只允许一个 serve 轮换；
  发布信息只给路径与状态，不给 token；安全存储不可用时 `capability_status` 具名不可用，
  私人日记 fail closed。
- `chiyo_bundle/phone_tool.py`：从 `phone.capability_file`（省略时
  `<phone.profile_home>/run/phone-capability.json`）每次重读；发送前校验 endpoint 与
  `/state` 主体；只有日记读写带 `Authorization: Bearer`，不跟随重定向，不沿用其它 bearer。
- 验收脚本 `scripts/private_diary_authority_demo.py` 用真实生产 serve、真实 Supply 与正式
  `phone_handler` 覆盖正常读写/幂等、operator 扣留、缺/错凭据拒绝且零副作用、
  subject/endpoint 不匹配、重启轮换与旧 token 拒绝、旧配置 fail closed、受控不安全存储
  不留 secret；最小直接测试 `tests/test_phone_capability.py`。
- 仍未覆盖：能读凭据文件的同 uid 进程/管理员、完整撤权竞态、S14 F-150/F-153/F-154、
  S13 全部条目、Memory/删除权威、gateway DM 绑定。不得把本实施写成“日记隐私完成”。
