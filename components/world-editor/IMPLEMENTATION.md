# v0.4 施工核查与实现位置

施工前读取并核对 v0.3 源码、README、FORMAT、INTEGRATION、TEST_REPORT、KNOWN_ISSUES；从该完整发行源码复制新目录继续升级，保留 v0.3 目录与 ZIP。

| v0.3 已有能力 | 原实际位置 | 本轮扩展 |
| --- | --- | --- |
| 区域、地点、双向连接与开放时间 | model.py、web/app.js | 原结构保留，区域另加 space |
| 物件百分比坐标、尺寸、旋转、层级、批量布局 | web/layout.mjs、app.js、原模型 | 原布局系统保留，空间使用单独模式/局部网格 |
| 四种主题、七种 SVG、封面 | assets.py、模型 cover_svg | 增设备/灯，静态自定义图与地面资源 |
| 阅读/坐卧/饮品/持有/收纳真实模板 | 原 model.py apply_action | 原语义保留于 legacy.py，扩展有限状态与装水链 |
| 编辑历史、恢复、冲突保护 | editor-state.mjs、Store、bootstrap | 复用历史和序号镜像；扩展新字段结构和图片预算 |
| SQLite、版本和动作回执 | store.py | 原事务/表和回执流程保留，迁移旧状态补默认字段 |
| 世界/居民存档导入导出 | server.py、模型 | 真实 v1/v2 兼容升级，导出 v3 和使用中的图像 |
| 居民区域移动、持有、占用和储物 | 原运行时、trial UI | 增室内位置、四邻接路径、门通行和近距离条件 |

原 v0.3 的限制是无室内寻路/墙门、无用户图片、无可补充的空杯状态。新增空间不替换地点图；20×20 正交网格、受控静态素材、有限模板是本版边界。

阶段 A：space.py 与 web/space.mjs 定义墙/依附门窗/地面；画布工具、属性、复制/删除、原撤销恢复共用。

阶段 B：space.path 四邻接 BFS；model.apply_action 处理 walk 和门开关/锁定。门关/锁阻挡，窗不通行；区域旅行检查可到本区入口。

阶段 C：resources.py 校验图片，/api/assets/import 导入，素材库点击/拖入/搜索/分类；export_package 裁掉未使用资源，数据内嵌跨实例。

阶段 D：model 的 fill、toggle_device、toggle 与杯空满状态；继续由 Store.execute 原事务提交，复用幂等与版本。组合为多个真实手动动作，无前端成功伪造。

阶段 E：原 app.js 增空间/物件/素材/规则分类，按选中对象显示属性，真实路径/状态/失败反馈，桌面与手机检查。

阶段 F：原回归 + 新语义测试、从空白 UI 咖啡馆、第二空实例、旧世界/存档、关闭浏览器并重启、双窗口冲突、最终 ZIP 启动。具体证据见 TEST_REPORT。
