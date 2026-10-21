# Agent Note: rc1 手机阅读工作台——真实正文/停点/页注只经 C 的 PhoneReading

Status: implemented

## Problem

这一轮要把「操作者显式给的文本 → 真实可读 → 显式保存停点 → 对真实页写私人笔记或疑问 →
重启后仍从上次停点继续」接到手机面上。本轮之前，手机只有阅读**元数据**：

* `GET /api/phone/reading` 只列 acquisition/position 的条目，**不含正文**（`service.py:reading()`）；
* 手机 HTTP 面**没有**任何阅读写路由，模型工具也没有读书动作；
* 实例文件里其实早就有 `reading_artifact_id`（`demo.py:_instance_payload`），但手机服务
  **根本不读它**，正文的真实 owner（Supply `ManagedArtifact` + `PrivateDailyItems`）在手机侧
  等于断线。

同时有几个必须当场定死、否则日后会被重走一遍的边界：

1. 正文不能落到手机自己的存储里——那会成为第二份私人真相，Supply 的版本、grant、CAS 与撤权
   全部被绕过（[v04 手机 API 决策](2026-10-17-v04-phone-api-local-owner.md) 已经立过这条）。
2. 「最近保存的停点」不等于「最高页」。用最高页或默认第 1 页冒充恢复，会让界面说一句用户
   从未做过的承诺。
3. 阅读面是全机最私密的一面（在读什么、停在哪、旁边写了什么），它必须沿用旧私人面那两把钥匙
   （`phone.enabled` + `phone.write_enabled`，受限记忆下还要 `phone.private_artifacts`），
   不能因为「新功能」就开第三条入口。
4. 客户端不能声明 artifact、路径、URL、authority、title 或「理解」；`understanding` 永远是
   `UNKNOWN`，停点不是理解证明。

## Decision

手机侧（P）只做**接线与拒绝**，所有阅读事实归 C 的 `PhoneReading`
（`components/life/scripts/phone_reading.py`）。落地的十件事：

1. **绑定来自实例，不做初始化**：`PhoneService.__init__` 读 `instance["reading_artifact_id"]`
   （`self.reading_artifact_id`），只把它传给 facade；P 不创建材料、不写 `instance.json`、
   不新增 CLI 导入旁路（真实导入走页面 `POST`）。根上没有绑定时一切阅读路由诚实
   `unavailable / READING_MATERIAL_UNAVAILABLE`（503，`available:false`），绝不补演示正文。
2. **facade 的第一个位置参数是一个已构造的 `LifeArtifactPort`**：C 的签名是
   `PhoneReading(port, *, reading_artifact_id, private_items_artifact_id, workspace_id, grant_id)`，
   构造 port 需要 `subject`，而签名里没有 `subject`——所以 `port` 只能是 P 已经持有的 port 对象
   （`PhoneService._port()`），由 C 的 `isinstance(port, LifeArtifactPort)` 守卫。P 用
   `reading=<facade>` 关键字注入测试桩，但绑定检查在注入之前：未绑定的根永远不会碰到 facade。
3. **`GET /api/phone/reading/material` 是纯读**：`page` 可选；不给就把 `None` 原样交给 owner，
   由 owner 回答「当前版本最近一次显式保存的停点」，否则 1——P 不预填 1、不算页数、不写盘、
   不记 operation（`phone/operations.jsonl` 在 GET 后仍是零行）。返回 `data` 里的
   `available/artifact_id/source_id/source_revision/version_number/title/page/pages/text/
   content_available/private_head/bookmark/annotations/understanding` 全部原样转发。
4. **Query 白名单只有 `page`，且页码严格**：未知键 `UNKNOWN_QUERY:<k>`、重复 `QUERY_REPEATED:page`、
   空值/符号/前导零/小数/布尔词 `PAGE_INVALID`、>`MAX_READING_PAGE`(64) `PAGE_OUT_OF_RANGE`；
   一律 400，且在 owner 之前拒绝。`parse_qsl(keep_blank_values=True)` + `^[1-9][0-9]{0,2}$`。
