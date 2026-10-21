# Agent Note: rc1 房间生活链的手机接线——实体店柜台、交付摘要、布置与灯

Status: implemented

## Problem

这一轮的显著功能是「实体日用店 → 到店扣款/产权 → 真实交付 → 回家布置 → 坐/躺/桌面承载 →
开关自己的灯 → 重启承接」。核心 owner（经济、交付、placement、Resolver、环境/灯）由 C 单写，
手机面（P）此前只有虚拟店、钱包、物品、世界快照与五个世界动词，因此有三个具体缺口：

1. **实体店没有任何读/写入口**：C 的 `shop_projection()` / `purchase_item()` 长期只有测试调用，
   手机 HTTP 面上没有「到店看货架」「柜台买一件」的路由；施工单已经冻结了
   `GET /api/phone/store` 与 `POST /api/phone/store/purchases {catalog_item_id, operation_key}`。
2. **房间动作没有转发面**：`ARRANGE`（或 C 定名 `RELOCATE`）与 `TOGGLE_FIXTURE` 即使进了
   Resolver，P 的白名单（`WORLD_ACTIONS` / `WORLD_ACTION_FIELDS` / 工具 schema）也会把它们当
   未知动作拒掉，链在 409 处断。
3. **`/state` 看不出「买到的东西到哪了、屋里有哪些自己的家具、灯是不是开着」**：手机首页只有
   钱包/虚拟店/世界三块，没有任何房间摘要；而这一轮的体验恰恰是「刷新/重启后还看得见」。
   同时有一条硬约束：GET 必须纯读——不能靠「读一次顺手推进配送/物化」来让界面好看。

P 的职责边界也必须保持不变：不重算价格/余额/交付/身体值，不新增第二 store，不放宽
`phone.enabled`/`write_enabled`/`private_artifacts`/入口绑定，不动 rc1 的移动引导。

## Decision

七条，全部落在既有 owner 与既有入口上：

1. **`GET /api/phone/store` 是纯读**：`PhoneService.store()` → `PhoneShopping.store()`，把 C 的
   投影原样返回（`store_id`/`channel`/`availability`/`currency`/`catalog[]`）。它和 `/world`
   一样受 `Host`/`Origin`/`Sec-Fetch-Site` 闸门（它带着「店开没开」与预算），不写盘、不配送、
   不物化、不迁移。GET 不交付这条纪律没有例外。
2. **`POST /api/phone/store/purchases` 只收两把钥匙**：`catalog_item_id` + `operation_key`。
   CSRF/`Content-Type`/`Content-Length` 闸门与既有 POST 完全一致；`uid`/`owner`/`home`/`price`/
   `balance`/`main_self_verified` 等一律 `CLIENT_IDENTITY_REFUSED`（409），未知字段 400，
   缺 `operation_key` 400。成交与否、扣多少钱、要不要配送、2 小时还是即时——全部是 owner 的话，
   P 只把它原样映射成 HTTP 码（`applied` 200 / `already_applied` 200 / `rejected` 409 /
   `unavailable` 503），并往 `phone/operations.jsonl` 记一行 `action=store_purchase`。
3. **facade 缺方法时明确「未实现」**：`_facade_call` 先 `getattr` 再调用；C 还没落地的
   `store`/`store_purchase` 返回 `unavailable / PHONE_FACADE_UNIMPLEMENTED {method}`，
   不是空成功、也不是通用 adapter 错误的黑箱。
4. **`/state` 的新摘要只报 owner 说过的事**：新增 `apps.store`（`catalog_count` / `store_id` /
   `availability`，全部来自 C 的投影；虚拟店 `apps.shop` 一字未动）；`apps.world.data.room` 里
   `deliveries`/`owned_furniture`/`surfaces` 只给 `status`/`reason_code` 和**C 真的发布了列表时的
   长度**（`count`），`local_environment.light` 原样复制。缺块 = `NOT_REPORTED`，
   **未知的灯不是「关」，未知的配送不是「没在途」**。
