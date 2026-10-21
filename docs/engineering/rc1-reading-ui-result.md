# rc1 阅读工作台（U 写集）实现结果

范围：`components/phone/web/{app.js,style.css}`（`index.html` 未改），新增 `tests/test_phone_reading_ui.mjs`、本文件、独占 note
`.agents/notes/implemented/feature/2026-10-22-rc1-reading-web-ui.md`。没有改服务端（P）、Life（C）、World/Body、Supply、MANIFEST 或任何 fix 脚本。

## 1. 消费的精确接口面（全部新字段只从 `data` 读）

| 调用 | 何时发出 | 字段 / payload |
| --- | --- | --- |
| `GET /api/phone/reading/material` | 进入阅读屏、点刷新、从锚点进入（先读当前版本） | 无 page 参数：由服务按“最近显式保存停点，否则 1”决定 |
| `GET /api/phone/reading/material?page=N` | 上一页 / 下一页 / 跳页 / 锚点版本一致时 | `page` 只在严格整数时拼进去 |
| `POST /api/phone/reading/positions` | 只有用户点「保存阅读停点」 | `{page, source_revision, expected_head, operation_key}` |
| `POST /api/phone/reading/material` | 只有用户在确认弹层点「替换」 | `{content, expected_revision, operation_key}` |
| `POST /api/phone/reading/annotations` | 「保存这一页的笔记」/「记为疑问」 | note：`{kind:'note',page,source_revision,expected_head,operation_key,title,body}`；unclear：`{kind:'unclear',…,note}` |
| `GET /api/phone/reading/operations/<key>` | 未决操作的「查询这次操作」 | 原键查询，不换键、不落到 `/operations/<key>` |

读取视图（`readingMaterialView`，`components/phone/web/app.js:690-770` 附近）消费：`available, artifact_id, source_id,
source_revision, version_number, title, page, pages, text, content_available, private_head,
bookmark{page,source_revision,needs_reread}, understanding, annotations[{id,kind,title,body,note,source_id,
source_revision,page,created_at,stale?,needs_reread?}]`。顶层同名键**不**当契约：没有 `data` 就渲染
「服务没有按契约返回 data」，不去猜 flat。

## 2. 界面行为

- **正文/分页**：`textContent` 写入（`sanitizeText` 去控制字符、遮罩本机路径与凭据，仍不拼 HTML），
  `white-space: pre-wrap`；页码 `第 N / M 页`、上一页/下一页/跳页都是纯 GET。
- **显式存停点**：`保存阅读停点` 用当前 `source_revision` 与 `private_head` 做 CAS；成功后 `refreshAfter`
  重读材料拿新 `private_head`，刷新没回来（`phase !== 'ready'`）时按钮根本不存在，杜绝旧 head 连写。
- **导入正文**：`%%PAGE%%` 分页，正文 1–24000 字节 / 单页 ≤12000 字节 / ≤64 页 / 拒空白页，实时字节与页数计数；
  必过确认弹层（写明“替换、旧版本保留、旧停点 needs_reread”），payload 只有三项，标题/回执/理解都不声明。
- **原页批注**：note（标题+正文）与 unclear（疑问）两组独立表单与独立 operation scope；被拒 `PAGE_NOT_ACQUIRED`
  时出现「保存第 N 页停点」的显式入口。
- **旧版诚实**：停点版本与当前 `source_revision` 不同 → 标 `旧版本的停点 … 需要按当前版本重看`；
  旧版批注标 `旧版本 · 需重看` 且**没有**导航按钮，只写一句“界面不会借它把你带到当前版本的第 N 页”。
- **笔记锚点**：`GET /notes` 的 `reading_anchor` 只做可选入口；点「回到阅读第 N 页」先读当前版本，
  版本一致才跳那一页，不一致就停下显式问「按当前版本看第 N 页」。协议的 `source_id/source_revision` 原字节保留，
  屏幕另用 `safeLabel`，payload 与导航永不使用展示标签。
- **状态**：loading / offline / error / readonly / empty / unavailable / disabled / malformed(无 data) /
  unknown / conflict 各有独立文案；`available:false` 时不渲染正文块、不给停点按钮、导入按钮禁用。
- **内存边界**：正文、笔记、导入文本只存在 `store.readingDraft`（内存）；sessionStorage 仍然只有 op 的
  `key/at/path` 与导航位置，没有新增键。
- **房间/灯**：不显示、不绑定，只留一句“界面不声称在桌边、灯亮或翻过一页就等于读懂”。

## 3. 验证命令与退出码

```
node --check components\phone\web\app.js                              → exit 0
node --test tests/test_phone_reading_ui.mjs                           → 25 tests, 25 pass, 0 fail, exit 0
node --test tests/test_phone_ui.mjs tests/test_phone_world_ui.mjs \
            tests/test_room_living_ui.mjs tests/test_phone_reading_ui.mjs → 105 tests, 105 pass, 0 fail, exit 0
```

新增 25 条里与验收项对应的：

