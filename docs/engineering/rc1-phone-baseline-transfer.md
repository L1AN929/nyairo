# rc1 手机基线迁移（v0.3.0-rc1 + 已验收手机，世界/身体另派）

Status: implemented（迁移与验证已完成；世界/身体三路未开始，等父级在新树重新派工）

## 1. 基线实指

| 项 | 值 |
|---|---|
| 基线 tag | `v0.3.0-rc1` |
| 基线 commit（本地 tag 与远程 `L1AN929/nyairo` release tag 一致） | `41e818faaa24115fbf43db2977b15585c04144af` |
| rc1 相对旧树 HEAD | rc1 = `345df15` + `1208f63`（weixin world_move）+ `41e818f`（全平台 world_move） |
| `git diff rc1..345df15` | 只有 4 个文件：`MANIFEST.json`、`plugins/chiyo/__init__.py`、`scripts/fix_platform_world_move.py`、`scripts/fix_weixin_world_move.py`（3 insertions / 568 deletions） |

结论：旧树 `345df15` **不含** rc1 的 world_move 工具注册、引导 prompt 和两个修复脚本。因此本轮先把 rc1 该有的东西留下，再把上一轮已验收的手机叠上去，**没有**用旧插件全文覆盖 rc1。

## 2. 新工作树

| 项 | 值 |
|---|---|
| 路径 | `D:\nyairo-rc1-body-backend`（真实 `git worktree`，不是旧树改名或旧 status） |
| 分支 | `work/rc1-phone-world-body`（自 `41e818fa…` 新建） |
| HEAD | `41e818faaa24115fbf43db2977b15585c04144af`（`git rev-parse HEAD` 实测） |
| 旧树 | `D:\nyairo-v04-backend` HEAD `345df15` 原样未动、未 reset/clean/checkout、未删任何文件 |
| 索引 | 未 stage、未 commit、未 push、未打 tag；`MANIFEST.json` 版本元数据保持 rc1 原样 |

`git worktree add -b work/rc1-phone-world-body D:\nyairo-rc1-body-backend 41e818faaa24115fbf43db2977b15585c04144af` 前确认：目标路径不存在、分支不存在、源树三个旧 writer 已 stopped。

## 3. 迁移路径白名单（新树实际改动）

未跟踪（`??`，17 项）：

- `components/phone/__init__.py`、`__main__.py`、`demo.py`、`service.py`、`server.py`、`web/{index.html,style.css,app.js}`
- `scripts/phone.py`
- `chiyo_bundle/phone_tool.py`
- `components/world/scripts/world_phone_shopping.py`
- `tests/test_phone_api.py`、`tests/test_phone_tool.py`、`tests/test_phone_shopping_owner.py`、`tests/test_phone_ui.mjs`
- `docs/engineering/v04-phone-{api-contract,api-result,parent-acceptance,shopping-contract,shopping-result,ui-result}.md`
- `.agents/notes/implemented/feature/2026-10-17-v04-phone-api-local-owner.md`、`…-v04-phone-web-ui-consumer.md`、`.agents/notes/implemented/architecture/2026-10-20-v04-phone-shopping-owned-purchase-consume.md`

已跟踪修改（`M`，8 项）：`chiyo_bundle/hermes_plugin.py`（`chiyo_phone` 门禁白名单，8 行）、`components/world/scripts/{world_property_economy,world_consumable_contract,world_action_resolver}.py`、`components/world/service/{world_body_gateway_api,world_body_service}.py`（手机购物/loc­al-owner 接线的既有 delta）、`docs/USER_GUIDE.zh-CN.md`（第 18 节手机）、`plugins/chiyo/__init__.py`（+12 行手机注册）。

不存在的文件：`components/phone/__pycache__/`、任何 `.pyc`、`docs/engineering/v04-world-body-contract.md`、`components/world/scripts/body_consequence.py` 的改动。

## 4. rc1 必须保留项（已用 git 对象核验）

| 保留项 | 核验方式 | 结果 |
|---|---|---|
| `MANIFEST.json` | `git diff --stat -- MANIFEST.json` | 空输出（仍是 rc1 对象） |
| `scripts/fix_platform_world_move.py` | 同上 | 空输出 |
| `scripts/fix_weixin_world_move.py` | 同上 | 空输出 |
| `plugins/chiyo/__init__.py` 的 world_move 修复 | `git diff --numstat` | `12 0`：只有新增的 12 行手机注册；rc1 的 `_WORLD_MOVE_GUIDANCE`、`_world_move_guidance`、`register_system_prompt_section('chiyo.world-move', …)`、`register_tool(name='world_move', …)` 一个字未改 |

手机注册块插在 world_move 注册之后、`chiyo_status` 命令之前；`except` 里只把注册失败降级为 warning，不吞掉 world_move 的注册。

## 5. 手机来源（上一轮已验收成果）

