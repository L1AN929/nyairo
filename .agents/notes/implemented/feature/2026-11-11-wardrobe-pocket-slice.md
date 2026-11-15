# 衣柜梳妆、歌单收藏想试小列表、作品旧版安全并排（0.2 补齐）

Status: implemented

## Problem

设计 15 章 2.1 / 2.2 / 2.3 / 2.8 / 4.6 与 13 章 3 要的不是新 store，而是三个
「只投影既有 owner 事实」的小片：她显式选过的穿搭与换季软装、几类有界个人卡片
与伴侣的短建议、同一作品两个真实历史版本的安全并排。既有能力都在，但手机端没有
把它们收成能用的小片；如果桌面自己拼，很容易把「选了风格」说成 World 穿戴或产权，
把季节写成偏好推断，把 `shared=false` 的私人卡片继续缓存显示，或者拿一封更新版本的
分享冒充更早版本的正文已经公开。

## Decision

1. **新增薄投影** `components/phone/wardrobe_pocket_view.py`，只拥有两份实例 JSON
   （`phone/wardrobe.json`、`phone/pocket_lists.json`）和纯投影/状态函数；
   `PhoneService` 向真实 economy / `PrivateProjectService` / 分享 owner 现读。
   它与 [[2026-11-11-workbench-curio-trio]]、[[2026-11-11-window-keepsake-and-slow-letters]]、
   [[2026-11-11-somatic-comfort-slice]] 是同一条「只投影既有 owner 事实」链上的相邻面。
2. **衣柜只存搭配选择**：`GET/POST /api/phone/home/wardrobe`。homewear/outdoor 风格、
   发带/发夹、搭配呼应备注、blanket/cushion 换季选择；只记显式风格，明确
   `selection_only` / `not_ownership_claim`，不改 World 穿戴，也不改既有 somatic 外衣
   穿挂，避免第二个同物件真相。若她点名 `item_instance_id`，服务向真实
   `world_property_economy` 现读核对 `OWNED` / 本人 / 类别适用 / 当前可读；假 id、
   他人物、非 OWNED、点心充发带都具名拒绝且零写。当前目录没有声明任何衣柜类别，
   所以 `related_owned_items=[]` + `NO_OWNED_RELEVANT_ITEMS`，**不造商品**。
3. **小列表只存显式卡片**：`GET/POST /api/phone/pocket/lists`。三类各 ≤12：
   playlist（title + 喜欢理由，`active|resting`，resting 保留理由）、favorites
   （title + 读前期待 + 读后取舍，`saved_unread|reviewed`，reviewed 只是自报回看，
   不声称读懂）、want_to_try（`active|paused|dropped|done`，无义务）。
   `shared` 缺省 false，本人显式设置；默认 GET 只给 `shared=true` 的 title/status 与
   伴侣建议，不给私人数量、entry id、理由、期待、取舍、备注或 operation key；
   `shared=false` 后下一次 GET 立即扣留，无缓存。个人写要 CSRF+capability；
   伴侣建议整体替换最多 3 条、只需 CSRF、标 `partner_contributed=true`，
   不能夹带个人 entry/shared 字段，也不自动采纳进私单。保存与 GET 不调用阅读、
   模型、记忆写，不建 FutureItem。
4. **旧版并排只投影两个真实版本**：`GET /api/phone/pocket/work-revisit`，query 精确
   `artifact_id` / `left_version_id` / `right_version_id`，拒绝未知/重复/缺失参数与
   跨作品拼版；相册/资料/研究/实践文档与分享信封都按既有 owner 或 title marker 拒绝。
   本人视角读两个真实版本的 ≤600 字符摘要，
   `excerpt_basis=owner_version_excerpt`。伴侣视角对**每个精确 source version**
   单独要求真实 `LOCAL_DELIVERED` 本地分享（核受众/频道/receipt/来源可读），摘要取
   **该版已投递分享正文**，`excerpt_basis=delivered_share_text`；无该版投递即
   `VERSION_NOT_DELIVERED`。已归档来源 `withheld` + `ARCHIVED_SOURCE`，不借 history
   读口绕开当前生命周期。既有 disclosure grant 即便仍有效，也不单独当作已投递。
   对比只给具名结论/范围，不给差异片段，不起新分享、不授许可。
5. **窄接线**：`server.py` 加三 GET、两 POST，复用既有 Host/Origin/Fetch-Site、
   CSRF 与本次 serve 的 capability；`X-Phone-Actor` 不是身份。动作外字段按存在性拒绝
   （`[]`/`null`/`""` 都算该动作不接受的字段），必需文本空值 400。
