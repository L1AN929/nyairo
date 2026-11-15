# 居家收官：重逢锚点、夜茶菜谱、待定小约定

Status: implemented

## Problem

居家三弹已把窗边/冰箱贴/信、创作托盘/小物履历/交换匣读了出来，但「重逢时她还记得什么」
与「两个人一起留下的小东西」没有可披露的读面：手机既不能把「上次在家读到哪、哪些分享已
投递、哪些东西真实买过/摆在哪」存成一张安全快照再比对，也没有双方显式共同维护的菜谱卡，
更没有只表示「暂定」而不变成核心安排的小约定。缺的三件事都必须只投影既有 owner 事实，不能
把未发私文、未送达购买或同名菜冒充成已发生。

## Decision

新增 `components/phone/home_hearth_view.py`（薄投影 + 三份实例内 JSON），并只在
`components/phone/service.py` / `server.py` 接线；owner 仍是 World、Supply、
`PhoneReading`、`LifeSharingService`、产权 economy + Body substrate、`own_day_read`。

1. **重逢锚点** `GET/POST /api/phone/home/reunion-anchor`：唯一动作 `capture`，CSRF 即可，
   客户端只发 `action`。服务端现读安全事实——本地时间与时钟来源/偏移、World 可信位置区域、
   读物 `artifact_id + source_revision` 与已保存停点、`LOCAL_DELIVERED` 分享 id 集合、
   已购物件 id/安全名/摆放摘要——写 `phone/reunion_anchor.json`。重复同状态忽略抓取时间，
   不增 revision；源不可读标 unavailable/unknown，不当 0。GET 用真实 id 集合比较：身份变了
   就说不可比，页码/位置未知就不猜，DRAFT 不算新投递，购买不冒充送达/移动。
   `no_absence_debt=true`，不自动催促、不算关系分、不调模型、不改 inbox 已读。
2. **夜茶菜谱** `GET/POST /api/phone/home/recipes`：`upsert` 单动作，12 卡上限，
   `title≤40`/`memo≤240` 必填非空；`recipe_id` 可省（按 `author_side+title` 派生稳定 id，
   同 create 重放不多卡）。作者侧只选验证策略：her 写需 CSRF+cap，partner 写仅 CSRF（错误
   cap 仍 403）；既有卡作者不可改，双方都不能覆盖对方卡。GET 附 `world_phone_shopping.inventory`
   的真实购买/消费参考区（≤6），显式 `not_evidence_of_cooking` / `consumption_claimed=false`。
3. **待定小约定** `GET/POST /api/phone/home/tentative-plans`：`propose/reschedule/drop`，
   6 条上限，状态仅 `TENTATIVE/RESCHEDULED/DROPPED`，`proposer` 不变；重复动作不增 revision，
   DROPPED 不复活。her 项需 CSRF+cap，partner 项只需 CSRF；每项
   `auto_hijack_activity=false`/`no_obligation=true`，绝不导入核心安排、`FutureItems`、
   canonical Activity 或 Memory。GET 默认只给正式今日安排的安全数量/状态/日期，暂定另列；
   带本人 cap 才沿既有授权附计划标题。安排源不可读具名 unknown，不假空。

新增 `tests/test_home_hearth_view.py`（纯投影 + 真实 owner loopback）、
`scripts/home_hearth_fixture.py`（复用 `workbench_curio_fixture` 真链，零模型）、
`.claude/launch.json` 的 `home-hearth`（3856）、`docs/engineering/home-hearth-api.md`。

## Alternatives considered

**给锚点存完整可回放时间线/正文，让「重逢」更像回忆录。** 最强理由：一次读面就能讲清「上次
到这次发生了什么」，不必每次现读 owner，体验最完整。否决：那会复制分享正文、未发标题、作品
笔记和私人时间线，直接违反既有隐私铁律；而且快照会与 owner 漂移，无法证明「真实新增」。改为
只存 id/身份/摆放摘要，GET 每次现读比较，缺源就说 unknown。

**为消费关联新增自由字段或让客户端自报「我做过这道菜」，并在 World 侧加一个手机专用
GET 消费回执接口。** 最强理由：菜谱与「真的吃过」直接挂钩最自然，前端也最省事。否决：自报
事实不是 owner 证据；改对外 World 协议超出本单红线。沿用 `inventory()` 的真实 `consumed` /
`receipt_id`，并明确购买/消费分开、同名不等于吃过；生成 fixture 的 World 未声明该点面可消费，
就不跑 CONSUME、不手造回执，如实标注未覆盖。

**给暂定项做一个独立日程 store / timer，到期自动变成今日安排。** 最强理由：用户会觉得「约定
了就会提醒」，产品感更强。否决：引入第二日程权威与自动义务，违反「暂定可改可放下、无缺席债」
与不造 timer 的既有决定。改为纯本地 6 条状态，GET 与正式 arrangements 分列，永不自动执行。

## Consequences

- 三份状态都可缺失、可损坏时 fail closed；GET 一律纯读（缺文件不建目录），写只走显式动作。
- 锚点只描述安全快照，重复 capture 幂等；菜谱/暂定的同意图重放不刷 revision，编辑才递增。
- 父级验收：仅新专项一次21例18.313s OK/exit0（8真实ownerHTTP、13纯投影/状态用例）；3856全新root三GET与capture/双方菜谱/暂定提议改期放下实际200，重放不涨revision，跨作者/越权409、凭据/CSRF403，15条空值额外字段全部400且状态不变。真实翻页+2/新投递/新购买delta由专项正式owner写口验证；未知位置/换读物身份不猜差值。两冻结字节不变，服务已停、新根保留，产品0新增/573余427，无旧root/删除/commit/push/桌面UI改动。短结果 `.agents/worker-runs/20261111-home-hearth-parent-result.md`。
- 未覆盖：正式 `CONSUME`、真实物件搬动与resident自然持续运行；零写只证无resident长tick观察窗口，不承诺World后台永不写。仅居家四弹后端限定收官，不代表整个v2.0设计或自然生活完成。