5. **房间动词逐字对齐 C 的表**：动词是 `ARRANGE`（C 的 `PhoneWorld.VERBS` 里没有 `RELOCATE`，
   P 也不接受、不广告这个别名），参数是 `object_id` + `target`；`TOGGLE_FIXTURE` 是 `object_id` +
   绝对 `state ∈ {on,off}`（`toggle`、`ON`、布尔值都拒）。P 只做形状检查：`target` 只允许
   `room`/`surface`（container/hand/in_transit 属别的动词），多余键与黑名单（身份、`elapsed*`、
   **价格/余额/币种/交付状态**、身体值）一律 409。键集不是我猜的超集，而是 C 的 `PARAM_SPEC`；
   `FacadeConformanceTest` 直接 import C 的模块比对签名与两张表，任一侧改名立刻红。
6. **工具只加两个动作，入口与开关不变**：`action=store`（只读）与 `action=store_purchase`
   （写，需要 `write_enabled`）。`store_purchase` 与老的 `purchase` 同属
   `PRIVATE_ARTIFACT_ACTIONS`——它们是同一类「她自己的经济面」，受限记忆 + 无
   `phone.private_artifacts` 时一起拒。个人入口（`cli_owner`/DM 绑定）、`local`/被委派会话拒绝、
   `PHONE_PROFILE_MISMATCH` 指纹校验全部原样。工具 schema 里 `state` 变成
   `start|stop|on|off` 的并集，但逐动词钉死（REST 只 start/stop，灯只 on/off）。
7. **开关是 `serve` 的一个显式 flag，不是「设个环境变量就自然生效」**：`scripts/phone.py serve
   --with-room-living` 是本轮房间链（实体店/交付/布置/房间灯）唯一的开关。它把同一组「有效开关」
   （`demo.effective_features()` = 实例记录的 `features` + 本次覆盖）同时交给 World/Body 子进程的
   环境与 `PhoneService(feature_overrides=...)`，只碰
   `CHIYO_WORLD_DAILY_RHYTHM_ENABLED`、`CHIYO_WORLD_ROOM_ENVIRONMENT_ENABLED` 两个键；
   默认（不带 flag）行为与旧启动一致。**只在本进程内存里生效**：`instance.json` 的 `features`
   一个字节都不改（`serve` 仍只改写 `supply_socket`/`supply_generation`），不重写
   `prepare-demo` 产物。旧实例要体验就带 flag 重启同一个 root（命令见
   `docs/engineering/rc1-room-userguide.md`），数据与余额原地承接。
8. **功能回退不等于数据回滚**：关掉这轮功能 = 核验「这个 PID 属于这个实例」后正常停止，再用
   同一 root、不带 flag 的命令重启；已经发生的交易、产权、摆放、灯状态与执行账本全部保留，
   不覆盖任何数据档。备份只用于取证 / 受控恢复：本轮之后会有新交易、新摆放、新开灯，覆盖旧
   `world_property_economy.json` / `body_substrate.json` / `world_state.json` / `instance.json`
   会让余额、产权与历史一起倒退；真要恢复必须先核对当前 ledger（含未决动作与删除屏障）与之后
   的全部历史，且不自动执行还原。schema 兼容性不确定时只读报阻塞，不拿旧代码回写。
9. **房间块只从 owner 真正发布的位置读**：C 的快照把 `deliveries`/`owned_furniture`/`surfaces`
   挂在 `data.objects.*`，灯挂在 `data.local_environment.light`；`_room_summary()` 原先按顶层键读，
   于是 owner 明明发布了三块，`/state` 却一律报 `NOT_REPORTED`（`room["deliveries"] == {"status":
   None, "reason_code": "NOT_REPORTED"}`）。现在只读 owner 发布的位置；缺块仍是 `NOT_REPORTED`，
   不新增「顶层兼容读」这第二种形状。桩快照同步改成真实嵌套（原来桩在顶层造了平行的
   `data.deliveries` 等键，等于让用例与真实 owner 形状脱钩），并新增两条真形状用例：三块真的被读到
   （`count` 仍只取 owner 真发布的列表长度）、嵌套里 `unavailable` 的块保留 owner 自己的
   `reason_code`。