5. **三个 POST 路由的字段就是契约**：
   * `POST .../reading/material` `{content, expected_revision, operation_key}`——正文 1..24000
     UTF-8 bytes、非空白、无 NUL，`%%PAGE%%` 原样透传（不重新分页、不加标题/回执）；
   * `POST .../reading/positions` `{page, source_revision, expected_head, operation_key}`——
     `page` 严格整数非 bool（`true`/`2.0`/`"2"`/`0` 全部 `PAGE_INVALID`），`expected_head`
     必填（它就是下一次 CAS 的基准）；
   * `POST .../reading/annotations` `{kind, page, source_revision, expected_head, operation_key}`
     \+ 逐 kind 字段：`kind="note"` 必须 `title`+`body` 且**不接受** `note`；`kind="unclear"`
     必须 `note` 且**不接受** `title`/`body`；`kind` 只认 `note|unclear`
     （`ANNOTATION_KIND_INVALID`）。两个 kind 绝不互相顶替。
   身份类字段（`artifact_id`/`uid`/`owner`/`source`/`workspace_id` …）继续 409
   `CLIENT_IDENTITY_REFUSED`；`title`/`understanding`/`url` 进 material 是 400
   `UNKNOWN_FIELD:<k>`；64 KiB 请求体、CSRF、Host/Origin/Sec-Fetch 闸门一字未退。
   页注文本的上限跟 C 走**字节**：`title ≤200`、`body`/`note` ≤8000 UTF-8 bytes（C 的
   `PhoneReading._text` 就是按字节判的；沿用私人面的 80/4000 会拒掉 C 允许的合法批注）。
6. **owner 的 `http_status` 提示有界采纳**：C 的 `envelope()` 会带一个 `http_status`
   （`_bad` 400 / `_conflict` 409 / `_unavailable` 503 / `_unknown` 202 / `_not_found` 404）。
   P 只在冻结集合 `{200,202,400,404,409,413,503}` 内、且不翻转语义时采纳它
   （成功只能 200/202，拒绝只能 400/404/409/413/503），其余回落到状态映射。这样
   「输入根本不是一页/一段正文」是 400，而任何 owner 都无法把拒绝说成 200。
7. **`PhoneReadingError` 直接冒泡成 500**：C 说「facade 被误用按 500 处理」，P 就让它走到
   `PHONE_INTERNAL_ERROR`，不伪装成 503 owner 不可用。其它异常 → `PHONE_READING_ADAPTER_ERROR`
   （503），facade 缺方法 → `PHONE_FACADE_UNIMPLEMENTED {method}`（503），绝不给空页。
8. **旧 `GET /api/phone/reading` 只多一个入口摘要**：追加 `material_available`
   （`bool(reading_artifact_id)`）与 `source_id`（`artifact:<id>`，与 C 的
   `reading_source_id` 同规则），`items` 与 `understanding` 原封不动；**正文永远不进这个列表、
   也不进 `/state`**。
9. **对账按原 key**：`GET /api/phone/reading/operations/<key>` 用路由正则限定键字符集，
   原样交给 `PhoneReading.operation(key)`；未知键返回 owner 的 `not_found`（404），未决返回
   `unknown`（202）。P 没有「换一个新 key 再试」的代码路径。
10. **工具加五个动作，钥匙不变**：`reading_material`（读，可带 `page`）、`reading_import`、
    `reading_position`、`reading_annotate`（写）、`reading_operation`（原 key 对账）。五者全部进
    `PRIVATE_ARTIFACT_ACTIONS`，因此受限记忆 + 无 `phone.private_artifacts` 时一起拒
    （`PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED`）；写动作另需 `phone.write_enabled`；入口规则
    （个人 CLI/DM 绑定、拒 `local`、拒被委派会话）原样。工具在发出请求前按同一套形状先拒
    （`page` 严格、`content` 1..24000 bytes、kind 逐字段、`artifact_id/path/url/title/
    understanding` 一律 `UNKNOWN_FIELD`），并给出诚实文案：导入 ≠ 读过、位置 ≠ 理解、
    页注 ≠ 读懂；`PAGE_NOT_ACQUIRED` 时明确提示先保存停点。

## Alternatives considered

- **P 自己 `port.read_artifact` + 分页 + 拼 acquisition/position（复制 `one_day_life.read_page`）**：
  少一个跨模块依赖，看起来最直接。否决原因：那会成为第二份阅读编排，分页标记、取得证据、
  receipt 格式、UNKNOWN 对账一有改动就和 C 分叉；而施工单已经冻结「正文/停点/页注只经
  `PhoneReading`」。
- **新建手机侧阅读 JSON（正文 + 停点 + 笔记一份存）**：UI 字段一一对应，最省事。否决原因：
  第二份私人真相，Supply 的版本/ grant / CAS / 撤权全部绕过；重启后两份数据分叉时没有任何
  owner 能判定谁对。
- **`GET material` 不带 `page` 时默认第 1 页**：实现最省，客户端不用理解「最近停点」。否决原因：
  这正是施工单点名禁止的伪装——用户回到第 3 页、刷新后看到第 1 页，等于手机替她改了停点；
  默认 `None` 让 owner 用自己的规则回答，P 不猜。
- **`GET` 顺手落一条 acquisition/position（翻页即记录）**：界面「翻过就算读过」最自然。否决
  原因：施工单写明「仅翻页不保存」，GET 一旦写盘，读一次页面就改一次私人文档，重启比对也失去
  意义；位置只由显式 `POST positions` 落。
