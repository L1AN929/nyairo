# 世界内容、居民存档与兼容 · v0.4

UTF-8 JSON。应用 v0.4 对应下面的 v3 格式；真实历史是 v0.2 的 v1、v0.3 的 v2。未知字段、重复 JSON 键、重复/无效 ID、错误引用、非有限数、越界参数和不兼容接口会拒绝。

世界文件上限 8 MB，HTTP 请求上限 18 MB（包含恢复基础指纹的额外空间）。导入校验成功才替换草稿或创建试运行；恢复副本可以保留未填完的属性，不等于可运行世界。

## 世界包 v3

| 顶层字段 | 定义 |
| --- | --- |
| format | nyairo.world-package.v3 |
| requires | nyairo.editor-runtime.v3，独立编辑器测试运行时 |
| meta | id/name/author/version/description/license/cover；version 是三段数字，cover 固定 generated-floorplan |
| scene | background 六位颜色、grid 布尔、decorations 布尔 |
| rooms | 1–60 个区域，每项 id/name/description/kind/x/y/w/h/hours/visual/space |
| connections | 最多 300 条双向地点连接，每项 from/to；两个不同现有区域，不能重复 |
| objects | 最多 300 个物件，见下文 |
| initial | room_id/time/resident_name；time 是 YYYY-MM-DDTHH:MM 的虚拟日历，无时区 |
| assets | 素材 ID 为键的自包含静态图片清单，最多 32 个 |

ID 为 1–64 个字母、数字、下划线或连字符。区域与物件各自唯一；空间对象在所属区域内跨四类唯一；素材清单键必须等于资源 ID。

区域 kind 为 room/shop/outdoor。外层地图为 1000×620，最小区域宽 100、高 80，整个区域须在边界内。外层区域可重叠，不产生物理碰撞；室内空间始终是该区域自己的网格。

区域 visual 包含 theme（bedroom/bookstore/cafe/street）、floor/border/background 六位颜色、texture（none/planks/tiles）、border_style（solid/dashed/none）。background 是标题背景带；scene.background 是全画布背景。decorations 控制标题背景，预览始终隐藏编辑网格。

## 区域的 space

```json
{
  "enabled": true,
  "entry": {"x":5,"y":10},
  "walls": [{"id":"partition","x1":50,"y1":0,"x2":50,"y2":100,"color":"#8c8a7d","width":7}],
  "doors": [{"id":"door","wall_id":"partition","offset":45,"length":15,"color":"#bd976e","state":"closed","actions":["open_door","close_door","lock_door","unlock_door"]}],
  "windows": [],
  "floors": [{"id":"wood","x":0,"y":0,"w":50,"h":100,"color":"#eee7d8","texture":"planks","asset_id":null}]
}
```

每类最多 100 项。墙/地面坐标为本区域的 0–100 百分比，须为 5 的倍数，每格 5%。居民的 entry/position 使用 0–19 的整数网格坐标，格中心为百分比 `x*5+2.5, y*5+2.5`。

墙必须有长度且水平或竖直，端点从左向右/上向下排序；width 是 2–12 的视觉宽度，不扩张碰撞几何。门窗依附现有 wall_id，offset 从墙的起点计，长度至少 5，均对齐 5%；跨度不越过墙、同墙门窗不重叠。门 state 为 open/closed/locked，actions 是四种门动作的子集。窗无通行动作。

地面 x/y/w/h 为对齐 5% 的矩形，宽高至少 5，须完全在区域内；color 六位颜色，texture 为 none/planks/tiles，asset_id 为 null 或存在的素材 ID。颜色/纹理/图片不产生碰撞。

运行时四邻接 BFS，只在区域自己的 20×20 网格搜索。相邻格跨过墙时，仅落在开启门跨度内才放行；窗、关门和锁门都阻挡。墙未封闭的端点可以绕行。初始居民在 entry，区域旅行到目标 entry；离开已启用空间的区域前，须能到达本区 entry。

## 物件与图片

物件保留 v2 字段 id/name/description/kind/room_id/x/y/actions/portable/capacity/servings/visual/behavior，新增 asset_id、initial_status、interaction。

