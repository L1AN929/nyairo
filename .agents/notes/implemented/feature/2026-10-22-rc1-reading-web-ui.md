# Agent Note: rc1 阅读工作台——真实正文、显式停点、原页批注与旧版需重看

Status: implemented

## Problem

手机里的「阅读」app 只有一个元数据列表：`GET /api/phone/reading` 把 PDI 里的
`source_acquisition` 与 `reading_position` 行原样摊出来，屏幕上能看到「取得过什么、位置是几」，
**看不到要读的那一页的字**。于是三件事都做不了：

- 生活链缺了「她真的在读」这一段：设计包 13:9/16/37、18:30/62-64 要的是可继续的私人阅读面，
  不是一张「取得清单」。
- 现有 `reading_position` 的最高页 helper 不是「用户最近显式保存的停点」。把它当停点显示，
  等于替用户宣布「你已经读到第 3 页了」，而她自己翻回第 1 页却没保存——界面在说谎。
- 版本会变（Supply 保留旧版本），旧停点/旧批注落在旧版本上时，界面最容易犯的错是「悄悄跳到当前
  版本的同一页」，看起来像恢复，实际上把两版内容当成同一份。

同时不能用「翻页即记录」来假装有进度：GET 一旦落库，读一页就等于写一次，谁都能被写成「读过了」。

## Decision

只改 `components/phone/web/{app.js,style.css}`（U 写集），把阅读屏改成四块工作台，全部字段按施工单
从 `data` 读，绝不猜 flat：

1. **正文来自 `GET /reading/material[?page=N]`**。无 `page` 就让服务按「最近显式保存停点，否则 1」
   决定；`page` 只在严格整数（`typeof === 'number'`、整数、≥1，bool/字符串/小数一律 null）时拼进 URL。
   正文用 `textContent` 写进 `p.reading-text`（`white-space: pre-wrap`），与文件头第 3 条铁律一致，
   仍然过 `sanitizeText`。
2. **翻页是纯读**：上一页 / 下一页 / 跳页只发 GET；`保存阅读停点` 是唯一会写 positions 的按钮，
   用当前 `source_revision` + `private_head` 做 CAS。写成功后 `refreshAfter('reading-position')`
   重读材料取新 `private_head`；重读没回来时 `readingView()` 返回 null，按钮不渲染，**物理上不可能**拿旧 head 连写。
3. **导入是显式替换**：`%%PAGE%%` 分页，正文 1–24000 字节、单页 ≤12000 字节、≤64 页、拒空白页，
   字节数用自写的 `utf8ByteLength`（不依赖 `TextEncoder`，node 检查里也算得出来）；必过确认弹层，
   payload 只有 `{content, expected_revision, operation_key}`——标题、来源回执、理解状态一律不声明。
4. **两类批注分开**：note（标题+正文）与 unclear（疑问）各一套表单、各一个 operation scope
   （`reading-note:<rev>:<page>` / `reading-unclear:<rev>:<page>`），`buildAnnotationPayload` 按 kind 白名单
   出键；被服务回 `PAGE_NOT_ACQUIRED` 时屏上出现「保存第 N 页停点」的显式入口，而不是自动补写。
5. **旧版不冒充当前版**：`readingBookmarkView`/`readingAnnotationView` 把 `bookmark.source_revision` 与
   `data.source_revision` 对比，不一致就 `needsReread`/`stale`、并且 `canNavigate=false`——旧版批注只显示
   一句「界面不会借它把你带到当前版本的第 N 页」，没有跳页按钮。
6. **笔记锚点是显式入口**：`GET /notes` 的 `reading_anchor` 在列表里只当一个 chip 加一个独立按钮
   （`<button>` 不能嵌在修订按钮里），点击后**先读当前版本**再比 `source_revision`：一致才跳那一页，
   不一致就停下来显式问「按当前版本看第 N 页」。锚点里的 `source_id`/`source_revision` 走 `protocolId`
   原字节，屏幕另走 `safeLabel`，payload 与导航永不使用展示标签。
7. **未决键走自己的查询口**：阅读 scope（`reading-import` / `reading-position` / `reading-note` /
   `reading-unclear`）在 `queryOperation` 里路由到 `/reading/operations/<key>`，不落 `/operations/<key>`
   这条购物线；`not_found`/`unknown_operation` 才算结案，`unknown` 保留原键。
8. **内存与存储边界**：正文、笔记、导入文本只存在 `store.readingDraft`（内存），冲突时不清空草稿、
   不自动重发；sessionStorage 仍然只有 op 的 `key/at/path` 与导航位置，没有新增键，也没有正文。
9. **诚实状态**：loading / offline / error / readonly / empty / unavailable / disabled / malformed(无 data) /
   unknown / conflict 各有文案；`available:false` 时没有正文块、没有停点按钮、导入按钮禁用；
   `understanding` 只显示服务给的值（UNKNOWN 显示「理解：未知」），界面不判定读懂。房间与灯不显示、不绑定，
   只留一句“不声称在桌边、灯亮或翻过一页就等于读懂”。