| 验收要求 | 用例 |
| --- | --- |
| click-level 真实 shape | `保存停点：POST 形状精确…`、`原页笔记与疑问：payload 按 kind 白名单`、`导入正文：确认弹层后 POST 只带三项` |
| 版本旧停点 | `旧版本停点与旧版本批注：标 needs_reread，且不许导航到当前页同号`、`旧版本锚点不悄悄跳页` |
| opaque ID | `opaque ID：注解 id/版本/键原字节进视图，展示标签另走 safeLabel` |
| 未知查询路由 | `结果未知：原键不换，查询走 /reading/operations/<key>`（并断言 `/operations/<key>` 未被请求）、`对账的 typed 回答：not_found 是答案` |
| GET 不写 | `翻页只发 GET：两侧都不产生写请求`、`刷新没回来就不写：不拿旧 head 重发停点` |
| 正文注入不执行 | `正文注入不执行：<img>/<script> 只是文本`（textContent 原样、无子节点、无 __pwned） |
| 不加正文到存储 | `正文与笔记不进 sessionStorage：只留 key/at/path` |
| 诚实状态 | `诚实状态：禁用 / 不可用材料都不补演示正文`、`PAGE_NOT_ACQUIRED`、`冲突：保留草稿` |
| 390 / 换行 | `390：正文块可换行、翻页与导入按钮不写死宽度` |

## 4. 缺口与偏差（照实写）

1. **收尾时已与真实实现对齐**：开工时 `components/life/scripts/phone_reading.py` 还不在树里、`components/phone/server.py`
   只有 `GET /api/phone/reading`；收尾时 C 的 facade 与 P 的四个路由已落地，界面按真实实现逐项核对后补齐：
   - 原因码表换成 C 真正会发的值（`READING_REVISION_CONFLICT` / `READING_HEAD_CONFLICT` / `READING_DOCUMENT_VERSION_MISMATCH` /
     `READING_WRITE_REFUSED` / `READING_GRANT_REQUIRED` / `READING_OPERATION_KEY_REUSED` / `PAGE_NOT_ACQUIRED` /
     `PAGE_NOT_PRESENT` / `PAGE_INVALID` / `CONTENT_INVALID` / `CONTENT_EMPTY` / `CONTENT_TOO_LARGE` / `BLANK_PAGE` /
     `TOO_MANY_PAGES` / `PAGE_TOO_LARGE` / `ANNOTATION_KIND_INVALID` / `ANNOTATION_FIELD_NOT_ALLOWED` /
     `READING_OPERATION_UNKNOWN` / `READING_OPERATION_NOT_FOUND` / `SUPPLY_UNAVAILABLE` / `SUPPLY_REFUSED` /
     `PRIVATE_ITEMS_UNAVAILABLE`），没列出的码原样显示。
   - 对账接口是 typed 回答：`GET /reading/operations/<key>` 的成功是 `ok:true,status:applied`，
     “没这条记录/仍未知”是 `ok:false + status:not_found|unknown`（404/202）。界面把这两种当成**答案**处理
     （结案/继续保留原键），不误报成“查询没有成功”。
   - 材料 GET 的非 ok 信封（`READING_MATERIAL_UNAVAILABLE` 503、`PAGE_NOT_PRESENT` 409）带真实 `reason_code`，
     界面现在把原因写在面板上；`data.available=false` 的信封照“材料不可用”渲染。
2. **P 的 `GET /notes` 还没带 `reading_anchor`**：界面侧已就绪（`noteListView` 解析锚点、列表给独立按钮、
   旧版本不直接跳页），但当前 `components/phone/service.py` 的 `notes()` 不返回该字段，所以这个入口在真实实例上
   暂时不出现。这是 P 写集，本轮没有改。
3. **没有真浏览器验收**：25 条里 14 条是 stub DOM 点击链，只证明接线与字段搬运，不证明布局、焦点环、软键盘。
   390px 只是静态 CSS 断言。真实页面串链留给父级在同实例里做。
4. **`GET /reading/material` 的成功 status 未限定**：`deriveReadOutcome` 沿用既有读取语义
   （`ok===true` 或 `applied/already_applied/ok/available`），没有把阅读单独收紧成某一个字面量。
5. **正文经 `sanitizeText`**：与控制字符/本机路径/凭据遮罩共用一条规则（文件头第 3 条铁律）。
   纯文本读物不会被改字，但正文里真写了 `C:\Users\...` 或 Bearer 会被遮成占位符——这是刻意保留的一致性，
   不是漏。
6. **不确定的服务上限**：批注标题 80 / 正文 4000 / 疑问 2000 是按 memo 的既有上限设的界面侧限制；
   若 P 的上限更小，服务拒绝后界面显示原因码，不会自己截断正文。
7. **没有做**：阅读室的房间/灯只读展示（本可以复用 `/world`，但会多一条与阅读无关的 GET，本轮没做）；
   理解评分、LLM、自动读懂、World 新动词、书架容器——按施工单一律不碰。
