# Agent Note: 窗边景致、冰箱贴与自选锁屏、分日慢写的信

Status: implemented

## Problem

设计 15 §2.2/2.3/2.6/2.8 要的都不是新 store：房间可以住熟（窗向/窗景/院子座位是她自己
决定过的真实 detail），物件有单一权威（植物/灯光只在世界包与 room environment 里），
相册可以真的选一张当锁屏，冰箱贴只能是已经本地投递的分享，信是跨日慢慢写出来的作品/
分享而不是一个新的信件表。现有能力各自都在，但手机端没有把它们收成用户能看见的小片；
如果桌面自己拼，很容易把「没有气象权威」显示成天气、把模板 ambient 声景当成真实雨声、
从标题里「信」字猜身份，或者把投递后的新版本算进已投递的信。

## Decision

1. **新增薄投影** `components/phone/window_keepsake_view.py`，`PhoneService` 只取既有
   owner 的只读事实并调用它；World / Supply / `PhoneAlbum` / `LifeSharingService` /
   `PrivateProjectService` 的契约都不改。它与
   [[desk-presence-and-unsent-notes]]、[[v04-private-album-import]]、
   [[v04-life-sharing-service-local-inbox]]、[[revocable-disclosure-authority]]、
   [[world-ambient-event-expansion]] 是同一条「只投影既有 owner 事实」链上的相邻面。
2. **窗边景致**：植物只取 `PhoneWorld.snapshot()` 当前区域中世界包真实声明的
   `kind=plant` 对象（object_id / 名称 / area_id / 摆位来源）；生长状态没有权威，
   `growth.value=null` + `NO_PLANT_GROWTH_AUTHORITY`。灯光沿用
   `world_room_environment` 的声明灯+已记录开关，不按钟点推白天。窗向/窗景/院子座位读
   `world_home_space.observe_home` 里她自己决定过的 detail；未决定就是 `UNSET` +
   `UNDECIDED_BY_SELF`。`weather` 没有气象权威时恒为 `null` +
   `NO_WEATHER_AUTHORITY`；ambient 只是当天 home 的模板声景，明确
   `template_projection=true` / `is_weather=false`。
3. **锁屏与冰箱贴只存引用**：新增实例本地 `phone/window_keepsakes.json`（原子写、0600），
   只记 `album_artifact_id` / `share_id` + 选择时间 + revision。锁屏选择前先走 owner
   `read_album_image`，再走 `read_album_image_bytes` 校验媒体现存与大小/SHA-256，字节读完
   即丢；GET 每次重读 owner，失效/被治理就扣留并具名。冰箱贴只允许真实
   `LOCAL_DELIVERED` 且本地回执/受众/频道/source 当前可读；未投递或源被治理具名拒绝、
   零写。相同 select/pin/unpin 重放返回 `already_applied`，revision 与时间不变。
4. **信不建 store**：`GET /api/phone/home/letters` 把既有分享的 `source_artifact_id` +
   精确 `source_version_id` 接到 `PrivateProjectService` 的多版本时间线，只统计到该
   pinned version 为止（`versions_after_cutoff_excluded` 具名），跨 >=2 个
   owner 时间戳本地日才列出。`date_basis=owner_timestamp_localized`、
   `historical_business_day_provable=false`：owner wall-clock 不等于压缩业务钟的历史
   业务日，没既有邮戳就不冒充；`writing_days` 与 `elapsed_days_inclusive` 分开。
   未发 DRAFT/DEFERRED 默认只给安全元数据；有效 capability 才给有界分享正文。
5. **窄接线**：`server.py` 加三读一写，复用现有 Host/Origin/Fetch-Site、CSRF 与本次
   serve 的 capability；`POST /api/phone/home/keepsakes` 先 CSRF、再 capability；
   `X-Phone-Actor` 声明不是身份。错误凭据 403，不降级成默认成功。

## Alternatives considered

- **新建一个「信件」artifact/store，投递时复制来源正文。** 最强理由：信件有独立类型，
  直接落库后读取最简单，桌面端不用理解版本时间线。否决：这会造第二个真相源，还会让
  投递后的私密修改和已投递文本分叉；设计要的是同一件作品跨日写成的投影，不是复制。
