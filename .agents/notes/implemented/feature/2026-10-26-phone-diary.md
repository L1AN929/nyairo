# Agent Note: 手机日记 —— 她自己的文字，普通读面只看得到时间

Status: implemented

## Problem

设计 v2 第 15 章:93 要的是她**自己写**的真实日记；:201 要求私人经历默认 private、后续披露
另选、账号运维另授权且不承诺管理员绝不可读；:273 要求私人作品不自动成为待分享，初版可以复用
已有存储与读模型、不新造 owner。已有的笔记（memo）是给阅读批注等用途的，和"按日期积累的日记"
混在一起会让普通笔记面天然带出日记正文；而另造一个日记 owner/数据库又会复制 Supply 的版本、
CAS 与权限。

## Decision

1. **日记是私人事项文档里的独立 item type**：`private_daily_items.ITEM_DIARY`
   （`diary_entry`），写进这台实例**已有**的 private-items artifact；不新 Supply 表、不新
   owner、不新数据库。条目只含 `body`、写入时间、字数和修订数，写一次不可改（没有 revise
   入口），因此不会悄悄改写过去某天。
2. **一条 operation_key 对应一条日记**：phone facade 用 `diary:<sha256(key)[:16]>` 生成条目
   id。同 key 重试落在同一条；不同 key 即使文字相同也是两条（同一天写两遍同样的话不是重试）。
   日期取手机服务自己的 `_clock()`，调用方不能自带时间。
3. **普通读面默认只有元数据**：`GET /api/phone/diary` 只返回数量、日期、每条的字数与写入时间；
   `GET /api/phone/diary/<id>` 默认（`X-Phone-Actor` 缺失/不是 `model`，即 operator 普通页面）
   同样只返回元数据并带 `body_withheld: true`，正文不出现。只有她自己的工具调用
   （`diary_read` 带既有 `X-Phone-Actor: model` 声明）才返回正文。`/state` 的 `diary` 应用只报
   `item_count` / `latest_at` / `dates`。
4. **工具与路由一起接**：`chiyo_phone` 增加 `diary`（目录）、`diary_read`（正文）、
   `diary_write`（写，`operation_key` 必填）；HTTP 增加 `GET /api/phone/diary`、
   `GET /api/phone/diary/<id>`、`POST /api/phone/diary`。没有页面、没有标题、没有摘要、
   不进 Memory、不外发。
5. **既有入口不旁路**：日记不是 project，私人事项 artifact 本来就在 `projects` 的排除名单里；
   `notes` 只读 `ITEM_MEMO`；项目/版本/比较/来历/上下文读口都不会碰到日记条目。

## Alternatives considered

- **每条日记建一个 private project artifact** —— 否决：项目面的读、版本、比较、来历、
  context 都要各自加分类/排除才能不泄露正文，漏一个口就是隐私事故；写入还得多一次
  artifact 创建。
- **新造日记 owner / 新数据库** —— 否决：复制 Supply 的版本、CAS、grant 和撤权语义，违反
  "复用现有 Supply/private project 正规存储和权限，不另造 owner"。
- **任何 GET 都返回正文，靠页面不展示** —— 否决：隐私不能建立在"普通读面碰巧不渲染"上；
  默认响应必须不带正文。
- **引入新的 subject token / 新鉴权规则** —— 否决：这是任务写明的停止条件；现有
  `X-Phone-Actor` 已是服务自己的声明通道，配合"默认扣留"足够表达普通读面边界，且不扩权限。
- **每天自动生成 / 自动写入 Memory** —— 否决：设计要的是"想写才写"，普通日子没有最低配额；
  自动补写会把沉默变成伪造的经历。

## Consequences

- 日记正文与既有私人笔记共用同一份 Supply artifact 和同一套 CAS/grant；普通状态与手机读面只看
  得到数量、日期、字数，看不到正文、摘要或派生标题。
- `X-Phone-Actor` 是可由客户端填写的**声明，不是主体身份鉴权**：任何能访问本地接口的客户端若自行声明 `model`，也可能取得正文，不必具有管理员或文件读取权限。因此这里只承诺普通读面默认扣留正文，不承诺技术上的“仅她可读”，更不承诺管理员隔离。
- 没有新增页面、外发通道、Memory 准入或模型调用；operator 的普通项目/版本/搜索入口不会带出
  日记正文。
- 验证：`scripts/phone_diary_demo.py` 用真 Supply + 真 `phone_handler` 写两条、原 key 重放
  不增条、同 key 换文字明确拒绝、列时间、读回正文、普通 operator 读面扣留、正规停服后同根
  重开仍可读，14 项结果全 yes；另跑 `tests.test_phone_tool` 13 例与 `tests.test_phone_api`
  的 state 单例。未跑全库，未提交/部署。
- **2026-10-27 边界复核**：写入侧不是靠 `X-Phone-Actor` 放行，而是走既有 Life Supply
  `commit_markdown` 的 Governance grant（`COMMIT_MANAGED_ARTIFACT` / `artifact:personal` /
  `workspace:<id>`）和 `ManagedArtifact` 的 `WriterFence` epoch + SQLite 写守卫，旧 writer /
  第二 writer 会在提交前被拒。读取侧仍只有客户端声明：本机回环上的任何客户端都能自行发
  `X-Phone-Actor: model` 取得正文；CSRF token 由 `GET /api/phone/state` 发放，只是跨站防护，
  不是身份。服务端可核验的 model/operator 凭据尚未实现，按停止门交
  [私人日记可信身份方案](../../../../docs/engineering/private-diary-authority-options-20261027.md)。
- **2026-10-27 已补本地能力凭据**：每次 serve 签发绑定 subject + 实际 endpoint 的 0600
  不透明凭据，日记正文读取与主体写入必须持有；缺失/错误/无法安全存储时 fail closed，
  operator 元数据与其它手机面不变。威胁边界仍是“能读凭据文件/同 uid/管理员在外”，
  不承诺仅她可读。决定与取舍见
  [phone-diary-capability](2026-10-27-phone-diary-capability.md)；旧 note 的
  “只有客户端声明”限制到此为止，但 S14/S13 整体仍未全过。
