# C 实际加载的世界与供给边界（只读）

父级已读worker安全脚本并独立运行约定入口，实际exit0。结论：**C用的是coastal_town_929711，不是扩充过的daily_neighborhood_v1街区包。** 这能限定本批内容与呈现边界，不能证明「除了自循环没有事可做」，更不能反推不出门的动机。[C行为结果](v06-world-natural-c-result-20261102.md)保持：坐下、四作品派生、MOVE0、20请求到额结束。

## 实际资源和声明

本C保留的`one-day/instance.json`指向复制到本根的`one-day/world-package`。包ID `coastal_town_929711`，时区Asia/Tokyo；17地点及连接与旧[d调查](d-move-supply-20261031.md)同集。包内ambient为`worldgen.ambient.v1`，6条单日模板，类型是shrine_sweeping、harbor_fish_unload、store_evening_sale、home_window_sound、street_construction、beach_tide_low。它不是内建24，也不是daily_neighborhood_v1的24；所以不能把[旧目录](v04-world-events-catalog-20261025.md)里11月1日结束的有限跨天事件解释成本C11月2日没事件的原因。

objects/scene声明仅覆盖home（10物件、3区）和cafe（12物件、2区）；对象能力表22项，仅cafe_cup04标为takeable/portable，consumable_catalog一项。另15地点没有这两种资源里的物件/室内场景声明，但均有content条目及rules行为声明。这不是全owner能力的穷尽证明，也不能说那些地点只有名字/绝对无事可做。

重点三地：street_1声明WALK/LOOK_AROUND，连接书店/咖啡店等；cafe声明SIT/READ，9–21当地时营业；bookstore声明BROWSE_STORE，10–20营业且无objects物件。上述是包声明，不是本轮实际可执行owner动作列表。C没有到场，不能改位置或拿另一包演示冒本C正式可用。

## ambient的已知与未知：纠正worker报告

父级复验输出`one_day_life_env_has_key=true`，与worker文字「features/env无该键」矛盾；父级额外定点只读`one-day/instance.json.environment.CHIYO_WORLD_AMBIENT_EVENTS_ENABLED`，实际为字符串`0`。因此**已知本实例持久配置关闭ambient**，不能称没有配置证据。

没有保留完整的运行时子env快照，实验入口还经过child env映射；本次不重新启动、不读取旧进程环境，不据静态token零命中或当前shell默认宣称当时World子进程一定off。现树one_day_life里显式0支持配置来源，不替代现场加载同一性证明。最终请求trace没有独立ambient字段，候选来源也没有ambient记录；事实列表0/日志零命中不等所有背景材料无事件。故历史实际开关/home环境事件呈现仍UNKNOWN，持久配置值0单独列明。

## 与Artifact比较，不混口径

C现存Supply为5件Artifact/9版本（含准备期一件），写操作记录CREATE5/COMMIT_MARKDOWN9；这不是14次模型内容请求，也不等读/对比工具调用数。真实四件由START各写到v2，CONTINUE合法no_change，见C报告。

World侧12次最终请求各有5个home候选，MOVE到street_1均在场、5次START合法。street_1是已供给的移动目标，所以不能笼统说「street供给0」；**街上/咖啡店/书店的到场活动候选**没有在本批被观察到，非当前位置API不支持查询就UNKNOWN，不当作空集合。这与Artifact owner已经执行的写操作是不同层，不能做能力数量比分。

本轮没有完成全17地点的正式owner动作能力图，也没有历史home完整prompt/背景材料证据。可证明资源厚度不均、C没有装扩充街区包、配置ambient=0；不能证明MOVE「没有下一步」、没有其他合法事情、街有内容未选即动机/呈现故障。

## 处理决定

不改世界内容、模板日期、ambient默认、候选/静默/Memory规则，不重跑C或新开真实批。本轮是范围澄清，不是生产缺陷已定位；不能拿对另一个世界包的交付声明替代C的实际加载。所有数据保留，212/300余88不变、无额外产品请求；未跑测试、服务、移动或生产import。worker报告的不准之处在这里明确纠正，不回写历史实例。需要新内容或改默认时仍按原产品停止门，不把通用协调授权解释成自动批准。

本地证据在ignored `20261102-v06-c-world-supply-safe.py/json`，父级固定入口同worker路径复跑会刷新该ignored JSON；它不是只写stdout，未写实例。JSON中部分note/标志是提取器固定描述，不作为实测断言。额外父级核查只读取上述一个非秘密布尔配置字段。未提交/部署/改变系统。