物件初始坐标是区域百分比 x=5–95、y=15–90，整数；室内运行时向下取所在网格并钳制 0–19。当前物件位置另存于状态，放下会使用居民实际位置，持有物随居民，收纳物位于柜子。

visual 为 scale（40–200%）、rotation（0–359°）、layer（-100–100）、show_name（布尔）。层级控制物件之间绘制顺序，不是物理几何。asset_id 为 null 使用内置图，否则引用图片；图像与物件状态分开。

initial_status：cup 是 empty/water，且 empty 当且仅当 servings=0；dispenser 是 available/unavailable；lamp 是 off/on；其他种类是 neutral。interaction 为 `{near:布尔,radius:1–8整数}`。要求接近且空间启用时，未持有物件按最短可达路径的步数检查；墙后的直线近距离仍失败。旧世界 near=false。

assets 每项严格包含：id/name/category/collection/mime/data/width/height/kind/visual/interactive。名称和分类 1–80 字符，集合可空、最多 80；kind 为九类物件之一，visual 与物件视觉同范围，interactive 为布尔。

mime 仅 image/png、image/webp、image/svg+xml，data 为原始图片的严格 Base64，不是文件路径。单图 512 KiB，宽高 1–2048，清单合计原始字节最多 3 MiB。PNG 校验签名/块/CRC/结束，拒绝动画；WebP 校验 RIFF/静态图块/尺寸，拒绝动画。素材面板还要求浏览器实际解码成功。

SVG 使用 XML 静态元素和展示属性白名单：svg/g/基础形状/path/text/defs/渐变等；不允许脚本、事件、外部引用、实体、style、image、foreignObject、动画。仅允许受控 `url(#内部渐变ID)` 引用，最多 2000 节点。SVG 通过图片数据 URI 显示，不作为内联网页执行。支持边界见 KNOWN_ISSUES。

本地草稿保存完整素材库；世界导出收集物件 asset_id 和地面 asset_id，仅保留被引用资源。一个资源可被多个实例共享。新放置实例采用素材的默认视觉/类型/交互开关；之后修改默认属性只影响以后放置的物件。没有独立集合包格式。

## 有限行为与真实结果

actions 是对应类型支持用途的子集；behavior 始终保留该类型完整参数，即便用途被关闭。参数均为整数。

| 类型 | 用途 | behavior 参数 |
| --- | --- | --- |
| chair | sit | {} |
| bed | rest | minutes 1–720、energy_gain 0–100 |
| book | pick_up/place/read | read: minutes 1–240、energy_cost 0–100 |
| cup | pick_up/place/drink | drink: hydration_gain 0–100 |
| cabinet | store/retrieve | {} |
| table/plant | 无 | {} |
| dispenser | fill/toggle_device | fill: minutes 1–60、servings 1–20；toggle_device: minutes 1–60 |
| lamp | toggle | toggle: minutes 1–60 |

capacity、初始 servings 为 0–20；启用柜子储物需 capacity 至少 1；拿起/放下需 portable=true。前端从 bootstrap 读取条件、结果、参数和范围。

装水要求同区域、用途启用、区域开放、接近条件满足、设备 available、居民持有 cup 且 empty/0 份。成功在同一动作中改为 water/配置份数、设备 use_count 加一、推进配置分钟；身体水分不变。已有水不能叠加装入。饮用要求持有且有份数，减一份、水分增加配置值、推进 1 分钟；份数归零时同时变为空杯。

设备和灯切换实际状态、use_count 加一、推进配置分钟。门动作要求空间开启、所在区域开放、允许该用途、到门本侧两格内的可达路径；每次 1 分钟。open_door 要 closed；close_door 要 open；lock_door 要 closed；unlock_door 要 locked，结果是 closed。

walk 要空间启用、未占用家具、网格有效且可达；已在目的格会拒绝。每走一格 1 分钟，精力减 ceil(步数/10)，记录实际路径与终点。区域 move 保留原双向连接、目标开放、未占用家具检查，5 分钟/精力减 2；持有物跟随。

阅读须持有书，计时、次数和精力用配置；休息占用床、姿态 lying，计时和精力用配置；坐下占用椅子，站起释放；柜子收纳/取出检查容量和持有唯一性。其他即时动作 1 分钟，等待 minutes 1–1440。身体值限制 0–100。