- **宽容解析 `page`（接受 `"2"`、`true`、`01`、`2.0`）**：客户端少一次类型转换。否决原因：
  `true` 是第 1 页、`"2"` 是第 2 页这种宽容，会让「用户到底停在哪一页」变成服务端的猜测；
  严格整数 + 400 才能在越界时说话。
- **把正文塞进 `/state` 或旧 `GET /reading`（UI 少一次请求）**：前端最省。否决原因：那等于把
  最私密的正文摊进每次首页刷新与每个 app 摘要；施工单明确「不能在 state/旧元数据列表偷偷塞
  正文」。
- **把 `note` 与 `unclear` 合成一个 `body` 字段**：少一个 kind。否决原因：`unclear` 是
  「我没读懂」，把它写成普通笔记是撒谎；两种 type 在 owner 里本来就是两条记录。
- **无条件转发 C 的 `http_status`**：一条 `return int(result["http_status"])` 就够。否决原因：
  那等于把 HTTP 语义交给 owner 的任意字段；有界采纳（成功只 200/202、拒绝只错误码）既拿到
  C 需要的 400，又保住「拒绝永不被 200 化」。
- **工具在 `unknown` 时自动换 key 重试**：模型体验最好。否决原因：换键重发会让 owner 的
  私人文档多写一条、甚至落第二版；原键查询（`reading_operation`）才是唯一正确动作。
- **等 C 的 facade 落地再动手**：最安全。折中做法是先把施工单冻结的路由/字段/动作名与
  「未实现即 `PHONE_FACADE_UNIMPLEMENTED`」写死，再用**桩**钉住 P 自己那层，最后用真实
  facade 做签名一致性窄测与「它的话原样回来」窄测。

## Consequences

- **收益**：手机第一次能读到**真实正文**（一页）、把停点显式落到 owner、对「已取得的那一版
  那一页」写 `note`/`unclear`，并能按原 key 对账；`understanding` 恒 `UNKNOWN`，
  GET 零写、零记录、零创建；没有绑定材料的根老实 `READING_MATERIAL_UNAVAILABLE`，facade 缺方法
  老实 `PHONE_FACADE_UNIMPLEMENTED`。
- **代价/新增面**：`service.py` 多 5 个公开方法（`reading_source_id/material/import/position/
  annotate/operation` + `_reading_instance/_reading_call`）、`server.py` 多 4 条路由 + 4 组字段表 +
  `_reading_page/_clean_reading_content` 与 `http_status_for(status, result)` 的可选第二参、
  `phone_tool.py` 多 5 个动作 + 4 个 pre-flight helper + 文案表。`scripts/phone.py` 与
  `demo.py` **一个字节都没改**（实例里早就有 `reading_artifact_id`，不新增 CLI 导入旁路）。
- **接缝**：P 的字段表与 C 的签名由 `RealFacadeConformanceTest` 的签名比对钉住；若 C 改参数名，
  红的是这条用例，改的是 P 三处（service 转发、server 字段表、tool `_FIELD_BY_ACTION`）。
  两处故意不做：不把正文缓存进浏览器/工具，不提供「自动模型读书」。
- **验证**：`tests/test_phone_reading_api.py` 41 项（含 2 项需要 POSIX `LifeArtifactPort`
  的真实 C 集成用例；Windows 上跳过并写明原因，父级 WSL 上 3 项全跑）；`tests/test_phone_reading_tool.py`
  19 项；两者合计 60 项 `RC=0`。旧手机套件（`test_phone_api` / `test_phone_tool`）本机 24 项全部
  skip（POSIX-only）；`test_room_living_api` / `test_room_living_tool` 仍通过。
- **父级报回的测试侧缺陷（已修，纯测试）**：`tests/test_phone_reading_api.py` 的夹具命名原来用
  `pid + int(time.time()*1000)%1000000`，在 POSIX 上同一毫秒连续建两个同前缀根会撞名
  （父级日志：`AssertionError: fixture root already exists:
  /tmp/nyairo-reading-pi/rc1-reading-real-18660-614624`）。改成 `pid + time.time_ns() + 每进程
  自增序号`，`if root.exists(): raise` 保留（绝不覆盖、绝不删除已有夹具），并在签名一致性用例里
  加了「同进程连续两次同前缀必然不同名、两个目录都存在」的断言；产品代码零改动。
- **未做/已知**：没有在真实运行实例上跑「重启后仍停在第 N 页」的端到端（父级验收负责）；
  没跑全库回归；`test_room_living_serve_flag.py` 在 Windows 上因 `os.geteuid()` 报错（既有平台
  问题，不在本写集）；任何 POST 到未知 API 路由仍会「读到一半 404」，Windows 上客户端会看到
  `ConnectionAbortedError`（既有行为，未在本轮改动）。
