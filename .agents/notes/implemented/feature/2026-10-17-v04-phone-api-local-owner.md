# Agent Note: v0.4 手机 API —— 真实笔记/收藏/阅读与虚拟购物闭环，只对本地 owner 开放

Status: implemented

## Problem

用户要的是"一部能真正操作、刷新与重启后内容还在"的手机，而不是一个管理员面板。这一批的
难点不在画界面，而在**三个真相源如何拼成一部手机而互不夺权**：

* 笔记/收藏/阅读的真相已经在 Life Supply 的 `ManagedArtifact`（`private_daily_items` +
  `life_artifact_port`），再写一份手机本地 JSON 就是第二份真相；
* 钱、产权、物件位置、消耗回执分属 `world_property_economy` 与
  `world_body_substrate`，经 `world_action_resolver` 提交；手机侧若"直接改 state"，
  幂等、开销户与消耗一次性全部失效；
* 浏览器页面在 loopback 上被任意本机进程/网页访问，若无 Host/Origin/CSRF 与身份来源的
  闸门，一次 DNS rebinding 或一个跨站表单就足以让"本地 owner"变成"任何人"；
* 个体自己（模型）也要能用这部手机，但不能因此变成第二个权限入口，更不能借手机读回被
  遗忘的记忆。

因此需要一份**明确的 HTTP 面契约**（字段、状态码、幂等与对账语义）和一个只做编排、不做
拥有者的服务层，并且 demo 必须能一次创建**真实**世界与真实 Supply，而不是靠 stub 通过。

## Decision

新增 P 写集：`components/phone/{service,server,demo,__init__,__main__}.py`、
`scripts/phone.py`、`chiyo_bundle/phone_tool.py`，并把 `chiyo_phone` 按既有 `world_move`
的窄模式注册进插件；`hermes_plugin.memory_tool_gate` 只放行这个**结构上不能读记忆**的工具。

* **不新增任何 store。** 笔记/收藏/阅读只经 `LifeArtifactPort` 读写一个显式绑定的
  artifact（`private_items_artifact_id`），`expected_head` 做 CAS；钱包/商店/物品/购买/使用/
  对账全部转发 W 的 `PhoneShopping`，手机层不改字段、不重算价格、不猜余额。
* **一部手机 = 一个实例根。** `prepare-demo` 只在新的绝对路径上创建一次：真实 Supply
  workspace/artifact/私人事项文档、真实 worldgen 世界包与 world home、真实
  `initialize_genesis`（10000 JPY `move_in_grant`，不重置、不每日发钱）、真实
  `consumable_catalog` 声明；已存在即拒绝。
* **`serve` 自己起、自己停自己的两个子进程**（Supply owner 与 `WorldBodyService`，后者带
  显式 `CHIYO_WORLD_LOCAL_OWNER_PHONE=chiyo`），只绑 `127.0.0.1`，端口在第一行 JSON；
  退出只 SIGTERM 自己起的 pid，数据/socket/日志都留着。
* **socket 只增不删**：每次 `serve`/`prepare` 用新 generation 的新路径
  （`run/supply-serve-<gen>.sock`），同一数据根重启只更新实例里的有效 binding；World 的
  `gateway.sock`/`control.sock` 若已存在，P 的 wrapper 在启动子进程前 **rename** 成
  `<name>.retired-<gen>` 保留，绝不调用会 `unlink` 的 helper；非 socket 占用一律拒绝启动。
* **HTTP 闸门 fail closed**：Host 只认 `127.0.0.1`/`localhost`；`Origin` 必须同源；
  `Sec-Fetch-Site` 只能 `same-origin`/`none`；所有 POST 要 `X-CSRF-Token`（常量时间比较）；
  请求体字段白名单；body 里的 `uid`/`authority`/`main_self_verified`/`home`/`price`/
  `balance` 直接 409 拒绝；静态文件只有 `index.html`/`app.js`/`style.css` 三个名字。
* **笔记不做标题回退**：`POST /notes` 不传 `note_id` 时永远新建，`memo_id` 由 `operation_key`
  派生（同 key 重放、不同 key 独立）。修订只认 `note_id` 且只改正文；改标题明确拒绝
  （`NOTE_TITLE_CHANGE_NOT_SUPPORTED`），未知 id `NOTE_NOT_FOUND`。原 CAS 不变。
* **状态码与信封一一对应**：`applied|already_applied`→200、`rejected|disabled`→409、
  `unavailable`→503、`unknown`→202、`not_found`→404；`unknown` 一律先
  `GET /operations/<key>` 对账，不换 key 重发。
* **来源分开记录**：手机自己的 `operations.jsonl` 记 `actor`（浏览器缺省 `user`，工具声明
  `model`）+ `operation_key` + 结果；经济/Resolver 侧 provenance 仍是 `local_owner_phone*`，
  不伪造 `verified_main_self`，也不伪造自主 Decision。
* **工具先过原入口授权**：与 `hermes_plugin.allowed` 同一规则（CLI 的 `cli_owner` +
  真实 uid；网关只认已有 DM 绑定；`local` 与委派会话一律拒）；再要求
  `phone.enabled`/`write_enabled`，`base_url` 只能来自 profile 且必须 loopback，
  `profile_home` 用指纹比对。
