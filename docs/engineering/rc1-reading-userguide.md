# rc1 阅读工作台：怎么用（P 侧）

这一页讲**手机上怎么真的读到一段文本、把停点存下来、在旁边写一条私人笔记或疑问**，以及出问题时
怎么读错误码。契约细节见 [rc1-reading-api-result.md](rc1-reading-api-result.md)；正文/停点/页注
的真实语义都在 C 的 `PhoneReading`，这里只说手机面怎么用。

## 0. 前提

1. 手机实例（`instance.json`）里已经有 `reading_artifact_id`——`prepare-demo` 时就会写；
   没有就说明这个根从来没绑过阅读材料，所有阅读路由会诚实回 `READING_MATERIAL_UNAVAILABLE`。
2. 手机服务在跑：`python scripts/phone.py serve --instance <root> --port 0`（本机 loopback）。
   阅读不依赖 `--with-room-living`，那个 flag 只管实体店/交付/布置/灯。
3. 浏览器打开服务地址后，先 `GET /api/phone/state` 拿 `csrf_token`；所有 POST 都要带
   `X-CSRF-Token`。

## 1. 读一页真实正文（只读）

```http
GET /api/phone/reading/material            → 回到「最近一次显式保存的停点」
GET /api/phone/reading/material?page=2     → 明确要看第 2 页
```

* `data.text` 就是这一页的真实正文；`data.page/data.pages` 是当前页/总页；`data.title` 是材料名。
* `data.bookmark` 是最近一次**显式保存**的停点（`page` / `source_revision` / `needs_reread`）；
  没有就是 `null`（这时不带 `page` 会拿到第 1 页）。
* `data.source_revision` 是这一版的真实 id，`data.private_head` 是私人事项的真实 head——
  后面保存停点/写页注要原样带上。
* `data.annotations[]` 是这一版这一页相关的私人笔记/疑问（`kind` 是 `note` 或 `unclear`）。
* **读一次不会写任何东西**：不记录、不落停点、不写文件。翻页是免费的，翻过去不等于读过。

## 2. 导入一段自己的文本（显式写）

```http
POST /api/phone/reading/material
{"content": "第一页…%%PAGE%%第二页…", "expected_revision": "<上一页读到的 source_revision>", "operation_key": "read-import-1"}
```

* `%%PAGE%%` 是唯一的分页标记；没有标记就是**一页**。整段 1..24000 UTF-8 bytes，每页 ≤12000
  bytes，最多 64 页；空正文、空白页、超界都会被拒（原因是 C 的 `CONTENT_EMPTY` /
  `BLANK_PAGE` / `PAGE_TOO_LARGE` / `CONTENT_TOO_LARGE` / `TOO_MANY_PAGES`）。
* `expected_revision` 必须等于当前真实 `source_revision`，否则 409 `READING_REVISION_CONFLICT`：
  重新读一次当前页，再拿新的 `source_revision` 提交（**不要**换 `operation_key` 硬撞）。
* 这是一个**显式操作者给的文本**，不是抓取：手机不联网、不抓 URL、不改标题。
* 旧正文仍在 Supply 的版本历史里；旧停点不会搬到新版相同页，`bookmark.needs_reread` 会变成
  `true`，界面要提示「材料变了，请重看」。
* 同一个 `operation_key` + 同一段正文重放 = `already_applied`（不会多一版）；同一个 key 换正文 =
  409 `READING_OPERATION_KEY_REUSED`。

## 3. 保存停点（显式写）

```http
POST /api/phone/reading/positions
{"page": 2, "source_revision": "<当前 material 的 source_revision>", "expected_head": "<当前 material 的 private_head>", "operation_key": "read-pos-2"}
```

* 服务会**真的读该页**，把「取得证据（这一版这一页、字符数）」与「位置」一次性提交进同一份
  私人文档；不存在「先记一半」。
* 回到前页再保存是合法的：想从第 3 页退回第 2 页继续读，就 `page=2` 保存。
* `page` 必须是 JSON 整数（`2`，不是 `"2"`、不是 `true`、不是 `2.0`）。
* 结果里的 `understanding` 永远是 `"UNKNOWN"`：**位置不是理解证明**。
* 409 常见原因：`READING_REVISION_CONFLICT`（材料换版了）、`READING_HEAD_CONFLICT`
  （`private_head` 过期——重新读 material 拿新的）、`PAGE_NOT_PRESENT`（没有这一页）。

## 4. 写页注：笔记 或 没读懂（显式写）

```http
POST /api/phone/reading/annotations      # 私人笔记
{"kind": "note", "page": 2, "title": "页边", "body": "这一段想到别的事",
 "source_revision": "…", "expected_head": "…", "operation_key": "read-note-1"}

POST /api/phone/reading/annotations      # 真的没读懂
{"kind": "unclear", "page": 2, "note": "这里为什么要换称呼？",
 "source_revision": "…", "expected_head": "…", "operation_key": "read-unclear-1"}
```

* `kind` 只有两种，字段也各自固定：`note` 要 `title`+`body`、不接受 `note`；`unclear` 要
  `note`、不接受 `title`/`body`。写错了是 400，不会被悄悄吞掉。