10. **两端必须读同一组开关，且覆盖只留在内存**：`demo._world_child()` 原先把
   `CHIYO_WORLD_DAILY_RHYTHM_ENABLED` 硬编码成 `"0"` 写进子进程环境，而手机面
   `PhoneService.environment` 是 `os.environ` 再被 `instance.features` 覆盖（旧 root 记着 `0`）；
   于是「外部 env 设 1」两端都不会变，实体店的营业判据永远关着。现在 `effective_features()` 是
   唯一算法，子进程环境与 `PhoneService(feature_overrides=...)` 都吃它；`serve` 不把覆盖写回
   `instance.json`。探测实测：`env=1` 不带 flag → 两端都 `0`；带 flag → 两端都 `1`（`env=0` 时
   flag 照样赢）。

## Alternatives considered

- **用 GET 顺手推进配送/物化**：界面上「刷新就到家」最省事，但这正是施工单点名否决的做法：
  GET 一旦写盘，读一次页面就改一次世界，重启比对也失去意义。否决：到货只由 C 的 owner tick /
  显式命令推进，P 的 GET 保持零写（用例把整棵夹具目录前后比对）。
- **P 自己算价格、算余额、判断「到没到」**：能让手机页在没有 C 时也显示得像真的。否决：
  这是第二个经济真相，和 v04 立下的「钱/产权/交付只有一个 owner」直接冲突。
- **等 C 的契约落定再动手**：最安全，但整轮会空转。折中：施工单冻结的（路由、HTTP 方法、POST
  body、工具动作名）先做，**只有 owner 能定的键集**先不猜——C 的 facade 一落地就把 P 的表逐字
  对齐，并加 `FacadeConformanceTest` 直接 import 比对，让「谁改了名」变成一条红的断言而不是线上 400。
- **把柜台购买塞进 `/api/phone/world/actions` 或另起一个总入口**：能少一条路由。否决：
  施工单要求扩既有 `PhoneShopping`，不新增第二个购物入口；而且经济写入不该借世界动词的账本。
- **`TOGGLE_FIXTURE` 只收 `object_id`、省略 `state`（真 toggle）**：客户端少写一个字段。
  否决：施工单要求灯是绝对状态、同 key 绑定目标 state，盲 toggle 会让重放变成「又切回去」。
- **给新动词放宽成「除黑名单外都放行」**：绝不会因为键名不一致而 400。否决：那等于 P 的白名单
  名存实亡；逐字对齐 + owner 复核才是「fail closed 且不猜」。
- **在 P 里给 `store` 复制一份目录/营业规则**：离线也能显示店开着。否决：营业时间、渠道、
  到店校验都是 C 的，复制即说谎。
- **顶层键与嵌套都读（两种形状兼容）**：改动最小，桩用例也能一起过。否决：owner 只发布一种形状，
  兼容读等于把「假形状」当合法契约，将来 owner 挪块时 P 会静默继续报旧位置的 `NOT_REPORTED`；
  正确做法是把桩改成真形状 + 加一条真形状用例。

- **把开关持久化进旧实例的 `features`（"开一次就永久开"）**：省得每次打 flag。否决：`instance.json`
  是旧 root 的既有事实，`serve` 只该改 socket 绑定两行；持久化会让「功能回退」变成一次数据写入，
  回退时又得再改一次，等于拿开关去动记录。内存覆盖 + 显式 flag 才能让「停用 = 同 root 无 flag 重启，
  原记录一字不动」。
- **只让子进程跟随外部 env，P 侧照旧读 features**：改动最小。否决：那只是把不一致换个方向，旧 root
  仍然打不开实体店；两端必须读同一组「有效开关」（`effective_features()`）才谈得上一致。

## Consequences

