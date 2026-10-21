# Agent Note: 手机共同实践——正式装配把 A 以 actor=user 绑上，用户输入与结束走本实例来信箱交给正常 Life

Status: implemented

## Problem

上一轮的门面已经按 A 的真名调用、`resume` 归她、缺少的对账照实说不支持，但整条链还有两个
真实缺口，而且都是「看起来能用、其实没接上」的那种：

1. **正式起机是 UNBOUND**。`demo.py` 的 `serve` 只写 `PhoneService(instance, feature_overrides=…)`，
   没人注入 A 的 owner，于是真实实例上 `shared practice` 一律 `SHARED_PRACTICE_UNBOUND`；
   测试里那份「注入替身」证明的只是门面，不是装配。
2. **手机抢了 Life 的活**。它把用户的话直接 `submit_user_contribution` 写进 A、把结束直接
   `end_practice` 改成 `ENDED`。可 A 的作品只是**作品**：正常 Life runner 靠它自己的
   `LocalContactInbox` 消费一条来话，才写用户贡献（user 角色）、才做一次内容请求、才写她的
   回应（life 角色 + 经核对来源）。手机直写的结果是：正常 runner 永远看不到那三轮输入和那次结束；
   真跑起来会重复记同一条用户贡献；而「作品已 ENDED、joint 还占着前台」正好是 B 那条链反过来的坑。

## Decision

1. **装配在 `PhoneService` 里**（`components/phone/service.py::_shared_practice_owner_from_instance`）：
   用 A 自己的 factory `shared_practice_service_from_env(port=…, env=…, actor=CALLER_USER)`，
   env 沿用 `private_project_service` 已发布的名字（workspace / grant / subject / socket / excluded），
   值全部来自**实例 payload**（`root`、`subject`、`workspace_id`、`artifact_grant_id`、`supply_socket`），
   不读进程全局 env，也不从请求里取。半接好的实例返回 `None` 并给出具名
   `SHARED_PRACTICE_BINDINGS_MISSING`；`demo.py` 一行都不用改。
   `project_port` 只作为「这位 owner 已经打开的端口对象」注入（父级/测试/POSIX Supply），
   它**不是**替身服务：A 的 `SharedPracticeService` 仍由 A 的 factory 真建、角色真是 `user`。
2. **用户那一半走传输**（`practice_shared` 的写半边整体换掉）：
   * 开一次 = `CO_PRESENCE_INVITATION`（`scope`=标题、`text`=第一轮、`material_refs`=[材料]），
     **手机不建 A 工件**：作品由正常 Life 在 ACCEPT 之后自己 `open_practice`（B 的既有实现）；
   * 用户说话 / 修改 = `LOCAL_MESSAGE`（只有 `text` + 原键）；
   * 结束 = `CO_PRESENCE_END` + `explicit_end=true` + 原键；先读协议 `co_presence.json` 的
     当前作品/邀请/事件绑定，再向 inbox 核对原邀请事件确实 ACCEPTED；不扫描不存在的结果字段。
     缺绑定、绑定另一作品、读不到或未接纳均具名拒绝，不写 END，不改作品；
   * `pause` 仍走 A 的 `pause_practice`（A 发布的 user 侧状态动作，只停这件作品的追加）。
3. **三条分离写清楚**：邀请（transport 事件状态）／作品状态（A 的 OPEN/PAUSED/ENDED）／
   joint 状态（CANDIDATE vs JOINT，只有经核对来源的她的回应才算 JOINT）；`accepted` 只照抄，
   手机永远给 `accepted=false`、`phone_wrote_practice=false`、`user_contribution_written=false`。
4. **原键跨 send/retry/query**：同一个 `operation_key` 打到 inbox 只会有一条事件；
   `GET /shared-practice/operations/<key>` 改为 B 的 `LocalContactInbox.operation` 真读数
   （`absent` / `applied` / `unknown`），手机不再回「不支持」，也不自造账本。
5. **可运行 fixture**（`components/phone/fixture.py`）：`start_fixture(project_port=…)` 建真实例根 +
   真 loopback HTTP 面，返回 `url` / `csrf_token` / `root` / `service` / `close()`；根与账本保留。
6. **共享来源核验与项目编辑分离**：共同实践 factory 只排除私人事项台账，不把阅读材料列入
   excluded；否则已明确共享的阅读 source 也会被当作读不到，正确的治理闸门会把全部回应扣空。
   实际源权限/换版/归档治理仍每次重读，非共同实践工件不能通过本服务写门。手机不投影来源全文。

## Alternatives considered

- **在 `demo.py` 里手工拼 A 的 owner 再注入**：改动最小。否决原因：装配点是 `PhoneService`，
  任何别的正式入口（父级脚本、后面的 Edge）都得再拼一遍；env 映射与「半接好就具名」的逻辑
  会散在多处，且 `demo.py` 没有 instance→A 的 env 知识。
- **手机继续直写用户贡献，只额外发一条来话「通知」Life**：实现最简单。否决原因：那会让同一条
  用户输入在 A 的贡献历史里出现两次（手机一次、Life 消费时一次），而且谁来写、写第几次就成了
  两边都不能自洽的问题；B 的 `step_message` 已经明确用 user 角色写用户那一半。
- **结束由手机同时写 `end_practice` + 发 END 事件**：看起来两边都收口。否决原因：`end_practice`
  是 B 在处理 END 事件时**同一个动作**里做的（LR-5 完成 joint、LR-4 开资格），手机先写一次只是
  多一个 `already_applied` 的版本；更糟的是如果事件没送出去，就会出现「作品 ENDED、joint 还在前台」。
- **把 `material_refs` 直接塞进手机 POST，由 server 透传给 contact 端点**：少一层转换。
  否决原因：选材的身份在 P 这一层只有一个入口（`source_artifact_id`，来自 owner 的 `/shares/sources`），
  让客户端能直接塞任意 ref 列表等于开了第二个入口；现在由门面从那个 id 派生。
- **把作品 id 补进已经终结的邀请结果**：查找最简单。否决：接受结果早于开作品，补写会改写
  已终结传输事实。现有 co-presence 协议已经持久绑定原邀请事件与作品，直接纯读再核验即可。

## Consequences

- **收益**：真实 `serve` 起机后共同实践面不再是 UNBOUND（`PhoneService(instance, project_port=…)`
  在 12 条新用例里真绑定、`owner.actor == "user"`）；用户输入与结束真的进了本实例自己的来信箱，
  正常 runner 有东西可消费；A 里不会因为手机多出用户贡献或第二个作品。
- **收益**：`operations/<key>` 从「不支持」变成真对账；UI 照实说「已进来信箱、还没被处理／排队
  不等于她答应」。
- **代价与已知上限**：邀请必须被正常 Life 处理才有作品（没有 runner 时列表就是空的，这是真话）；
  结束依赖协议绑定与原 ACCEPTED 邀请均可读（否则拒绝，不猜）；`pause` 只停作品追加，不动 Activity。
- **父级验证**：真 Supply/World/正常 runner + 正式 PhoneService 工厂整链 11 checks exit0；
  Edge 从主屏选材、三轮输入/真实 owner 回应、END、同 Activity 续读 page2、重启读回，12 checks exit0。
  模型是严格原始 JSON 替身，不是自然选择。手机回归 29 tests exit0；新增不替换查找方法的绑定测试
  与阅读材料被错误 excluded 的回归。真实模型结果独立见父级报告，不以脚本绿冒充自然行为。
  手机始终不跑模型、不 claim、不 record_result、不起第二个 Life writer。