hours 为 null 或 `{open:0–23,close:0–23}`，两者不同。开放端包含、关闭端不包含，支持 22–6 跨夜。使用动作开始时检查开放，长动作可越过关闭时刻；关闭允许离开，禁止进入及使用物品。规则预览只检查时间，不保证路径可达。

## 居民存档 v3

顶层严格为 format/package/package_hash/state，format 为 nyairo.editor-save.v3，package 必须是 v3。摘要为规范化 UTF-8 JSON（键排序、无额外空格、不转义中文）的 SHA-256，用于检测损坏，不是身份签名。

state 保留 revision/minutes/resident/objects/events，新增 doors 和 path。resident 保留 name/room_id/pose/energy/hydration/held/using，加 position `{x,y}`；objects 的每项保留 room_id/placement/container_id/read_count/use_count/servings，加 status 和 position。doors 按区域 ID 映射门 ID→open/closed/locked。path 是 `{room_id,points:[{x,y},...]}`，记录最后实际行走路径；路径相邻、终点等于居民，最多 400 点。随后关门不会抹去历史路径。

所有物件、门和容器引用须与冻结包一致；杯子状态/份数、持有/收纳位置、唯一持有、容量、姿态/占用、计数、事件版本与时间均校验。事件保留最近 200 条，不是无限日记。成功恢复创建新的试运行 ID。

## 真实 v1 / v2 升级

| 来源 | 实际格式 | 升级处理 |
| --- | --- | --- |
| v0.2 世界 | world-package.v1 + editor-runtime.v1 | 原结构先校验，补 v2 视觉/原默认行为，再补 v3 |
| v0.3 世界 | world-package.v2 + editor-runtime.v2 | 保留已有视觉/参数，再补 v3 |
| v0.2 存档 | editor-save.v1 + v1 世界 | 原 v1 摘要和状态先校验，再升级 |
| v0.3 存档 | editor-save.v2 + v2 世界 | 原 v2 摘要和状态先校验，再升级 |

v1 的默认阅读 15 分钟/精力消耗 3，休息 30 分钟/精力恢复 15，饮品水分 20；默认尺寸 100、朝向/层级 0、显示名称，默认主题按区域类型补齐。v2 保留作者配置。

v3 补入空 assets、关闭的空 space、entry (5,10)、asset_id=null、interaction near=false/radius=2；杯子状态按原剩余份数推导。旧存档保留时间、身体、持有/使用、收纳、计数、事件和 revision，补入口坐标和状态，不臆造旧室内路径。原作者 meta.version 不改成应用版本。

所有重新导出均 v3，无降级。旧编辑器拒绝 v3，不误报兼容。存档与包必须对应版本，混合版本或错误摘要拒绝。

## SQLite、恢复与动作回执

继续使用原 draft/trial/receipt/editor_meta/recovery 表与事务。启动迁移旧 draft/trial 包，draft revision 加一次，旧试运行 ID、经历与 receipt 保留。迁移不是重新创建居民，新增状态按上述规则补入。升级前必须停止旧服务并备份 data；回退恢复备份。

恢复副本允许未完成字段；页面先做可渲染结构筛选，正式保存和执行仍完整校验。每页独立 client_id 和递增 sequence，清除也写更高序号，迟到写不复活。base_revision/base_fingerprint 与正式草稿不符时不能覆盖，只能导出或回到已保存草稿。旧格式恢复副本可升级编辑，但旧基础通常会触发冲突保护。

动作提交示例：

```json
{"execution_id":"unique-action-id","revision":0,"command":{"action":"walk","x":8,"y":10}}
```

门的 target 是 `区域ID:门ID`；物件 target 是物件 ID。同执行 ID 的同请求仅返回原事件回执和当前状态，不重复消耗、不回退；不同请求复用 ID、旧版本均 409。业务失败不改变时间、状态或事件。状态、事件、回执在一个 SQLite 事务中提交。

导出写入 data/exports，返回实际路径与下载链接，同时生成世界初始场景 SVG 封面。下载入口进程内保留最近 20 项，磁盘文件不受此限制。内容、居民经历、用户生产个人存档保持分离。