`docs/engineering/v04-phone-parent-acceptance.md` 记录的真实浏览器链在同一实例里跑通：笔记新建/编辑、收藏、钱包 10000→9760、确认购买、物品物化、实际消耗一次（`world-consumption:5b9e805b…`）、SIGTERM 后同根重启并逐项相等。本迁移把**那些已验收文件**原样搬入新树（字节复制），只对三个半成品文件做剥离（见第 6 节）。

## 6. 未完成世界/身体片段（INCOMPLETE_WORLD_BODY_DELTA，已排除）

旧树在 2026-10-18 09:0x 被世界/身体 writer 动过；以下片段**不属于**上一轮手机 delta，因此没有进新树：

| 片段 | 旧树位置 | 判定依据 |
|---|---|---|
| `components/world/scripts/body_consequence.py`（+122 行） | 旧树 `M`，mtime 09:03 | 上一轮手机文件全部是 10-17 23:1x–23:5x；此文件 09:03 被改，属身体后果新工作 |
| `docs/engineering/v04-world-body-contract.md`（24,608 B） | 旧树未跟踪，mtime 09:02 | 世界/身体新契约，字段未实现；在本树**不存在**，不得当成 implemented |
| `components/phone/service.py` 的世界门面 | 旧树 536–583 行区（`PhoneService.world/_world_action/_world_operation/_world_instance/_world_call/_body_from_world/world_summary`）、`APPS` 里的 `"world"`、`WORLD_ACTIONS` | 只会经未实现的 `world_phone_world.PhoneWorld` 取数；`/state` 的 `apps.world` 与“升级版 body”同属世界/身体 |
| `components/phone/server.py` 的世界路由 | `/api/phone/world`、`/api/phone/world/actions`、`/api/phone/world/operations/<key>`、`_post_world_action`、`_world_params/_world_param_value`、`WORLD_ACTION_FIELDS`/`MAX_WORLD_PARAM_*`/`PARAM_KEY`/`WORLD_PARAM_TIME_OR_BODY`/`_WORLD_OPERATION_PATH`、`import math` | 同上；手机验收用例一条都不引用（`grep WORLD_ACTIONS|/world|\.world\(` 在三份手机测试里无命中） |
| `components/phone/demo.py` 的世界体验准备 | `_prepare_world_package`、`_package_capabilities`、`_write_capabilities(package_dir=…)` 的包能力声明、`DEFAULT_WORLD_PACKAGE_DIRNAME`、`interiors_locations`、`world_tick_seconds` + `--tick-seconds`、summary 里的 `world_package`/`interiors` | 世界场景/室内/owner tick 属世界身体；且这两处编辑**当时是坏的**（见第 7 节） |
| `components/phone/web/app.js` 的世界 app | `WORLD_APP`/`APP_KEYS`/`HOME_ORDER`、`APP_REGISTRY.world`/`SCREEN_META.world`/`RESOURCE_PATH.world`/`SCREEN_RESOURCE.world`，以及 817–1168 行整块（`WORLD_ACTION_TEXT`、`BAND_*`、`POSE_TEXT`、`bodyView` 等 25 个世界/身体视图函数，`pureApi` 未导出、`renderBody` 未使用，全部是死代码） | 未完成的世界界面半成品 |

**保留**的、易被误删的已验收手机项（说明为什么没跟着世界片段一起删）：`supply_socket_for`、`retire_socket`、`supply_generation`、`_supply_child(..., generation=)`、`serve_instance` 的 socket 换代与 `retired_sockets`、`_body_from_gateway`（`GET /state` 的 body app 仍走 gateway `get_snapshot`）。证据：已验收运行的实例 `/tmp/nyaw05r.jdK3q/v04-phone-p-accept1/instance.json` 里 `supply_generation: "serve-1792253740-11079"`、`supply_socket: …/run/supply-serve-1792253740-11079.sock`，`run/phone.serve.json` 里有 `retired_sockets: [gateway.sock.retired-…, control.sock.retired-…]`，且 `one-day/world-home/run/` 下确实留着三对 `.retired-*` socket。所以换代/改名逻辑是 23:46–00:15 那次已验收运行的组成部分。

## 7. 冲突与判定方法（怎么做、没做什么）