* **私人档案是第二把钥匙**：`memory` 打开且策略非 `unrestricted` 时，工具只剩
  wallet/shop/inventory/operation；notes/references/reading 及其写操作需要 profile 显式
  `phone.private_artifacts: true`。开启手机不绕过遗忘保护。

## Alternatives considered

1. **把笔记直接写进手机自己的 JSON 文件**（最快，与 UI 字段一一对应）。放弃原因：那会成为
   第二份私人真相，Supply 的版本、grant、CAS 与撤权语义全部被绕过；重启后两份数据一旦分叉，
   没有任何 owner 能判定谁对。
2. **手机服务直接调用 `world_property_economy` / Resolver**（少一层，看起来"更真实"）。
   放弃原因：手机就成了第二个购物 authority，W 的幂等键、物化重试、一次性授权与消耗回执
   都会被复制一份；契约里"P 只按 `PhoneShopping` 接线"这一条也就名存实亡。
3. **前端不校验、全靠服务端**（实现少一半）。放弃原因：UI 写集是 U 的，且浏览器侧仍需要
   在拿不到 `csrf_token` 时**只读**而不是硬发；服务端闸门是底线，不是替代品。
4. **让工具直连 Supply socket 与经济文档**（省掉 HTTP）。放弃原因：那等于给模型开第二个
   权限入口，绕过了 CSRF/Host/字段白名单与 `operations.jsonl` 的来源记录；工具走同一条
   HTTP 面，来源才能被记录成"模型发起"。
5. **`serve` 用线程内服务代替子进程**（启动更快）。放弃原因：`WorldBodyService` 与 Supply
   owner 都是单写者/带 fence 的进程；把它们的生命周期绑进 HTTP 进程会让"退出只停自己的
   pid"和"子进程失败日志可查"都无法诚实做到。
6. **新建笔记时"同标题唯一就自动修订"**（第一版实现，UI 少按一次）。放弃原因：用户新建一条
   同名备忘时会**静默覆盖**旧笔记；标题不是身份。现在新建永远新建（`memo_id` 来自
   `operation_key`），修订必须给 `note_id`。
7. **让 `revise_memo` 顺手改标题**（看起来更完整）。放弃原因：owner 的修订契约只提交正文，
   手机层若把新标题当成功返回就是"API 说改了、实际没改"；改名明确拒绝更诚实。
8. **沿用 owner 的 `prepare_socket_path()` 清理旧 socket**（第一版实现，能让重启绑定成功）。
   放弃原因：它会 `unlink` 证明为 stale 的 socket，而本轮禁止删除；改成新 generation 新路径 +
   窄 rename 保留，既不删也不偷。
9. **只凭 `phone.enabled` + 实例指纹就放行工具**（第一版实现）。放弃原因：那会让**未绑定**的
   gateway 会话或同机器上的其它 profile 也能访问；必须复用 `hermes_plugin.allowed` 的入口
   规则，并把委派会话挡在外面。
10. **"工具不读 memory DB 所以不可能读到被遗忘历史"**（第一版注释里的断言）。放弃原因：
    私人 notes 本身就能保存她的过往；现在受限记忆下默认只放行经济视图，私人档案需要
    `phone.private_artifacts` 这把第二钥匙。

## Consequences

* 真实闭环已经跑通：`POST /purchases` 从 10000 扣到 9760 并把物品物化到 `home/bedroom`；
  `POST /items/use` 经原 Resolver 落 `world-consumption:*` 回执；同 key 重放
  `already_applied` 不二次扣款/不二次消耗；同根重启后余额 9760、物品 `CONSUMED`、笔记仍在。
* 更正：第一版结果文档写"socket 都留着"对数据成立，但对 socket 文件**不准确**——
  当时 `owner-supply` 调了 owner 的 `prepare_socket_path()`（会 unlink 证明 stale 的 socket），
  World 的 bind 也会 unlink 自己的旧 gateway/control。本轮已改成新 generation + 窄 rename
  保留，并在结果文档 §5.1 明确作废旧说法。
* 代价与边界：手机只有 `127.0.0.1` 一种绑定；没有删除/充值/真实支付/外发；demo 以当前
  uid 运行 Supply owner 时显式 `LIFE_SUPPLY_REQUIRE_NON_ROOT=OFF`（多用户部署必须保持 ON）；
  受限记忆下工具默认看不到私人笔记（要 `phone.private_artifacts`）。
* 交接观察（W 写集，未代改）：`inventory` 对已消耗物品会报 `materialization:"pending"`；
  W 已另行处理"consumed 物品显示"，P 不碰。
* 验证方式（定向，不做整仓回归）：返修后 `tests.test_phone_api.PhoneAPINotesTest`（5）与
  `tests.test_phone_tool`（11）均 `RC=0`；另在全新根上重跑一次真实 prepare/serve/同根重启
  与完整买卖链（端口 53335→50763→49245），证据与命令见
  `docs/engineering/v04-phone-api-result.md` §5。