6. 新增 `tests/test_wardrobe_pocket_view.py`、`scripts/wardrobe_pocket_fixture.py`
   （复用 `workbench_curio_fixture` → `window_keepsake_fixture` 的真实 owner 构造，
   再加两件正常提交/投递/归档的作品）、`.claude/launch.json` 的 `wardrobe-pocket`（3858）、
   `docs/engineering/wardrobe-pocket-api.md`。

## Alternatives considered

- **把衣柜选择写进 World / Body，做「第二套真值」。** 最强理由：一次读取就稳定，
  不必区分风格选择与真实穿戴。否决：World/Body 没有这些字段，扩协议会造第二真相，
  也会让「选了发带」看起来像已穿戴或已拥有。只存本实例显式风格，声明不等于产权。
- **按 catalog 名字或「可携带」推断哪件是衣柜/发饰。** 最强理由：实现最短，能立刻给
  点心一个发带槽位演示。否决：点心不是发带；类别必须是显式声明，不能按名字编产权。
  当前没有声明就如实空，并把「无相关已购」写成能力边界。
- **默认 GET 连私人卡片数量也一起给。** 最强理由：界面好排版，能显示「共几张」。
  否决：私人总数、理由与 entry id 都是未公开内容；shared=false 后继续显示或缓存
  就是绕过她的显式公开决定。默认只给 shared 卡片与伴侣建议。
- **伴侣视角用「该作品最新的那封分享」代表两个版本。** 最强理由：实现简单，看起来
  历史正文都已经投递过。否决：一封 v2 分享不能证明 v1 正文公开；必须逐精确 source
  version 检查真实投递，摘要只取该版已投递文本。
- **把有效 disclosure grant 当作已本地投递。** 最强理由：许可确实存在，省一次投递
  检查。否决：许可不等于投递事实；本单只认真实 `LOCAL_DELIVERED`。
- **为旧版并排新建「对比结果」store 或缓存正文。** 最强理由：GET 快、离线可显示。
  否决：会复制作品正文并绕过当前生命周期/来源治理；只做逐次现读的安全摘要。

## Consequences

- 收益：桌面端只靠三个 GET、两个 POST 就能显示/操作衣柜搭配、私人小列表与伴侣建议、
  两个真实版本的并排摘要；所有写只落在实例自己的两份 JSON；产权、版本、分享仍由原
  owner 拥有并受其治理。
- 代价 / 已知限制：
  - 当前 economy 目录没有适用的衣柜类别，真实「已购发带」正路径尚不存在；负控
    （假 id / 他人物 / 非 OWNED / 点心）已覆盖，这是能力边界而非待办。
  - 衣柜不产生全身物理或自动换装，也不改 somatic 外衣穿挂。
  - 伴侣旧版披露严格受精确版本本地投递约束；更新版本的分享不会扩大更早版本的可见性。
  - 对比在伴侣视角只能证明「已投递分享正文是否变化」，不能证明来源全文是否变化。
- 验证：新专项 24 例，纯投影（产权核对/默认扣留/状态/上限/摘要边界/冻结 SHA）+
  真实 Supply/economy/购买/作品/分享/归档 loopback HTTP，`24 tests / 17.338s / OK /
  exit0`（最终一次）。父级仍需独立同命令复跑一次。3858 新根夹具真实 HTTP 冒烟：默认衣柜
  `NO_OWNED_RELEVANT_ITEMS`、本人 set/GET、点心充发带 409、私人列表默认不可见/公开后
  可见、伴侣建议、本人 v1/v3 摘要、伴侣 v1/v2 已投递摘要、v3 `VERSION_NOT_DELIVERED`、
  归档 `ARCHIVED_SOURCE`。根目录保留：`/tmp/nyairo-wardrobe-pocket-29853-1794392920627106555`；
  cap 文件 `.../run/phone-capability.json`；版本 refs 见夹具 stdout，不在日志打印 token。
  两个冻结文件（`autonomous_life_assembly.py`、`life_sharing_service.py`）SHA-256 与本单
  baseline 相同；未改设计目录、旧 root、World/Supply/Life 业务协议；产品调用新增 0
  （573/1000，余 427 不变）；无 commit/push/reset/stash。
- 父级限定验收：仅同新专项一次24例13.999s OK/exit0；3858全新root真实HTTP衣柜本人set/重放、点心冒发带拒绝、歌单resting保留理由/默认私密/公开撤回、伴侣建议通过。本人v1/v3读owner摘要，伴侣v1/v2仅已投递文本摘要，v3未寄/归档源扣留、跨作版本409；六条空值额外字段400且状态不变、三读口错cap403。两冻结字节不变，服务已停新根保留，设计只读、产品0新增/573余427、无旧root/commit/push。短结果 `.agents/worker-runs/20261111-wardrobe-pocket-parent-result.md`。真实owner零写限定无resident长tick观察窗口，不冒自然认知链。
- 未验证：桌面端实际渲染、真实已购衣柜类别正向；本单外旧套件/大demo未跑。