- **锁屏选择时把图片字节或缩略图存进手机自己的库。** 最强理由：GET 快、离线也能显示，
  不必每次打 owner。否决：相册正文与生命周期属于 `PhoneAlbum`/Supply；复制会把治理
  （归档、tombstone）绕过去，也违反「物件单一权威」。只存引用，GET 重读。
- **冰箱贴默认连 caption/标题一起显示，看起来更「回忆墙」。** 最强理由：冰箱贴本来就
  是给人看的。否决：未投递正文/标题是私人内容；默认只显示已投递分享自己的有界正文，
  锁屏默认连 caption 与 artifact id 都不给。
- **把 owner 的 `created_at` 直接当压缩业务日，或按当前 fastday checkpoint 倒推历史
  逻辑日。** 最强理由：这样跨日判断和测试最省事，能直接说「第几天写的」。否决：owner
  wall-clock 与业务钟逻辑日不是一回事，倒推会把不存在的历史业务邮戳说成事实；只做
  `owner_timestamp_localized` 并明确不可证明。
- **用标题含「信」或人工挑选的作品当信件身份。** 最强理由：实现最短，产品语义直观。
  否决：身份必须由真实 share→source version 与版本时间线证明，标题只是文本。
- **为 GET 顺手 ensure/init `home_personal_space.json` 或 ambient 状态。** 最强理由：
  第一次读就有完整形状。否决：GET 必须纯读；缺状态就具名 `UNSET`/`FEATURE_DISABLED`，
  读不到不写文件。
- **给 `POST /home/keepsakes` 加通用 operation ledger / UNKNOWN 重放。** 最强理由：
  统一幂等与对账。否决：这里只有「选哪张/贴哪条/撤哪条」三个真实意图，按 ref+action
  幂等即可；新账本会成为又一份要维护的真相。

## Consequences

- 收益：桌面端能只靠三个 GET 渲染窗边（真实植物/灯光/窗向/院子座位/模板声景）、
  锁屏与冰箱贴（默认不泄引用与 caption）、以及跨日信件投影；缺权威时如实显示 unknown
  而不编天气/生长；所有写只落在实例自己的选择 JSON，图片与正文仍在原 owner。
- 代价 / 已知限制：
  - 没有气象权威，`weather` 永远是 `null`；这是能力边界，不是 bug。
  - 植物没有生长权威，只显示世界包声明的对象身份/摆位；要显示生长需另立 owner 事实。
  - 字母投影逐条分享现读 source history，条目受既有 `MAX_SHARES` 与
    `MAX_HISTORY` 约束；分页截断会标 `bounded/partial`，不假完整总数。
  - 历史业务日不可证明：`postmark` 明确 `date_basis=owner_timestamp_localized` 与
    `historical_business_day_provable=false`；若以后要业务日邮戳，需要 owner 侧新事实。
  - 预览夹具注入 World 自己的 `world_clock` 选一个轮转模板真正包含 home 声景的
    本地日；这是既有 owner 时钟注入，不是手造事件。分日作品用 Supply owner 自己的
    `clock` 做两个真实 commit，再提交第三个版本验证截止；没有写 DB 或手填版本历史。
  - 本单施工自验：仅运行本轮 `tests.test_window_keepsake_view` 一次，22 例 / 8.037s /
    OK；真实 owner loopback HTTP 覆盖三读一写、CSRF/capability/伪 actor、假锁屏与未投递
    pin 拒绝、同意图不增 revision、+08 午夜边界、source version 截止与重复 GET 零写。
    父级随后独立同专项一次：22例 / 7.608s / OK / exit0；3854新root真实HTTP三GET与锁屏选择/贴撤POST通过，重放revision保持、未发pin409、凭据/CSRF负控403；默认锁屏引用/caption及未发正文扣留，本人凭据可读。冻结文件与施工前基线相同，产品0新增/573余427，服务已停止、scratchroot保留。邮戳只证owner时间戳本地化，不冒历史压缩业务日；真实HTTP用例7条，沿desk构造的admission reader仍是明确测试来源。父级短结果见 `.agents/worker-runs/20261111-window-keepsake-parent-result.md`。