- **收益**：实体店有了纯读货架与一条只收两个字段的柜台购买；房间三块（到货/自有家具/承载面）与
  灯进 `/state`，且缺块不会被填成「没有」；`ARRANGE` 与 `TOGGLE_FIXTURE` 从手机、
  从工具都能真的走到 C 的 Resolver。手机服务在没有新版 facade 时诚实报
  `PHONE_FACADE_UNIMPLEMENTED`，不会假装成功。
- **代价/新增面**：`service.py`(+2 方法/+房间摘要/-1 通用分支)、`server.py`(+2 路由/+3 动词、
  黑名单加价格类键)、`phone_tool.py`(+2 动作/+2 动词/+`state` 并集)、`WORLD_ACTIONS` 从 5 个
  变 7 个（与 C 的 `VERBS` 一字不差）。旧用例里没有断言这 5 个动词的精确集合，故不受影响；
  `run 45 项全刷` 依然不是本轮的验收方式。
- **验证**：`tests/test_room_living_api.py` 23 项（21 → 23：新增两条真形状房间块用例；含 2 项对 C
  真实 facade 的一致性检查）、
  （真 loopback HTTP + 桩 facade + 真 profile 配置）；两者都不需要真实世界实例，也不写生产数据。
  真实世界/经济/灯语义由 C 的 `test_store_room_owner.py` / `test_room_environment.py` 覆盖，
  P 不冒充。
- **已知接缝**：P 的动词表与 `object_id`/`state` 拼写已经和 C 的 `PARAM_SPEC` 一致，并有
  `FacadeConformanceTest` 兜住；若下一轮 C 改动词名或参数，红的是这条一致性用例，改的是
  `server.ROOM_WORLD_ACTION_FIELDS` / `phone_tool._WORLD_FIELDS` 两处，不是判据。另有两条
  故意不做：灯/书的「使用」语义与容器摆放——没有 owner 就不给按钮（沿用「没有权威就不编」）。
- **一条过期的既有断言（明确授权后修正）**：`tests/test_phone_world_api.py:242` 把
  `actions.local_owner_verbs` 写死成五项，C 扩成七项（`+ARRANGE +TOGGLE_FIXTURE`）后必红。现在直接
  `from world_phone_world import VERBS` 做**精确**比对，并额外要求原来那五个动词仍在表里——不是宽泛
  `contains`：表里少一项照样红，表里多一项则跟随 C（那份表是 C 的契约，不是 P 的常量）。
  其余安全断言（九块、零写入、bed 的 `takeable=false`、浮点扫描）一字未动。
- **开关轮的验证与代价**：`tests/test_room_living_serve_flag.py` 新增 13 项（纯配置构造，约 0.4s，
  不起子进程、不绑 HTTP、不 prepare）：有效开关默认 == 记录值、带 flag 只多两个键（并断言没造出
  `PRICE`/`HOUR`/`TIMEZONE`/`DELIVERY_MODE` 之类的键）、child 环境与有效开关逐键一致、
  `PhoneService` 默认 features 压 env（旧行为）而给 `feature_overrides` 时覆盖胜出、两端取值相同、
  一次 `serve_instance(room_living=True)` 之后 `instance.json` 的 `features` 逐字节不变（只写
  socket 绑定两行）、`serve --help` 接受 `--with-room-living`。代价是 `demo.py` 多一个
  `effective_features()` 与 `ROOM_LIVING_FEATURES` 常量、`PhoneService` 多一个
  `feature_overrides` 关键字、`serve` 的公告多一个 `room_living` 字段（附加，不删旧字段）。
- **没有做的**（如实）：没有在运行中的 live 实例上带 flag 重启验证（未碰 5651/未重启任何服务），
  没有在真实实例上跑「到店→买→到货→布置→开灯」端到端；没有跑旧手机 45 项等长套件。
- **操作偏差（保留事实，不抹掉）**：施工中途我用过 `git add -N`（intent-to-add）标记 5 个文件，
  随后立即 `git reset` 撤销；当前工作树没有我的暂存内容，此后不再做任何 index 动作
  （不 add/stage/commit/push）。全程没有物理删除（socket/日志/缓存/目录一律保留）。
