# Agent Note: 创作托盘、小物履历与三样喜欢之物（轻量第三弹）

Status: implemented

## Problem

设计 15 §2.2/2.3/2.6/2.7/2.8 要的不是新 store，而是三个「只投影既有 owner 事实」的小片：
一个能放下「几件想一起做的东西」的托盘、一份能说清「这件小物从哪来、现在在哪」的履历、
一个能同时看见她选的三样和伴侣写的三张短卡的匣子。既有能力都在，但手机端没有把它们收成
用户能看见、能操作的小片；如果桌面自己拼，很容易把客户端传来的版本号当事实、把「买了」
说成「送到了」或「摆好了」、把场景声明的物件说成她的私产、把伴侣卡说成她看过/相信/记住，
或者把 GET 做成顺手 tick/materialize 的写操作。

## Decision

1. **新增薄投影** `components/phone/workbench_curio_view.py`。它只拥有两份显式引用 JSON
   （`phone/workbench_tray.json`、`phone/trio_exchange.json`）和纯投影/状态函数；
   `PhoneService` 负责向既有 owner 现读并调用它。`PhoneAlbum` / `ResearchWorkspaceService` /
   `PrivateProjectService` / `LifeSharingService` / World economy 与 substrate 的契约都不改。
   它与 [[2026-11-11-window-keepsake-and-slow-letters]]、[[2026-11-03-v04-research-workspace-phone]]、
   [[2026-10-27-world-outing-store-and-flowerbed]] 是同一条「只投影既有 owner 事实」链上的相邻面。
2. **托盘只存引用与放入时基准**：最多 2 张真实相册图、2 份正式资料、1 件私人作品，备注 ≤120 字。
   `set_tray` 整体替换，`clear_tray` 清空。基准版本由服务向真实 owner 读取：资料取当前
   source revision，作品取当前 head version id + 序号；客户端传版本/正文/标题一律按名字拒绝。
   混合任何无效引用整单拒绝零写；同一意图重放 `already_applied`，不涨 revision/time，
   也不因作品后来更新而偷偷重记基准（移出再放才是新选择）。
3. **GET 每次重核**：默认视角只给 `has_materials`、三类件数、`updated_at`、安全状态与
   `no_obligation=true`；本人 capability 才给引用、基准版本、当前 head、可读状态和备注。
   `head_advanced` 只按版本历史/序号证明后继：相同 `false`、有真实历史项且序号更大 `true`、
   缺历史或基准不在历史 `unknown`；字符串不相等不算证据。资料换版照实 `STALE`。
4. **小物履历是纯读 join**：按稳定 `item_instance_id` 把产权 economy 的已购实例与 Body
   substrate 的 placement/手持/在途对起来。名称/取得时间/价格/交易号/购买意图来自 economy
   自己的账本；送达状态与位置分开报告；没有 placement 时 `pending_delivery` 或 `unknown` +
   具名原因，绝不回退主体当前区域。不 tick、不 materialize、不 ensure；没有剩余次数权威就是
   `null` + `NO_REMAINING_USES_AUTHORITY`，不造磨损/依恋分。
5. **交换匣**：`her_picks` 只接受真实 `LOCAL_DELIVERED` 分享，复用上一弹已有的
   `_verify_pin_share` 校验（本地回执、`local:owner`/`local_inbox`、source 当前可读），
   GET 每次现读正文，来源被治理/删除时扣留并具名；默认只给已投递分享自己的有界正文，
   不给来源全文/标题。`partner_picks` 最多 3 张 `{title≤40, description≤120}` 短卡，
   明确 `partner_contributed=true`，只存匣子，绝不调用 Memory/Belief/经历/认知 API。
6. **窄接线**：`server.py` 加三读两写；托盘与 `set_her_picks` 先 CSRF 再本次 serve 的
   capability；`set_partner_picks` 只需 CSRF，但显式携带的 cap 仍验证，错凭据 403 不降级；
   `X-Phone-Actor` 声明不是身份。GET 一律纯读。
   动作外字段按**是否存在**拒绝，不按值真假：`set_partner_picks` 带 `her_picks: []`/`null`/
   `""`、`set_her_picks` 带 `partner_picks` 同类值、`clear_tray` 带空值托盘字段，都返回
   `UNKNOWN_FIELD:<字段>` 并零写；合法 partner 无 cap + CSRF、her 带 cap 的写与清空不受影响。