## Alternatives considered

- **把最高页 helper 当停点显示** — 立论：不用等服务，屏幕立刻有「第 3 页」。否决：那不是用户保存的停点，
  翻回第 1 页没保存时界面会显示 3；恢复语义一旦掺水，验收里「重启仍回到第 1 页」就永远对不上。
- **翻页顺手 POST positions（省一次点击）** — 立论：体验顺滑、进度不会丢。否决：读一页就写一次等于把
  「读过」变成自动事实，施工单明确 GET 绝不记录位置；而且每次翻页都会消耗 CAS head，冲突率暴涨。
- **导入时一起发 title** — 立论：材料叫什么是用户自己的事。否决：契约把 title 归服务（来自 artifact/版本），
  客户端声明就是伪造来源；界面只在 `<h2>` 里显示服务返回的标题，缺了就写「服务没有给标题」。
- **旧版批注给一个「跳到当前版第 N 页」按钮** — 立论：少一次手动翻页。否决：两版内容可能完全不同，
  「跳到同一页」看起来像恢复；现在只给一句解释，要看得自己显式点「按当前版本看第 N 页」。
- **冲突后自动用新 head 再提交一次** — 立论：用户意图明确，帮他重试。否决：那是静默覆盖别人的写入；
  现在保留草稿、给出「重新读取材料」入口，由人决定。
- **把阅读 operation 复用购物 `/operations/<key>`** — 立论：少一个端点。否决：那会把 Supply 的操作键丢进
  购物账本查询，语义串线；施工单要求原键查 `/reading/operations/<key>`。
- **给阅读屏顺手读 `/world` 显示房间与灯** — 立论：氛围更好。否决：多一条与阅读无关的 GET，而且很容易
  被读成「在桌边/灯亮就等于读过」；本轮只写边界声明，不做展示。

## Consequences

- **收益**：阅读屏第一次是一条能走完的链——粘贴本地多页正文 → 翻页只读 → 显式保存第 2 页停点 →
  这一页写私人笔记或疑问 → 翻回第 1 页再保存 → 重启后停在保存过的那一页 → 新材料版本让旧停点/旧批注
  显示「需要重看」。协议值（artifact/版本/操作键/批注 id）全程原字节，展示另走 `safeLabel`。
- **代价**：导入必须点两次（先确认），翻页后必须显式保存才有停点；屏幕上「已保存停点」常常落后于
  正在看的页码——这是刻意的，比一个假的进度条可信。
- **诚实性**：服务没按契约给 `data` 时屏幕直接说「服务没有按契约返回数据」，不会去顶层捡同名字段；
  真实接口落地后若字段不同，坏在明处而不是悄悄补一个看起来对的正文。
- **测试形态**：新增 `tests/test_phone_reading_ui.mjs` 25 条（14 条 stub DOM 点击链：真实 payload 键集、
  翻页零写、停点后换 head、刷新失败不写、PAGE_NOT_ACQUIRED 提示、冲突留草稿、unknown 原键 +
  `/reading/operations/<key>`、typed `not_found` 算答案、正文注入不执行、sessionStorage 无正文、
  旧锚点不跳页、诚实状态、390px 静态规则）。是 stub DOM，**不**证明浏览器布局/焦点环/软键盘，
  真实串链留给父级在同实例里做。旧 `test_phone_ui.mjs`(34) / `test_phone_world_ui.mjs`(25) /
  `test_room_living_ui.mjs`(21) 在本轮后仍全绿（合计 105）。
- **跨契约核对（收尾时 C/P 已落地）**：`components/life/scripts/phone_reading.py` 的 `material()` 与我消费的字段
  逐个对上（含 `bookmark{page,source_revision,needs_reread}`、note/unclear 两种注解行、`understanding=UNKNOWN`）；
  原因码表随后改成 C 真正会发的值（`READING_*` / `PAGE_NOT_*` / `CONTENT_*` / `BLANK_PAGE` / `TOO_MANY_PAGES` /
  `PAGE_TOO_LARGE` / `ANNOTATION_*` / `SUPPLY_*` / `PRIVATE_ITEMS_UNAVAILABLE`）。对账接口的
  “没这条记录/仍未知”是 `ok:false + status:not_found|unknown`，界面把它当 typed 答案（结案/保留原键），
  不误报成查询失败；材料 GET 的 409/503 原因也会写进面板。
- **已知缺口**：`components/phone/service.py` 的 `notes()` 还没返回 `reading_anchor`，所以普通笔记里的
  阅读锚点入口在真实实例上暂时不出现（界面侧已就绪，等 P 带字段）；阅读屏不读整份材料（只读一页）、
  不做字数/进度百分比、不做阅读时长与理解评分、不碰 World 动词与书架容器；房间/灯只读展示本轮没做；
  本轮没有真浏览器/真实例验收（未连 live、未重启、未导入父级文本）。