1. **没有整树复制、没有大 hash 比对、没有 shell patch/git apply/reset**。文件进新树的两种方式：字节复制（已确认完整）或在新树里用编辑器做行级改动。
2. **旧树 demo.py 当时是坏的**：`components/phone/demo.py` 有两个写坏的收尾——`_prepare_owners` 返回字典缺 `}` 且 `"capabilities"` 块里 `"path"` 重复出现两次，`_instance_payload` 留着一行旧签名（`supply_socket: Path, generation: str) -> dict[str, Any]:`）。`python3 -c "ast.parse(...)"` 在旧树上报 `SyntaxError: closing parenthesis ')' does not match opening parenthesis '{' on line 484`。新树按世界片段剥离时一并修好，现在是 562 行、`compile()` 通过。
3. **用 `__pycache__/*.pyc` 做“函数清单”，没有当成验收快照。** 旧树 `components/phone/__pycache__` 里 `service/server/demo` 的 pyc 分别定格在 23:25/23:20/23:30（源码长度 27069/19749/19518 字节）。它们**早于** 23:46 开始的换代重构，所以只能用来回答“某个函数当时长什么样（body 走 gateway get_snapshot、APPS 七个、`_instance_payload(root, prepared)` 等）”，不能当成最终验收态。据此判断出 `create_note` 的 `NOTE_TITLE_CHANGE_NOT_SUPPORTED`、`_body_from_gateway` 等是 23:4x 那次手机返修的结果，必须保留；而 `_body_from_world`、`world_*` 是今天的。
4. **app.js 没有可靠字节快照，所以用结构化剥离 + 既有测试证明。** 唯一可得的 app.js 快照是 scratch 里浏览器缓存的 23:13–23:16 三份（88,776 / 89,239 / 89,271 字符，均早于 23:4x 的协议 ID 返修），所以**没有**拿它们覆盖文件。剥离依据是：世界常量只在 5 处出现、`APP_KEYS`/`HOME_ORDER` 各只被 `stateView`/`renderHome` 引用一次、817–1168 的 25 个函数在 `pureApi` 和 `renderBody` 里都没有引用（死代码）。剥离后 `world` 字样 0 处、`node --check` 通过、`tests/test_phone_ui.mjs` 34/34 通过。
5. **手机 delta 的 world 侧接线原样保留**（`world_phone_shopping.py`、`world_property_economy.py`、`world_consumable_contract.py`、`world_action_resolver.py`、`world_body_gateway_api.py`、`world_body_service.py`、`hermes_plugin.py`）：这些文件 mtime 全是 10-17 23:1x，内容都是“本地 owner 手机 + 真实消耗回执/购买”，属上一轮手机成果。`body_consequence.py` 因为 09:03 被改且不属于手机，**未**进新树。
6. 旧树全程只读（`git status`/`git diff`/读文件/读 `/tmp` 实例），没有启动或停止任何 live phone 实例，没有真实模型/部署/付款。

## 8. 验证（真实命令、真实退出码，均为新树 cwd）

| 命令 | 结果 |
|---|---|
| `git rev-parse HEAD` | `41e818faaa24115fbf43db2977b15585c04144af`（= rc1） |
| `git diff --stat -- MANIFEST.json scripts/fix_platform_world_move.py scripts/fix_weixin_world_move.py` | 空输出，exit 0（仍是 rc1 对象） |
| `git diff --numstat -- plugins/chiyo/__init__.py` | `12 0`（只加手机注册，world_move 未动） |
| `git diff --check` | exit 0（无空白错误） |
| `node --check components/phone/web/app.js` | exit 0 |
| `node --test tests/test_phone_ui.mjs` | `# tests 34 / # pass 34 / # fail 0`，exit 0 |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_phone_tool -v` | `Ran 11 tests … OK`，exit 0（真实 Supply/World 子进程 + 真实购买/消耗） |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_phone_api` | `Ran 13 tests … OK`，exit 0（真实 HTTP + CSRF/Host/Origin、备注/购买/使用、`/state` 七个 app） |
| `PYTHONDONTWRITEBYTECODE=1 python3 -m unittest tests.test_phone_shopping_owner` | `Ran 21 tests … OK`，exit 0 |
| 定向脚本 `phone_check.py`（AST + 导入 + 剥离后签名 + `_write_capabilities`/`supply_socket_for`/`retire_socket` 真实文件行为） | `FAILURES: []`，exit 0 |
| 笔记数量 | 新树 `.agents/notes` 32 篇（与验收时“32 条笔记 tree/format 均 0”一致） |

未跑/跳过：

- 笔记 tree/format 门禁脚本：**本仓库没有**（`scripts/`、`dev/`、`.agents/` 下均无该脚本，旧树也只是在会话里调用外部工具）。跳过原因写明；三篇笔记是从旧树字节复制的、在验收时已过门禁。
- 没有跑旧全库套件（`scripts/test.py` / `test_alpha.py`），也没有跑世界/身体测试；按要求只跑手机相关。
- 没有真实浏览器复验（父级会用同一实例串链）；没有真实模型调用。

## 9. 结论与后续

- 新树 `D:\nyairo-rc1-body-backend`（branch `work/rc1-phone-world-body`，HEAD `41e818fa…`）就是 **rc1 + 上一轮已验收手机**：三份手机测试 + UI 测试全绿，rc1 的 world_move 工具/引导与两个发布脚本一字未动，`MANIFEST.json` 仍是 rc1 原样，未 stage/commit/push/发布。
- 世界/身体三路（C 核心、P 接线、U 界面）在本树**尚未开始**：`body_consequence.py`、`docs/engineering/v04-world-body-contract.md`、`PhoneWorld` 门面、`/api/phone/world*`、`app.js` 的世界 app 都不在这里。父级若要重新派工，写集边界与施工单 C/P/U 一致即可，不会和已验收手机冲突。
- 已知残余风险（如实记录）：`app.js` 与三份 Python 手机模块在“世界片段剥离”后没有与 23:5x 验收态做过字节比对（无可信快照），正确性由既有测试与行为断言支撑，而不是字节一致性。