## Alternatives considered

- **给托盘/匣子另建一张表或复制正文/图片。** 最强理由：GET 快、离线可显示、桌面端不必理解
  版本时间线。否决：会造第二个真相源，并让相册/资料/作品的治理（归档、tombstone）被复制绕过；
  设计要的是同一件东西的引用投影，不是第二份副本。
- **让客户端传基准 version id，服务原样保存。** 最强理由：实现最短，客户端已经知道它看到的是哪版。
  否决：任意客户端都能伪造「我放下时它就是这样」，而基准的用途正是对账；必须由服务向真实
  owner 读取，客户端只能点名引用。
- **GET 时顺手 tick/materialize，保证履历「完整」。** 最强理由：一次读取就能看到最新到货/摆放。
  否决：GET 必须纯读；tick/kill 一次还会改变 owner 时间线和别人看到的账。到货与摆放各自有
  正式写口，读面只报当前真实事实。
- **把「买了」直接说成「已送达/已摆好」，或把场景物件说成私产。** 最强理由：产品叙事更顺。
  否决：产权、送达、placement 是三个 owner 事实；合并会说谎，也会让后续 ARRANGE/取用判断
  失去依据。
- **伴侣卡写入时调用 Memory/Belief 或记录「她已看过」。** 最强理由：看起来更像「共同记忆」。
  否决：卡片只是匣中显示物，是否感知/相信/记住没有 owner 事实；调用认知 API 会把展示冒充成
  她的心理状态。
- **把来源已治理的分享正文缓存下来继续显示。** 最强理由：已经投递过，之前能看到。否决：
  治理/删除之后继续发就是绕过 source 边界；每次现读并扣留是唯一诚实的做法。

## Consequences

- 收益：桌面端只靠五个新口就能显示/操作托盘、小物履历与三样喜欢之物；所有写只落在实例自己的
  两份引用 JSON；图片、正文、版本、交易、位置仍由原 owner 拥有并受其治理。
- 代价 / 已知限制：
  - 托盘的作品基准只在放入时记录；作品后来更新是 `head_advanced` 的事，不会自动改基准。
  - 小物没有「剩余次数」权威，字段恒为 null；要显示次数需另立 owner 事实。
  - 来源治理的 HTTP 用例用 owner 自己的 `tombstone` 动词在一次性 scratch 根里证明扣留；
    归档按既有设计仍可读，所以不用归档冒充「不可读」。
  - 预览夹具每次在新 scratch root 构造真实相册/资料/作品/订单/分享；不碰旧 root，不联网，
    不生成图片，不调用模型。
- 不变量：无新表/新库/新 Memory；无迁移、无物理清理；治理负控对一次性 scratch 新建 source 执行 owner tombstone（逻辑删除），不声称从未删除；GET 零写；写必须 CSRF（her 选择另需 cap）；
  冻结的 `autonomous_life_assembly.py` 与 `life_sharing_service.py` 一字未动；
  `APP_ORDER` / `HOME_ORDER` 未动。
- 本单施工自验：仅运行 `tests.test_workbench_curio_view` 一次，21 例 / 14.051s / OK / exit0；
  真实 owner loopback HTTP 覆盖托盘有效链/非法/超数/伪图/伪资料/伪作品/cap/CSRF/伪 actor/
  幂等/作品推进 `head_advanced` 不重置基准/资料换版 STALE/默认隐私、小物真实购买与位置、
  her 已投递/未投递/来源被治理、partner 无 cap 写与清空/越界字段/3 项上限、重复 GET 零写。
  父级首轮21例15.017s绿后，真实HTTP发现空her_picks夹带被静默忽略，未收口；定点改为字段存在性校验后，父级同专项一次22例8.279s OK。3855两个全新根先验证两图两资料一作品/真实购买与位置/两侧匣子读写，再验证[]、null、空串9个夹带请求全部400且状态不变，合法两侧写清空仍200。两冻结文件字节不变，产品新增0/573余427；两次服务停止、根保留。当前curios仅列economy已购实例，不冒所有inventory或场景家具；零写证据限定无resident长tick观察窗口。父级短结果 `.agents/worker-runs/20261111-workbench-curio-parent-result.md`。未验证：桌面端实际渲染；本单外旧套件/大demo未跑。