* **必须先保存过这一版这一页的停点**，否则 409 `PAGE_NOT_ACQUIRED`——先去第 3 步。
* 笔记是私人事项（旧 `GET /api/phone/notes` 里能看到，带可选 `reading_anchor`
  `{source_id, source_revision, page}`：那是**当初那一版那一页**的真实 protocol id，点它可以回到那一页；
  没有锚点的旧笔记行保持原样，不带这个键。锚点是元数据，`/state` 里既没有正文也没有锚点），
  `unclear` 是另一条独立记录：它表示「我没读懂」，不会被伪装成普通笔记，也不代表理解。
* 页注正文只在私人读取面出现（`GET material` 的 `annotations[]`、`GET notes`），**不进**
  `/state`，也不进旧的阅读元数据列表。

## 5. 对账（结果未知时）

```http
GET /api/phone/reading/operations/<operation_key>
```

* 200：owner 已经落地，`data.effect/applied/reason` 说清做了什么。
* 202 `unknown`：仍未定案——**继续用同一个 key 查**，绝不换新 key 重发。
* 404 `not_found`：这个 key 从未落地（可以重新发起**同一个** key 的同一意图）。

## 6. 从模型/工具那边用（`chiyo_phone`）

模型侧有五个动作（同一套闸门、同一条 HTTP 面）：

| 动作 | 作用 | 需要 |
|---|---|---|
| `reading_material` | 读一页（可选 `page`） | `phone.enabled` + `phone.private_artifacts`（受限记忆时） |
| `reading_import` | 导入正文（`content` + `expected_revision` + `operation_key`） | 上面两条 + `phone.write_enabled` |
| `reading_position` | 保存停点（`page` + `source_revision` + `expected_head` + `operation_key`） | 同上 |
| `reading_annotate` | 页注（`kind` + `page` + … + `title/body` 或 `note`） | 同上 |
| `reading_operation` | 原 key 对账 | 读的两把钥匙 |

* 五个动作都属于「私人档案」：`memory` 打开且策略不是 `unrestricted` 时，profile 必须显式写
  `"phone": {"private_artifacts": true}`，否则 `PHONE_PRIVATE_ARTIFACTS_NOT_PERMITTED`。
* `local` 入口、未绑定的 DM、被委派的会话一律没有手机入口。
* 工具会在发出请求前先按同一套形状拒（页码严格、正文 1..24000 bytes、kind 逐字段、
  `artifact_id`/`path`/`url`/`title`/`understanding` 不接受）。
* 工具文案不会把「导入」说成「读过」，也不会把「保存停点」说成「读懂了」。

## 7. 错误码速查

| 码 | 含义 / 怎么办 |
|---|---|
| `READING_MATERIAL_UNAVAILABLE` | 这个根没绑阅读材料（或材料不存在/正文为空）；不能凭空造一个 |
| `SUPPLY_UNAVAILABLE` / `SUPPLY_REFUSED` | Supply owner 不在或拒绝；等它起来再试，别改数据 |
| `PHONE_FACADE_UNIMPLEMENTED` | 这一版 C 的 facade 没这个方法；升级 C，不要伪造空页 |
| `PHONE_READING_ADAPTER_ERROR` | facade 返回了非信封；看服务日志，不要重发 |
| `PAGE_INVALID` / `PAGE_OUT_OF_RANGE` | 页码不是整数 / 超出 1..64 |
| `PAGE_NOT_PRESENT` | 这一版没有这一页（材料变短了） |
| `PAGE_NOT_ACQUIRED` | 还没保存过这一版这一页的停点，先 `reading_position` |
| `READING_REVISION_CONFLICT` | 材料换版了；重读 material 拿新 `source_revision` |
| `READING_HEAD_CONFLICT` | 私人事项 head 过期；重读 material 拿新 `private_head` |
| `READING_OPERATION_KEY_REUSED` | 同一个 key 想干另一件事；换新 key 表示新意图（不是重试） |
| `READING_CONTENT_INVALID` / `CONTENT_TOO_LARGE` / `BLANK_PAGE` / `PAGE_TOO_LARGE` | 正文越界：整段 ≤24000 bytes、每页 ≤12000 bytes、不许空白页 |
| `ANNOTATION_KIND_INVALID` / `ANNOTATION_TITLE_REQUIRED` / `ANNOTATION_BODY_REQUIRED` / `ANNOTATION_NOTE_REQUIRED` | 页注种类或字段不对 |
| `UNKNOWN_QUERY:<k>` / `QUERY_REPEATED:page` | `GET material` 只接受一个 `page` |
| `CLIENT_IDENTITY_REFUSED:<k>` | 你带了 artifact/uid/owner/source 之类不该由客户端声明的字段 |
| `UNKNOWN_FIELD:<k>` | 这个动作不接受这个字段（例如 material 里带 `title`） |
| `CSRF_TOKEN_MISSING` / `CSRF_TOKEN_INVALID` | 重新 `GET /api/phone/state` 拿 token |
| `HOST_NOT_ALLOWED` / `ORIGIN_NOT_ALLOWED` / `CROSS_SITE_REFUSED` | 只允许本机同源访问 |

## 8. 已知边界（别误会成「功能坏了」）

* 手机只给**一页**正文：它不会把整本书塞给浏览器，也不把正文写进 `localStorage`
  （UI 是 U 的写集，正文用 `textContent` 渲染，不执行 HTML/脚本）。
* 不存在的材料不会补演示文本：`available:false` 就是没有。
* 手机不判断「读懂没读懂」：`understanding` 恒 `UNKNOWN`。
* 没有自动模型读书、没有网络抓取、没有「在桌边/灯亮着所以读过了」这类推断。
* 读物不是 World 实体书：手机不扣款、不加书架容器、不新增世界动词。
