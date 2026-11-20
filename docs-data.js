const DOCS_TREE = [
  {
    "category": "起步与安装",
    "items": [
      {
        "id": "intro",
        "title": "项目介绍与核心能力"
      },
      {
        "id": "changelog",
        "title": "版本动态与更新说明",
        "badge": "NEW"
      },
      {
        "id": "quickstart",
        "title": "第一次使用，从这里开始"
      },
      {
        "id": "installation",
        "title": "安装前准备与系统要求"
      },
      {
        "id": "windows",
        "title": "在 Windows 上安装",
        "badge": "桌面端"
      },
      {
        "id": "linux",
        "title": "Linux 与手动安装"
      },
      {
        "id": "agent-install",
        "title": "交给 AI 助手自动安装"
      },
      {
        "id": "configuration",
        "title": "日常启动、人设与模型"
      },
      {
        "id": "weixin",
        "title": "接入微信",
        "badge": "推荐"
      },
      {
        "id": "telegram",
        "title": "接入 Telegram 与 QQ"
      }
    ]
  },
  {
    "category": "功能与日常使用",
    "items": [
      {
        "id": "desktop-guide",
        "title": "桌面客户端使用指南",
        "badge": "桌面端"
      },
      {
        "id": "daily-companions",
        "title": "共读、文稿、相册与节律"
      },
      {
        "id": "world-editor",
        "title": "地图编辑器与世界包"
      },
      {
        "id": "cli-reference",
        "title": "常用命令与记忆管理"
      },
      {
        "id": "modules",
        "title": "独立推演与底层服务"
      }
    ]
  },
  {
    "category": "维护与规划",
    "items": [
      {
        "id": "update",
        "title": "版本升级与一键回滚"
      },
      {
        "id": "data",
        "title": "数据目录、备份与卸载"
      },
      {
        "id": "troubleshooting",
        "title": "常见问题排查"
      },
      {
        "id": "status-matrix",
        "title": "已完成与未完成清单"
      },
      {
        "id": "roadmap",
        "title": "未来规划：初遇引导与四季",
        "badge": "规划"
      }
    ]
  },
  {
    "category": "隐私与开源",
    "items": [
      {
        "id": "privacy",
        "title": "隐私与数据边界"
      },
      {
        "id": "testing",
        "title": "测试范围与发行说明"
      },
      {
        "id": "release",
        "title": "开源许可与站点维护"
      },
      {
        "id": "contributing",
        "title": "问题反馈与参与贡献"
      },
      {
        "id": "acknowledgments",
        "title": "致谢"
      }
    ]
  }
];

const DOCS_CONTENT = {
  "intro": {
    "title": "项目介绍与核心能力",
    "summary": "认识 nyairo：给数字个体一方住得下来的空间、存在本机的长期记忆，以及按真实时间流动的日常。",
    "toc": [
      {
        "id": "intro-section-0",
        "text": "为什么要做 nyairo？"
      },
      {
        "id": "intro-section-1",
        "text": "现在能做什么？"
      },
      {
        "id": "intro-section-2",
        "text": "开始前需要准备什么？"
      }
    ],
    "content": "<div class=\"callout callout-info\"><div class=\"callout-title\">框架与人设说明</div><p>nyairo 是一个开源的数字个体框架（Apache-2.0）。新装默认提供干净的中性底板，不预设固定角色身份——你可以自己给 TA 起名字、定语气、改房间，慢慢养出属于你们的日常。</p></div>\n\n<h2 id=\"intro-section-0\">为什么要做 nyairo？</h2>\n<p>平时用大多数 AI 聊天工具，只要关掉窗口，对方的时间就彻底停了；下次再打开，又要从零开始客套。说去哪里也是一秒钟瞬移过去，聊完就忘，很难让人觉得对面住着一个活生生的伙伴。</p>\n<p><strong>nyairo 想解决的就是这件事：</strong>让 TA 在你关掉窗口之后依然有自己的时间流速，有具体的房间和街道可以走动，聊过的往事稳稳存在你自己的电脑里。你们的每一次对话，都接在上一次分别之后的生活里。</p>\n\n<h2 id=\"intro-section-1\">现在能做什么？</h2>\n<div class=\"table-scroll\">\n<table class=\"doc-table\">\n<thead>\n<tr>\n<th scope=\"col\" style=\"width: 22%;\">模块</th>\n<th scope=\"col\" style=\"width: 50%;\">实际作用</th>\n<th scope=\"col\" style=\"width: 28%;\">当前状态</th>\n</tr>\n</thead>\n<tbody>\n<tr>\n<td><strong>本地长期记忆与回忆灯</strong></td>\n<td>把聊过的习惯、约定和经历存进本机 SQLite 数据库，双层回忆灯机制保护对话原稿零篡改；记错了能当面纠正，不想再提的事也能设为停止召回。</td>\n<td>默认开启 · 原稿留在本机</td>\n</tr>\n<tr>\n<td><strong>连续起居与相处分寸</strong></td>\n<td>关窗或重启电脑后位置与手头进度不清零；具备去工具人化的日常自主拒绝边界（不想帮的忙干脆拒绝，不念客服式道歉模板也不记仇），按真实日程隔几小时异步回信，睡眠/外出/专注时将非急事收入手机待回匣，真急事可敲门找 TA。</td>\n<td>基础连续默认开启 · 自主活动按需开启</td>\n</tr>\n<tr>\n<td><strong>街区店铺与冷热物态</strong></td>\n<td>出门按路网距离真实计时，支持 12 类街区店铺与开放柜台自由点单（如去便利店买热关东煮），旧书店与咖啡屋店主独立错峰作息并支持吧台杯垫留言；随身包袋支持最多 3 层嵌套收纳，具备桌边热茶自然放凉、窗边纸条遇雨微潮晕字等冷热干湿物态。</td>\n<td>免选风貌全随机专属世界 + 7 套内置世界 + 深浅双色离线编辑器</td>\n</tr>\n<tr>\n<td><strong>共读、文稿与搬家旅行箱</strong></td>\n<td>保存旅途明信片与日常手记，支持双人同步阅读批注与文稿修订对比；无损搬家旅行箱 <code>.nyairo-suitcase.zip</code> 可连同世界切片与迁移损失清单整套带走。</td>\n<td>桌面端内直接可用</td>\n</tr>\n<tr>\n<td><strong>独立分析建议</strong></td>\n<td>遇到纠结的问题或代码排错时，可用命令请 TA 单独梳理利弊与思路，分析完继续回到日常聊天。</td>\n<td>按需开启（<code>/chiyo_consider</code>）</td>\n</tr>\n</tbody>\n</table>\n</div>\n\n<h2 id=\"intro-section-2\">开始前需要准备什么？</h2>\n<ul>\n<li><strong>一台普通电脑</strong>：Windows 10 / 11（提供约 5.8 MB 免管理员桌面端，配合系统内置 WSL 2 运行）或 Linux 均可。对话推理走你填写的模型接口，<strong>不需要独立显卡</strong>，日常轻薄本就能跑。</li>\n<li><strong>一个大模型 API Key（或本地免费 Ollama）</strong>：新手可在 DeepSeek 开放平台（<code>platform.deepseek.com</code>）花 5–10 元创建一串 <code>sk-...</code> 密钥，也可直连本机免费的 <code>Ollama</code>（<code>http://127.0.0.1:11434</code>）实现零云端流量对话。</li>\n<li><strong>几分钟安装时间</strong>：首次安装会自动拉取运行环境与校验包，保持网络通畅即可。</li>\n</ul>\n<p>准备好后，直接前往 <a href=\"#quickstart\">第一次使用，从这里开始</a>。</p>"
  },
  "changelog": {
    "title": "版本动态与更新说明",
    "summary": "如实记录每一版的改动、修补与当前还没做完的边界。当前最新公开测试版为 v0.7.0，旧版标签与安装包均保留在 GitHub Releases。",
    "toc": [
      {
        "id": "changelog-v0-7-0",
        "text": "v0.7.0 · TA 更像一个有分寸的伙伴了"
      },
      {
        "id": "changelog-v0-6-0",
        "text": "v0.6.0 · 日常相处回执、未来问题与共读体验"
      },
      {
        "id": "changelog-v0-5-1",
        "text": "v0.5.1 · 身体节律、街区早晚与桌面端"
      },
      {
        "id": "changelog-v0-4-0",
        "text": "v0.4.0 · 多段出行与多事挂起"
      },
      {
        "id": "changelog-v0-3-0",
        "text": "v0.3.0 · 出行、消费与连续闭环"
      },
      {
        "id": "changelog-v0-2-0-rc3",
        "text": "v0.2.0-rc3 · 聊天内移动与在途感知"
      },
      {
        "id": "changelog-v0-2-0-rc2",
        "text": "v0.2.0-rc2 · 安装与升级元数据修复"
      },
      {
        "id": "changelog-v0-2-0-rc1",
        "text": "v0.2.0-rc1 · 自主日常与地图编辑器"
      },
      {
        "id": "changelog-v0-1-0-rc9",
        "text": "v0.1.0-rc9 · Agent 辅助安装与社区入口"
      },
      {
        "id": "changelog-v0-1-0-rc6",
        "text": "v0.1.0-rc6 · 一条命令更新与回滚"
      },
      {
        "id": "changelog-v0-1-0-rc5",
        "text": "v0.1.0-rc5 · 自定义人设与隐私加固"
      },
      {
        "id": "changelog-v0-1-0-rc4",
        "text": "v0.1.0-rc4 · 首个公开体验版"
      }
    ],
    "content": "<div class=\"callout callout-info\"><div class=\"callout-title\">版本说明</div><p>当前最新发布包为 <strong>v0.7.0</strong>（GitHub prerelease 公开测试版，尚非正式稳定版），同时提供原生 Windows 桌面安装器（<code>nyairo-0.7.0-setup.exe</code>）。此前发布的 <code>v0.6.0</code>、<code>v0.5.1</code>、<code>v0.4.0</code>、<code>v0.3.0</code>、<code>v0.2.0-rc3</code> 等历史标签与压缩包均原样保留。升级前建议先备份自己的 <code>~/.nyairo</code> 目录。</p></div>\n\n<h2 id=\"changelog-v0-7-0\">v0.7.0 · 全随机世界、开放店铺、相处边界与无损旅行箱（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公开测试版 · 免选风貌随机创世、12 类店铺自由点单、自主拒绝与异步回信、包袋物态及跨设备搬家</p>\n<p>这一版把空间自由度、日常相处的呼吸感、物理物态细节与无损迁居体验做成了完整闭环：</p>\n<ul>\n<li><strong>免选风貌全随机专属世界与深浅双色离线编辑器</strong>：除 7 套内置世界与 4 种主题风格外，新增免选风貌的全随机专属世界一键生成（6 种地貌混搭、4 种异构路网、8 类商业店铺与多房间住宅），离线可视化世界编辑器同步焕新支持深浅双色工作台切换。</li>\n<li><strong>12 类街区店铺开放点单与店主错峰日常</strong>：打通 12 类街区店铺与开放柜台自由点单（例如走到便利店买一份热关东煮），旧书店与咖啡屋店主按独立错峰作息出勤，并支持在吧台留下杯垫留言。</li>\n<li><strong>去工具人化的自主拒绝边界与真实日程异步回信</strong>：不想帮的忙 TA 会干脆拒绝，不念客服式道歉模板也不记仇；支持按真实日程隔几小时异步回信，睡眠、外出或专注时将非急事收入手机待回匣，真有急事可敲门找 TA。</li>\n<li><strong>随身包袋 3 层嵌套收纳与冷热干湿物态</strong>：随身包袋与收纳盒支持最多 3 层嵌套收纳与严格容器视角隔离，物品具备真实的冷热干湿物态——桌边热茶会自然放凉，窗边纸条遇雨会微潮晕字。</li>\n<li><strong>双层回忆灯与无损搬家旅行箱</strong>：双层回忆灯严格保护对话原稿零篡改；无损搬家旅行箱 <code>.nyairo-suitcase.zip</code> 支持连同自定义/随机世界切片与迁移损失清单整套带走（自动剥离 API 密钥与本机路径，导入前双步安全预检）。</li>\n<li><strong>每日模型用量封顶与桌面端加固</strong>：新增默认每日 50,000 Token 预算上限与唤醒票对账防超支；Windows 桌面端安装引导与网关互斥保护同步升级。</li>\n</ul>\n<p>相关链接：<a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.7.0\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Release v0.7.0</a> · <a href=\"#windows\">Windows 桌面端安装</a></p>\n\n<h2 id=\"changelog-v0-6-0\">v0.6.0 · 日常相处回执、未来问题与共读体验（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公开测试版 · 日常相处与桌面端体验升级</p>\n<p>这一版重点打磨了日常聊天、街区散步、双人共读与桌面客户端的细节体验：</p>\n<ul>\n<li><strong>对话生活感知与动作回执</strong>：聊天窗口顶部实时显示 TA 当前所在的房间或街道、身体姿态与手头进度；当对话里触发了出门散步、写随笔或加书签等真实动作时，气泡下方会浮现可点击的跳转胶囊，一键跳到对应地图或文稿。</li>\n<li><strong>未来问题与习惯话头管理</strong>：桌面端「记忆 → 共处与实践」支持随时手动记下 TA 回头想问你的问题、直接回答或划掉，也能把常聊的共同默契沉淀为习惯话头，并在「总览」页一键带入聊天。</li>\n<li><strong>双人共读示范书与初次向导</strong>：共读书架支持一键放入《凪浦海风手记（共读示范）》立即体验翻页、书签与页码批注；全新世界提供起床、喝水、写随笔与逛书店的四步轻巧引导。</li>\n<li><strong>模型接口一键连通测试</strong>：桌面端「设置」页新增大模型接口直连测试按钮，当场返回响应耗时或错误原因，省去盲猜排错。</li>\n</ul>\n<p>相关链接：<a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.6.0\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Release v0.6.0</a> · <a href=\"#windows\">Windows 桌面端安装</a></p>\n\n<h2 id=\"changelog-v0-5-1\">v0.5.1 · 身体节律、街区早晚与桌面端（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公开测试版 · 分段测试与发行清单校验通过 · 同步推出 Windows 桌面端</p>\n<p>这一版把近期围绕日常起居、街区细节、随身相册文稿以及桌面客户端的工作整合成了可直接安装的测试版本：</p>\n<ul>\n<li><strong>Windows 原生桌面客户端</strong>：新增约 5 MB 的免管理员安装包（<a href=\"#windows\">安装说明</a>），常驻系统托盘，首次打开自动检测 WSL 2 并引导配齐环境，内置总览、流式引用对话、街区与室内平面、文稿对比和微信扫码面板。</li>\n<li><strong>起居与补给节律</strong>：加入困倦休息、饮水与简餐补给的时间节律，动作按真实耗时推进（目前仍是轻量节律，不是完整的饥饿/营养数值系统）。</li>\n<li><strong>街区早晚变化与可读风物</strong>：同一条街在早晚不同时段会呈现不同风貌，路边设施与阅读角支持驻足查看具体内容。</li>\n<li><strong>相册、日记与文稿续写</strong>：补齐相机留影、相册翻看、双人共读批注，以及未写完文稿的挂起续写与历史版本差异对比。</li>\n<li><strong>工程清理与脱敏</strong>：统一安装器版本号与记忆模块显式配置，对公开源码中的内部测试地址做了彻底脱敏。</li>\n</ul>\n<div class=\"callout callout-info\"><div class=\"callout-title\">v0.5.1 当期历史边界记录</div><p>已完成专项分段测试、9 项脱敏离线检查、<code>MANIFEST.json</code> 与 ZIP 哈希核对及桌面端实机联调；当轮未重跑历史四万余项宿主全量回归，也未进行 24 小时真实大模型连续挂机长测（注：当时尚未合入的全随机世界与搬家旅行箱已在后续 <code>v0.7.0</code> 落地）。</p></div>\n<p>相关链接：<a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.5.1\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Release v0.5.1</a> · <a href=\"#windows\">Windows 桌面端安装</a></p>\n\n<h2 id=\"changelog-v0-4-0\">v0.4.0 · 多段出行与多事挂起（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公开测试版 · 世界与身体专项 263 项测试通过</p>\n<p>在 0.3 打通单次出门闭环的基础上，0.4 解决了「远门分几段走」和「手头几件事穿插着做」的问题：</p>\n<ul>\n<li><strong>多段连贯行程</strong>：去较远的地方会自动拆成多段路程（例如：卧室 → 客厅 → 街道 → 书店）依次推进，每走完一段单独记录进度，中途停下也不会乱跳。</li>\n<li><strong>多件事挂起，同一时刻专心做一件</strong>：手头的事如果暂时做不完，可以记下等待条件先挂到一边去干别的；等条件满足了再接回来继续，前台始终只跑一件事。</li>\n<li><strong>跨天天气与街区见闻</strong>：阴晴变化与街坊小事会持续几天并分阶段演变，过期后自然翻篇，不再死循环触发。</li>\n<li><strong>状态回执更清晰</strong>：修复了不同活动之间动作编号偶发串用的问题；明确区分「操作被拒绝」与「结果暂时未知」，不再混成同一种报错。</li>\n<li><strong>文稿版本对比</strong>：加入受管文稿的历史版本列表与增删对比视图，续写时自动参考上一版摘要。</li>\n</ul>\n\n<h2 id=\"changelog-v0-3-0\">v0.3.0 · 出行、消费与连续闭环（GitHub prerelease）</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">公开测试版 · 8 组 51 项核心机制检查通过</p>\n<p>这一版把出门、在途等待、临时改主意、进店买东西、回家接着做原来的事，串成了可暂停、可恢复的连续过程：</p>\n<ul>\n<li><strong>按真实路网寻路</strong>：直接从当前地图解析地点与房间出入口，名字有歧义或路不通就如实告知，不再靠硬编码瞬移。</li>\n<li><strong>半路暂停不重走</strong>：暂停指令只拦住还没出发的下一段路，已经在路上的这一段会如实走完，恢复后从断点接着走。</li>\n<li><strong>网络抖动不重复执行</strong>：提交动作后如果没收到回执，会先核对账本结果，不会盲目重发导致同一件事做两遍。</li>\n<li><strong>每日模型调用预算</strong>：支持按天和按单次运行窗口设置请求上限，暂停后绝不在后台偷偷消耗额度。</li>\n<li><strong>进店消费与经历留痕</strong>：在店里买下的饮品或读物会真实消耗并记入随身记录，后续聊天与选择都能感知到刚刚发生的事；支持查询未来几天店铺的营业时间表。</li>\n</ul>\n\n<h2 id=\"changelog-v0-2-0-rc3\">v0.2.0-rc3 · 聊天内移动与在途感知</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">发布时间：2026 年 10 月 14 日</p>\n<ul>\n<li><strong>一句话让 TA 出门</strong>：新增 <code>world_move</code> 移动工具，在终端、微信或 Telegram 里直接对 TA 说去便利店或公园，不相邻的地点会自动经街道中转。</li>\n<li><strong>真实步行耗时</strong>：每段路按距离需要走 5 到 13 分钟不等；半路发消息问 TA，TA 能准确说出大概还要走几分钟才到。</li>\n<li><strong>权限放行与测试补齐</strong>：修复了记忆安全策略误拦移动工具的问题，补齐 16 项专项测试与文件清单校验。</li>\n</ul>\n\n<h2 id=\"changelog-v0-2-0-rc2\">v0.2.0-rc2 · 安装与升级元数据修复</h2>\n<p>修复了 rc1 压缩包内更新登记版本号仍停留在旧版的问题，新增打包自动核对脚本防止版本号错配，并修正了 Telegram 绑定说明中的命令笔误。</p>\n\n<h2 id=\"changelog-v0-2-0-rc1\">v0.2.0-rc1 · 自主日常与地图编辑器</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">发布时间：2026 年 10 月 13 日</p>\n<ul>\n<li><strong>后台自主节律（默认关闭）</strong>：开启后，就算没人找 TA 说话，TA 也会按节奏醒来看看周围、散步或接着写没写完的随笔。</li>\n<li><strong>结果核实后才算数</strong>：避免大模型「嘴上说写完了其实没存盘」——只有底层服务确认写入成功才记账，拿不准的状态如实标为不确定。</li>\n<li><strong>自带离线地图编辑器</strong>：随包附带可视化空间编辑器与多套城镇模板，可以在浏览器里画墙、装门、摆家具。</li>\n</ul>\n\n<h2 id=\"changelog-v0-1-0-rc9\">v0.1.0-rc9 · Agent 辅助安装与社区入口</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">发布时间：2026 年 10 月 10 日</p>\n<ul>\n<li><strong>AI Agent 辅助安装规范</strong>：上线 <a href=\"AGENT_INSTALL.zh-CN.md\">AGENT_INSTALL.zh-CN.md</a>，支持把标准指令复制给 Claude Code、Codex 等工具，自动完成环境自检、实例隔离与 SHA256 校验安装。</li>\n<li><strong>社区交流入口</strong>：开通官方 QQ 交流群（<code>1126943805</code>）与 Telegram 频道（<code>@nyairoai</code>），方便反馈问题与交流配置经验。</li>\n<li><strong>文档站样式整理</strong>：优化了官网首屏与文档阅读排版。</li>\n</ul>\n\n<h2 id=\"changelog-v0-1-0-rc6\">v0.1.0-rc6 · 一条命令更新与回滚</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">发布时间：2026 年 10 月 6 日</p>\n<ul>\n<li><strong>整包平滑升级</strong>：新增 <code>nyairo update</code>，只替换 <code>~/.local/share/nyairo</code> 下的程序目录，<code>~/.nyairo</code> 里的聊天记录、记忆、密钥与 <code>SOUL.md</code> 原样保留。</li>\n<li><strong>一键回滚与中断恢复</strong>：升级后若遇到兼容问题，可用 <code>nyairo update --rollback</code> 退回上一版（期间新聊的内容不会丢）；断网中断可用 <code>nyairo update --recover</code> 修复。</li>\n</ul>\n\n<h2 id=\"changelog-v0-1-0-rc5\">v0.1.0-rc5 · 自定义人设与隐私加固</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">发布时间：2026 年 10 月 4 日</p>\n<ul>\n<li><strong>人设模板解耦</strong>：新实例默认采用干净的中性 <code>SOUL.md</code> 模板，方便自由设定名字、称呼与性格。</li>\n<li><strong>短对话上下文修复与工具限制</strong>：修复了简短对话偶发漏记上下文的问题；启用记忆模式时默认限制任意文件读取工具，防止模型绕过「停止召回」设置去翻旧日志。</li>\n</ul>\n\n<h2 id=\"changelog-v0-1-0-rc4\">v0.1.0-rc4 · 首个公开体验版</h2>\n<p class=\"doc-date\" style=\"color: var(--muted); font-size: 0.84rem; margin-bottom: 14px;\">发布时间：2026 年 10 月 2 日</p>\n<p>搭起 nyairo 的基础骨架：本地 SQLite 长期记忆、跨重启保留的生活状态，以及最初的位置与姿态感知接口。</p>"
  },
  "quickstart": {
    "title": "第一次使用，从这里开始",
    "summary": "根据你手头的电脑选择最顺手的装法：Windows 用户直接装桌面端，新手花 5 元配好 DeepSeek 密钥或免费连本地 Ollama 就能开聊。",
    "toc": [
      {
        "id": "quickstart-section-0",
        "text": "先选适合你的安装方式"
      },
      {
        "id": "quickstart-smartscreen",
        "text": "Windows 首次运行提示「已保护你的电脑」两步图解"
      },
      {
        "id": "quickstart-guided-0",
        "text": "Linux / WSL 一行命令安装"
      },
      {
        "id": "quickstart-model-beginner",
        "text": "零基础配模型：5 元 DeepSeek 密钥或免费 Ollama"
      },
      {
        "id": "quickstart-section-1",
        "text": "装好之后怎么聊天"
      },
      {
        "id": "quickstart-section-2",
        "text": "给 TA 起名字、改性格"
      }
    ],
    "content": "<h2 id=\"quickstart-section-0\">先选适合你的安装方式</h2>\n<p>开始前只需要准备好一台能上网的电脑，以及一个大模型接口（花 5 元配好 DeepSeek API Key，或者免费连本机 Ollama 均可）。按你的系统二选一：</p>\n<ul>\n<li><strong>如果你用 Windows 10 / 11（推荐）</strong>：直接下载约 5.8 MB 的 <a href=\"downloads/nyairo-0.7.0-setup.exe\" download=\"nyairo-0.7.0-setup.exe\"><strong>Windows 桌面端安装包</strong></a>，双击安装后会自动检测 WSL 2 并引导配好后台环境，不需要自己记终端命令。完整步骤见 <a href=\"#windows\">在 Windows 上安装</a>，界面用法见 <a href=\"#desktop-guide\">桌面客户端使用指南</a>。</li>\n<li><strong>如果你用 Linux，或习惯纯终端操作</strong>：继续看下面的一行命令安装步骤即可。</li>\n<li><strong>如果你平时用 Claude Code / Codex 等编程助手</strong>：也可以直接把 <a href=\"#agent-install\">交给 AI 助手自动安装</a> 里的标准指令复制给它代装。</li>\n</ul>\n\n<h2 id=\"quickstart-smartscreen\">Windows 首次运行提示「已保护你的电脑」两步图解</h2>\n<p>独立开源项目暂未购买每年数千元的商业 EV 代码签名证书，首次双击 <code>nyairo-0.7.0-setup.exe</code> 时 Windows SmartScreen 可能会弹出常规拦截提示。按下面两步点开即可（全程无需管理员权限）：</p>\n<ol>\n<li>先点弹窗文字下方的「<strong>更多信息</strong>」展开安装包详情；</li>\n<li>可顺手核对安装包 SHA256 与官网公布的 <code>174e459727af67c5d97204abd69dd7ce0741ed056a084fc64ba78a187bc14dd8</code> 一致，再点右下角出现的「<strong>仍要运行</strong>」即可进入安装向导。</li>\n</ol>\n<div class=\"smartscreen-guide\" style=\"margin: 16px 0 20px;\">\n<svg viewBox=\"0 0 680 232\" role=\"img\" aria-label=\"Windows SmartScreen 两步放行示意图：第一步点击更多信息，第二步核对 SHA256 后点击仍要运行\" style=\"width: 100%; max-width: 680px; height: auto; display: block; color: var(--fg);\">\n  <rect x=\"4\" y=\"6\" width=\"312\" height=\"220\" rx=\"10\" fill=\"var(--card)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <line x1=\"4\" y1=\"38\" x2=\"316\" y2=\"38\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"20\" y=\"26\" fill=\"var(--accent)\" font-size=\"11\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">STEP 01 · 点「更多信息」</text>\n  <g style=\"color: var(--muted);\" opacity=\"0.7\">\n    <line x1=\"292\" y1=\"18\" x2=\"300\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n    <line x1=\"300\" y1=\"18\" x2=\"292\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n  </g>\n  <text x=\"22\" y=\"64\" fill=\"var(--fg)\" font-size=\"14\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">Windows 已保护你的电脑</text>\n  <text x=\"22\" y=\"88\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">Microsoft Defender SmartScreen 已阻止启动</text>\n  <text x=\"22\" y=\"106\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">未识别的应用（开源构建包未购商业签名）</text>\n  <rect x=\"20\" y=\"122\" width=\"84\" height=\"26\" rx=\"6\" fill=\"var(--accent-soft)\" stroke=\"var(--accent)\" stroke-width=\"1.4\"/>\n  <text x=\"62\" y=\"139\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"12\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">更多信息</text>\n  <g style=\"color: var(--accent);\">\n    <path d=\"M138 135H112m6-5-6 5 6 5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n  </g>\n  <text x=\"144\" y=\"139\" fill=\"var(--accent)\" font-size=\"11.5\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">1. 先点这里展开</text>\n  <rect x=\"222\" y=\"182\" width=\"78\" height=\"28\" rx=\"5\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"261\" y=\"200\" text-anchor=\"middle\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">不运行</text>\n  <g style=\"color: var(--accent);\">\n    <path d=\"M325 116h28m-6-6 6 6-6 6\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n  </g>\n  <rect x=\"364\" y=\"6\" width=\"312\" height=\"220\" rx=\"10\" fill=\"var(--card)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <line x1=\"364\" y1=\"38\" x2=\"676\" y2=\"38\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"380\" y=\"26\" fill=\"var(--accent)\" font-size=\"11\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">STEP 02 · 点「仍要运行」</text>\n  <g style=\"color: var(--muted);\" opacity=\"0.7\">\n    <line x1=\"652\" y1=\"18\" x2=\"660\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n    <line x1=\"660\" y1=\"18\" x2=\"652\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n  </g>\n  <text x=\"382\" y=\"64\" fill=\"var(--fg)\" font-size=\"14\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">Windows 已保护你的电脑</text>\n  <rect x=\"382\" y=\"78\" width=\"276\" height=\"66\" rx=\"6\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"394\" y=\"98\" fill=\"var(--fg-2)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">应用: nyairo-0.7.0-setup.exe</text>\n  <text x=\"394\" y=\"116\" fill=\"var(--muted)\" font-size=\"11\" font-family=\"var(--font-sans, sans-serif)\">发布者: 未知发布者（核对官网哈希一致即安全）</text>\n  <text x=\"394\" y=\"134\" fill=\"var(--accent)\" font-size=\"10.5\" font-family=\"var(--font-mono, monospace)\">SHA256: 174e4597...14dd8</text>\n  <text x=\"528\" y=\"169\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"11.5\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">2. 再点「仍要运行」</text>\n  <rect x=\"482\" y=\"182\" width=\"92\" height=\"28\" rx=\"5\" fill=\"var(--accent-soft)\" stroke=\"var(--accent)\" stroke-width=\"1.5\"/>\n  <text x=\"528\" y=\"200\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"12\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">仍要运行</text>\n  <rect x=\"582\" y=\"182\" width=\"76\" height=\"28\" rx=\"5\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"620\" y=\"200\" text-anchor=\"middle\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">不运行</text>\n</svg>\n</div>\n\n<h2 id=\"quickstart-guided-0\">Linux / WSL 一行命令安装</h2>\n<p>打开你的 Linux 终端（Windows 纯终端用户请先打开 <strong>Ubuntu</strong> 窗口），复制下面这行命令并回车：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>curl -fsSL https://nyairo.com/install.sh | bash</code></pre>\n</div>\n<p>想先校验再运行，可分步下载官方压缩包与校验文件：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>curl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.7.0/nyairo-v0.7.0.zip\ncurl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.7.0/nyairo-v0.7.0.zip.sha256\nsha256sum -c nyairo-v0.7.0.zip.sha256\npython3 -m zipfile -e nyairo-v0.7.0.zip \"$HOME/apps/nyairo-v0.7.0\"\ncd \"$HOME/apps/nyairo-v0.7.0\" &amp;&amp; bash scripts/bootstrap.sh</code></pre>\n</div>\n<p>不用提前装 Python 或 Git。脚本会自动下载并校验官方 <code>v0.7.0</code> 发行包，配好 Python 3.13 运行环境，并在 <code>~/.nyairo</code> 下建好你的个人数据目录。</p>\n<p>看到终端提示 <code>[5/5] 程序安装完成</code> 后，会自动进入模型配置向导：</p>\n<ol>\n<li>用上下方向键选择你的模型服务商；</li>\n<li>粘贴你的 <strong>API Key</strong>（如果用的是第三方兼容接口，顺手填一下 Base URL）；</li>\n<li>回车保存后就会直接进入对话界面，发一句「你好」，能收到回复就说明跑通了。</li>\n</ol>\n<div class=\"callout callout-info\"><div class=\"callout-title\">提示找不到 curl？</div><p>极少数刚装好的 Ubuntu 镜像还没带 <code>curl</code>，先跑一句 <code>sudo apt update &amp;&amp; sudo apt install -y curl</code> 再执行上面的安装命令即可。</p></div>\n\n<h2 id=\"quickstart-model-beginner\">零基础配模型：5 元 DeepSeek 密钥或免费 Ollama</h2>\n<p>从来没申请过 API Key 也不用担心，nyairo 的确定性底座不挑昂贵模型，下面两种零门槛方式任选一种就能跑通：</p>\n<ul>\n<li><strong>方式 A（省心推荐 · 无需显卡）：花 5–10 元申请 DeepSeek 密钥</strong>：浏览器打开 <a href=\"https://platform.deepseek.com\" target=\"_blank\" rel=\"noopener noreferrer\">platform.deepseek.com</a> 注册登录，在「充值」页充入 5–10 元（日常对话单次仅几厘钱，足够用很久），点左侧「API keys → 创建 API key」，复制生成的 <code>sk-...</code> 密钥，粘贴进桌面客户端「<strong>设置 → 模型</strong>」（模型选 <code>deepseek-chat</code>），点「保存」并「测试连通」即可。</li>\n<li><strong>方式 B（完全免费 · 零云端流量）：直连本机 Ollama</strong>：前往 <a href=\"https://ollama.com\" target=\"_blank\" rel=\"noopener noreferrer\">ollama.com</a> 安装 Ollama 并拉取轻量模型（如运行 <code>ollama pull qwen2.5:7b</code>），保持后台监听 <code>http://127.0.0.1:11434</code>；在桌面端「<strong>设置 → 模型</strong>」选择本地 Ollama / OpenAI 兼容接口（地址填 <code>http://127.0.0.1:11434/v1</code>，密钥填 <code>ollama</code>），所有对话数据完全不出本机（分步细则见 <a href=\"#configuration-beginner-model\">日常启动、人设与模型</a>）。</li>\n</ul>\n\n<h2 id=\"quickstart-section-1\">装好之后怎么聊天</h2>\n<ul>\n<li><strong>桌面端用户</strong>：点击桌面或右下角托盘里的 <strong>nyairo</strong> 图标就能打开窗口，在侧边栏切换「总览」「对话」「街区」「记忆」或「设置」。</li>\n<li><strong>终端用户</strong>：平时打开终端输入 <code>nyairo</code> 回车就能接着聊；输入 <code>/chiyo_status</code> 可以看记忆（<code>READY</code>）与生活状态（<code>IDLE</code>）是否在线；想换模型随时运行 <code>nyairo setup model</code>。</li>\n<li><strong>连到手机微信</strong>：希望出门在外也能用手机跟 TA 聊，按 <a href=\"#weixin\">接入微信</a> 扫个码就能绑好。</li>\n</ul>\n\n<h2 id=\"quickstart-section-2\">给 TA 起名字、改性格</h2>\n<p>默认人设是一张干净的白纸。想给 TA 起个名字、定下说话语气和你们之间的称呼，用编辑器打开个人目录下的 <code>SOUL.md</code> 即可：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>nano \"$HOME/.nyairo/SOUL.md\"</code></pre>\n</div>\n<p>直接用日常中文写清楚 TA 叫什么、性格偏安静还是活泼、平时怎么称呼你。改完按 <code>Ctrl + O</code> 回车保存，再按 <code>Ctrl + X</code> 退出，下次对话就会按新设定生效。</p>"
  },
  "installation": {
    "title": "安装前准备与系统要求",
    "summary": "装之前花一分钟确认系统环境、磁盘空间与目录结构，省去后面来回折腾。",
    "toc": [
      {
        "id": "installation-section-0",
        "text": "支持哪些操作系统"
      },
      {
        "id": "installation-section-1",
        "text": "程序与个人数据存在哪"
      },
      {
        "id": "installation-section-2",
        "text": "硬件与网络要求"
      }
    ],
    "content": "<h2 id=\"installation-section-0\">支持哪些操作系统</h2>\n<ul>\n<li><strong>Windows 10 / 11</strong>：提供原生桌面客户端（<code>nyairo-0.7.0-setup.exe</code>），生活与记忆后端运行在 Windows 官方自带的 WSL 2（Ubuntu）里。操作步骤见 <a href=\"#windows\">在 Windows 上安装</a>。</li>\n<li><strong>Linux 桌面或服务器</strong>：在 Ubuntu / Debian 环境下验证最完整，直接运行一行引导命令即可。其他发行版需自行安装基础解压与编译工具。</li>\n<li><strong>macOS</strong>：目前还没有做完完整的打包与流程验证，暂不承诺开箱即用；熟悉命令行的用户可以参考源码手动尝试，或等后续专门适配。</li>\n</ul>\n\n<h2 id=\"installation-section-1\">程序与个人数据存在哪</h2>\n<p>为了避免升级软件时误伤你的聊天记录，nyairo 把「程序文件」和「个人数据」彻底分在两个目录里：</p>\n<ul>\n<li><strong>程序目录</strong>：Linux / WSL 下位于 <code>~/.local/share/nyairo/releases/v0.7.0</code>（Windows 桌面端界面程序位于 <code>%LOCALAPPDATA%\\Programs\\nyairo</code>），升级时只替换这一块；</li>\n<li><strong>个人数据目录</strong>：位于 <code>~/.nyairo</code>，里面放着你的人设（<code>SOUL.md</code>）、模型配置、聊天记录和本地 SQLite 长期记忆数据库。</li>\n</ul>\n<p>哪怕卸载重装客户端或回滚版本，只要不手动删掉 <code>~/.nyairo</code>，之前的记忆和设置就都在。</p>\n\n<h2 id=\"installation-section-2\">硬件与网络要求</h2>\n<ul>\n<li><strong>硬件要求</strong>：使用在线模型 API（如 DeepSeek、OpenAI 等）时，推理计算在云端完成，本机只负责跑轻量服务和本地数据库，<strong>不需要独立显卡</strong>。建议留出至少 <strong>3 GB</strong> 可用磁盘空间（用于存放 Python 环境、程序包与后续数据备份）。</li>\n<li><strong>网络要求</strong>：首次安装需要从 GitHub 和 <code>astral.sh</code> 下载发行包与 Python 运行工具；日常使用时，只需要本机能正常连上你所选的模型 API（以及微信或 Telegram，如果你启用了它们）。</li>\n<li><strong>离线表现说明</strong>：关掉电脑、系统休眠或执行 <code>wsl --shutdown</code> 后，后台生活进程也会随之停下；想让 TA 24 小时在线，需要跑在常开的电脑或 Linux 云服务器上。</li>\n</ul>"
  },
  "windows": {
    "title": "在 Windows 上安装（桌面客户端 + WSL2）",
    "summary": "推荐使用免管理员权限的原生 Windows 桌面端（附 SmartScreen 两步矢量图解与自动环境引导），也支持纯 WSL 2 终端命令行安装。",
    "toc": [
      {
        "id": "windows-desktop",
        "text": "方式一：Windows 桌面客户端（推荐）"
      },
      {
        "id": "windows-smartscreen",
        "text": "遇到「Windows 已保护你的电脑」怎么点（SmartScreen 图解）"
      },
      {
        "id": "windows-section-0",
        "text": "前置准备：开启 WSL 2 与 Ubuntu"
      },
      {
        "id": "windows-section-2",
        "text": "方式二：纯 WSL 终端命令行安装"
      }
    ],
    "content": "<h2 id=\"windows-desktop\">方式一：Windows 桌面客户端（推荐）</h2>\n<p>Windows 10 / 11 用户可以直接装原生桌面端（约 5.8 MB，装在当前用户目录下，不需要管理员权限）：</p>\n<ol>\n<li>下载安装包：<a href=\"downloads/nyairo-0.7.0-setup.exe\" download=\"nyairo-0.7.0-setup.exe\"><strong>nyairo-0.7.0-setup.exe</strong></a>（SHA256：<code>174e459727af67c5d97204abd69dd7ce0741ed056a084fc64ba78a187bc14dd8</code>）；</li>\n<li>双击完成安装并打开 <strong>nyairo</strong>（首次打开若弹出 SmartScreen 提示，按下方两步图解点「更多信息 → 仍要运行」即可），程序图标会常驻在屏幕右下角系统托盘；</li>\n<li>首次启动时，客户端走两步引导：<strong>第一步自动准备环境</strong>（下载校验 <code>v0.7.0</code> 发行包 → 装好 Python 依赖 → 建立独立个人目录 → 生成本机通信密钥 → 启动网关 → 探活验证）；<strong>第二步填配置</strong>（当场设置怎么称呼 TA、代词、模型名与 API Key，也可连接本机 Ollama，详见 <a href=\"#configuration-beginner-model\">新手零门槛模型教程</a>）；</li>\n<li>点「完成，开始使用」即可进入主界面。各页面的具体功能与快捷操作见 <a href=\"#desktop-guide\">桌面客户端使用指南</a>。</li>\n</ol>\n<div class=\"callout callout-info\"><div class=\"callout-title\">卸载不会删记忆</div><p>桌面端界面程序装在 <code>%LOCALAPPDATA%\\Programs\\nyairo</code>，而聊天记忆和人设独立保存在 WSL 的个人目录里。卸载桌面客户端只会停掉网关、清理开机自启项与窗口缓存，不会动你的个人数据（详见 <a href=\"#data\">数据目录、备份与卸载</a>）。</p></div>\n\n<h2 id=\"windows-smartscreen\">遇到「Windows 已保护你的电脑」怎么点（SmartScreen 图解）</h2>\n<p>为什么会弹这个提示？因为微软会对尚未购买商业 EV 代码签名证书（每年费用数千元）的新发布独立开源软件统一显示此提醒，并不代表文件有风险。按下面三步核对放行即可：</p>\n<ol>\n<li><strong>第 1 步 · 点击「更多信息」</strong>：在「Windows 已保护你的电脑」弹窗文字下方，点击带下划线的「<strong>更多信息</strong>」小字；</li>\n<li><strong>核对 SHA256 指纹（可选）</strong>：在安装包所在文件夹打开 PowerShell，运行 <code>Get-FileHash .\\nyairo-0.7.0-setup.exe -Algorithm SHA256</code>，确认算出的指纹与官网公布的 <code>174e459727af67c5d97204abd69dd7ce0741ed056a084fc64ba78a187bc14dd8</code> 一字不差；</li>\n<li><strong>第 2 步 · 点击「仍要运行」</strong>：点击窗口底部新出现的「<strong>仍要运行</strong>」按钮，即可正常启动安装。</li>\n</ol>\n<div class=\"smartscreen-guide\" style=\"margin: 16px 0 20px;\">\n<svg viewBox=\"0 0 680 232\" role=\"img\" aria-label=\"Windows SmartScreen 两步放行示意图：第一步点击更多信息，第二步核对 SHA256 后点击仍要运行\" style=\"width: 100%; max-width: 680px; height: auto; display: block; color: var(--fg);\">\n  <rect x=\"4\" y=\"6\" width=\"312\" height=\"220\" rx=\"10\" fill=\"var(--card)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <line x1=\"4\" y1=\"38\" x2=\"316\" y2=\"38\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"20\" y=\"26\" fill=\"var(--accent)\" font-size=\"11\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">STEP 01 · 点「更多信息」</text>\n  <g style=\"color: var(--muted);\" opacity=\"0.7\">\n    <line x1=\"292\" y1=\"18\" x2=\"300\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n    <line x1=\"300\" y1=\"18\" x2=\"292\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n  </g>\n  <text x=\"22\" y=\"64\" fill=\"var(--fg)\" font-size=\"14\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">Windows 已保护你的电脑</text>\n  <text x=\"22\" y=\"88\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">Microsoft Defender SmartScreen 已阻止启动</text>\n  <text x=\"22\" y=\"106\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">未识别的应用（开源构建包未购商业签名）</text>\n  <rect x=\"20\" y=\"122\" width=\"84\" height=\"26\" rx=\"6\" fill=\"var(--accent-soft)\" stroke=\"var(--accent)\" stroke-width=\"1.4\"/>\n  <text x=\"62\" y=\"139\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"12\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">更多信息</text>\n  <g style=\"color: var(--accent);\">\n    <path d=\"M138 135H112m6-5-6 5 6 5\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.5\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n  </g>\n  <text x=\"144\" y=\"139\" fill=\"var(--accent)\" font-size=\"11.5\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">1. 先点这里展开</text>\n  <rect x=\"222\" y=\"182\" width=\"78\" height=\"28\" rx=\"5\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"261\" y=\"200\" text-anchor=\"middle\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">不运行</text>\n  <g style=\"color: var(--accent);\">\n    <path d=\"M325 116h28m-6-6 6 6-6 6\" fill=\"none\" stroke=\"currentColor\" stroke-width=\"1.6\" stroke-linecap=\"round\" stroke-linejoin=\"round\"/>\n  </g>\n  <rect x=\"364\" y=\"6\" width=\"312\" height=\"220\" rx=\"10\" fill=\"var(--card)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <line x1=\"364\" y1=\"38\" x2=\"676\" y2=\"38\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"380\" y=\"26\" fill=\"var(--accent)\" font-size=\"11\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">STEP 02 · 点「仍要运行」</text>\n  <g style=\"color: var(--muted);\" opacity=\"0.7\">\n    <line x1=\"652\" y1=\"18\" x2=\"660\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n    <line x1=\"660\" y1=\"18\" x2=\"652\" y2=\"26\" stroke=\"currentColor\" stroke-width=\"1.3\" stroke-linecap=\"round\"/>\n  </g>\n  <text x=\"382\" y=\"64\" fill=\"var(--fg)\" font-size=\"14\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">Windows 已保护你的电脑</text>\n  <rect x=\"382\" y=\"78\" width=\"276\" height=\"66\" rx=\"6\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"394\" y=\"98\" fill=\"var(--fg-2)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">应用: nyairo-0.7.0-setup.exe</text>\n  <text x=\"394\" y=\"116\" fill=\"var(--muted)\" font-size=\"11\" font-family=\"var(--font-sans, sans-serif)\">发布者: 未知发布者（核对官网哈希一致即安全）</text>\n  <text x=\"394\" y=\"134\" fill=\"var(--accent)\" font-size=\"10.5\" font-family=\"var(--font-mono, monospace)\">SHA256: 174e4597...14dd8</text>\n  <text x=\"528\" y=\"169\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"11.5\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">2. 再点「仍要运行」</text>\n  <rect x=\"482\" y=\"182\" width=\"92\" height=\"28\" rx=\"5\" fill=\"var(--accent-soft)\" stroke=\"var(--accent)\" stroke-width=\"1.5\"/>\n  <text x=\"528\" y=\"200\" text-anchor=\"middle\" fill=\"var(--accent)\" font-size=\"12\" font-weight=\"600\" font-family=\"var(--font-sans, sans-serif)\">仍要运行</text>\n  <rect x=\"582\" y=\"182\" width=\"76\" height=\"28\" rx=\"5\" fill=\"var(--card-2)\" stroke=\"var(--line)\" stroke-width=\"1\"/>\n  <text x=\"620\" y=\"200\" text-anchor=\"middle\" fill=\"var(--muted)\" font-size=\"11.5\" font-family=\"var(--font-sans, sans-serif)\">不运行</text>\n</svg>\n</div>\n\n<h2 id=\"windows-section-0\">前置准备：开启 WSL 2 与 Ubuntu</h2>\n<p>如果你的电脑之前没开过 WSL 2（桌面端首次打开时也会提示你），按下面两步花几分钟装好就行：</p>\n<ol>\n<li>在 Windows 开始菜单搜索 <strong>PowerShell</strong>，右键选<strong>以管理员身份运行</strong>；</li>\n<li>粘贴下面这行命令并按回车：</li>\n</ol>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>powershell</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>wsl --install -d Ubuntu</code></pre>\n</div>\n<p>装完如果提示重启电脑，先重启一次；接着从开始菜单打开 <strong>Ubuntu</strong>，按提示设一个英文用户名和密码（Linux 终端输密码时屏幕不显示星号是正常现象，输完直接回车）。设好之后再打开 nyairo 桌面端，剩下的配置它会自动接手。</p>\n\n<h2 id=\"windows-section-2\">方式二：纯 WSL 终端命令行安装</h2>\n<p>如果你不喜欢图形界面、只想在终端里敲命令使用，打开 <strong>Ubuntu</strong> 终端粘贴这一行即可：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>curl -fsSL https://nyairo.com/install.sh | bash</code></pre>\n</div>\n<p>想先校验再运行，可按下面几行核对 SHA256 后再安装：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>curl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.7.0/nyairo-v0.7.0.zip\ncurl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.7.0/nyairo-v0.7.0.zip.sha256\nsha256sum -c nyairo-v0.7.0.zip.sha256\npython3 -m zipfile -e nyairo-v0.7.0.zip \"$HOME/apps/nyairo-v0.7.0\"\ncd \"$HOME/apps/nyairo-v0.7.0\" &amp;&amp; bash scripts/bootstrap.sh</code></pre>\n</div>\n<p>装完按屏幕提示填好模型 API Key，之后每次打开 Ubuntu 输入 <code>nyairo</code> 就能对话。</p>"
  },
  "linux": {
    "title": "Linux 与手动安装",
    "summary": "适合想自己查看每一步脚本、离线保存发行包，或在服务器上手动部署的用户。",
    "toc": [
      {
        "id": "linux-section-0",
        "text": "第一步：准备基础系统工具"
      },
      {
        "id": "linux-section-1",
        "text": "第二步：准备 uv 与 Python 3.13"
      },
      {
        "id": "linux-section-2",
        "text": "路线 A：通过 Git 标签克隆安装"
      },
      {
        "id": "linux-section-3",
        "text": "路线 B：下载官方 ZIP 校验安装"
      },
      {
        "id": "linux-section-4",
        "text": "机器上已经装过 Hermes 怎么办"
      }
    ],
    "content": "<div class=\"callout callout-info\"><div class=\"callout-title\">先看这里</div><p>如果你已经用 <code>curl -fsSL https://nyairo.com/install.sh | bash</code> 一行命令装好了，<strong>不需要再做本章任何步骤</strong>，直接去 <a href=\"#configuration\">日常启动、人设与模型</a> 即可。本章提供给希望逐步手动检查并部署的用户。</p></div>\n\n<h2 id=\"linux-section-0\">第一步：准备基础系统工具</h2>\n<p>在 Linux 终端（或 Windows 的 Ubuntu 终端）里先装齐基础工具（以 Ubuntu / Debian 为例）：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>sudo apt update\nsudo apt install -y git curl unzip ripgrep less nano python3</code></pre></div>\n<p>输入当前 Linux 账号密码回车，确认没有报错再继续下一步。</p>\n\n<h2 id=\"linux-section-1\">第二步：准备 uv 与 Python 3.13</h2>\n<p>项目使用 <a href=\"https://docs.astral.sh/uv/getting-started/installation/\" target=\"_blank\" rel=\"noopener noreferrer\">uv</a> 来管理独立的 Python 环境与依赖，不会污染系统自带的 Python。你可以先把脚本下载下来看一眼再执行：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>curl -LsSf https://astral.sh/uv/install.sh -o /tmp/uv-install.sh\nless /tmp/uv-install.sh\nsh /tmp/uv-install.sh</code></pre></div>\n<p>浏览时按 <code>q</code> 退出 <code>less</code>。装完重新打开一个终端窗口，拉取 Python 3.13：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>uv --version\nuv python install 3.13</code></pre></div>\n\n<h2 id=\"linux-section-2\">路线 A：通过 Git 标签克隆安装</h2>\n<p>路线 A 和路线 B 选一种就行。使用 Git 时，请固定检出已发布的 <code>v0.7.0</code> 标签（不要直接装正在变动中的开发分支）：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>mkdir -p \"$HOME/apps\"\ngit clone --branch v0.7.0 --depth 1 \\\n  https://github.com/L1AN929/nyairo.git \"$HOME/apps/nyairo-v0.7.0\"\ncd \"$HOME/apps/nyairo-v0.7.0\"\nbash scripts/bootstrap.sh</code></pre></div>\n<p>检出固定标签时 Git 提示 <code>detached HEAD</code> 是正常现象。安装完成后，可以跑一遍清单自检确认所有文件完整无缺：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>~/.local/share/nyairo/releases/v0.7.0/vendor/hermes/.venv/bin/python -B \\\n  ~/.local/share/nyairo/releases/v0.7.0/scripts/verify_manifest.py</code></pre></div>\n<p>输出中包含 <code>\"valid\": true</code> 即代表文件校验完全一致。</p>\n\n<h2 id=\"linux-section-3\">路线 B：下载官方 ZIP 校验安装</h2>\n<p>如果你想先把发行包保存下来核对哈希，再解压安装：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>mkdir -p \"$HOME/apps\" &amp;&amp; cd \"$HOME/apps\"\ncurl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.7.0/nyairo-v0.7.0.zip\ncurl -fsSL -O https://github.com/L1AN929/nyairo/releases/download/v0.7.0/nyairo-v0.7.0.zip.sha256\nsha256sum -c nyairo-v0.7.0.zip.sha256</code></pre></div>\n<p>确认屏幕输出 <code>nyairo-v0.7.0.zip: OK</code> 后，解压并调用包内安装器：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>python3 -m zipfile -e nyairo-v0.7.0.zip \"$HOME/apps/nyairo-v0.7.0\"\ncd \"$HOME/apps/nyairo-v0.7.0\"\nbash scripts/bootstrap.sh</code></pre></div>\n<p>如果 ZIP 是你在 Windows 浏览器里提前下载好的，WSL 里可以通过 <code>/mnt/c/Users/你的Windows用户名/Downloads/nyairo-v0.7.0.zip</code> 访问它。</p>\n\n<h2 id=\"linux-section-4\">机器上已经装过 Hermes 怎么办</h2>\n<p>不用删掉你原来的 Hermes。nyairo 自带了经过版本匹配测试的配套 Hermes 环境，并把数据独立放在 <code>~/.nyairo</code> 下，跟原版互不干扰。请不要手动把 nyairo 的插件目录硬拷进其他版本的 Hermes 里混用，否则很容易因为接口差异报错。</p>"
  },
  "agent-install": {
    "title": "交给 AI 助手自动安装",
    "summary": "如果你平时在用 Claude Code、Codex 或其他带终端权限的 AI 编程工具，可以把这段标准说明直接复制给它，让它先检查环境再安全安装。",
    "toc": [
      {
        "id": "agent-section-0",
        "text": "适合哪些工具与场景"
      },
      {
        "id": "agent-section-1",
        "text": "整段复制给 AI 助手的指令"
      },
      {
        "id": "agent-section-2",
        "text": "为什么要设这四条安全规矩"
      },
      {
        "id": "agent-section-3",
        "text": "装完后你自己核对哪几项"
      }
    ],
    "content": "<div class=\"callout callout-info\"><div class=\"callout-title\">纯本地执行，不索取密钥</div><p>官网只提供这份文字规范（独立原文见 <a href=\"AGENT_INSTALL.zh-CN.md\">AGENT_INSTALL.zh-CN.md</a>），所有检查和下载都在你自己的电脑终端里由你自己的 AI 工具完成。<strong>请勿把任何密码、SSH 私钥或模型 API Key 粘贴进 AI 助手对话框。</strong></p></div>\n\n<h2 id=\"agent-section-0\">适合哪些工具与场景</h2>\n<ul>\n<li><strong>适用工具</strong>：Claude Code、OpenAI Codex CLI、Cursor Agent、Windsurf 或任何能在你的 Linux / WSL 终端里执行命令的本地助手。</li>\n<li><strong>特别省心的场景</strong>：手头是一台刚开的 Linux 云服务器，或者你的电脑里以前装过旧版实例、怕自己敲命令不小心覆盖旧数据——这份指令强制要求 AI 先做只读摸底汇报，经你点头后再动手。</li>\n</ul>\n\n<h2 id=\"agent-section-1\">整段复制给 AI 助手的指令</h2>\n<p>点击代码块右上角的「复制」，原样发给你的 AI 助手即可：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>请帮我在这台电脑上安装 nyairo（v0.7.0）。请严格按 https://nyairo.com/AGENT_INSTALL.zh-CN.md 的规范执行，每一步用中文告诉我你在做什么，不要跳过检查，也不要假装成功：\n\n1. 【只读检查，先别安装】检查系统环境（需 Linux 或 WSL 2）、磁盘剩余空间（建议 ≥ 3 GB）、能否连上 github.com 与 astral.sh；检查当前 HOME 是否已有 ~/.nyairo、~/.local/share/nyairo、~/.chiyo-v1，以及是否有正在运行的 m0_writer_worker 或 gateway 进程。若发现已有实例，必须立刻停下来汇报并建议换独立 Linux 用户或独立 HOME，绝不能覆盖已有数据，也绝不能终止已有实例的进程。\n2. 【征求确认】向我说明即将创建的目录（~/.local/share/nyairo/releases/v0.7.0、~/.nyairo、~/.local/bin/nyairo），涉及 sudo 或新建系统用户必须先等我同意。\n3. 【校验安装】从 https://github.com/L1AN929/nyairo/releases/download/v0.7.0/ 下载 nyairo-v0.7.0.zip 与同名 .sha256，执行 sha256sum -c 校验通过后解压，运行包内 bash scripts/bootstrap.sh（无交互终端可加 --no-setup --no-launch）。\n4. 【实机核验】检查 ~/.local/state/nyairo/install.json 版本为 v0.7.0，用包内 Python 运行 scripts/verify_manifest.py 确认输出 \"valid\": true，执行一次 ~/.local/bin/nyairo -z \"安装自检\" 并确认新实例无残留进程。我未填密钥前，模型与外部通道一律如实标为「尚未验证」。\n5. 【中文交付】用简明中文告诉我启动（nyairo）、配模型（nyairo setup model）、接微信/Telegram、更新与备份方法。全程绝不要让我把 API Key 或密码发到聊天框里。</code></pre></div>\n\n<h2 id=\"agent-section-2\">为什么要设这四条安全规矩</h2>\n<ul>\n<li><strong>不覆盖旧记忆</strong>：很多 AI 助手看到目录已存在会习惯性删掉重装。指令里明确规定只要发现现存的 <code>~/.nyairo</code> 或 <code>~/.chiyo-v1</code> 就必须停手，引导用独立账号隔离。</li>\n<li><strong>不乱杀正在跑的进程</strong>：如果你机器上本来就挂着一个正在用的实例，AI 在做环境清理时不能去 <code>kill</code> 原实例的网关和记忆写入进程。</li>\n<li><strong>只认官方哈希与清单</strong>：强制核对 GitHub Release 同名 <code>.sha256</code> 与包内 <code>MANIFEST.json</code>，不拉取未发布的开发分支代码。</li>\n<li><strong>密钥留在本地填</strong>：AI 把基础环境装完就交还给你，你自己运行 <code>nyairo setup model</code> 把 API Key 写进本机 <code>0600</code> 权限的配置文件里，避免密钥流经第三方对话日志。</li>\n</ul>\n\n<h2 id=\"agent-section-3\">装完后你自己核对哪几项</h2>\n<p>AI 汇报「安装完成」后，你可以顺手看一眼这三件事确认没被糊弄：</p>\n<ol>\n<li><code>cat ~/.local/state/nyairo/install.json</code> 里的 <code>version</code> 确实是 <code>v0.7.0</code>（注：直接敲 <code>nyairo --version</code> 显示的是底层宿主 Hermes 的版本号，看 nyairo 自身版本以 <code>install.json</code> 为准）；</li>\n<li><code>~/.nyairo</code> 目录下已经生成了 <code>SOUL.md</code>、<code>config.yaml</code> 和 <code>chiyo/config.json</code>；</li>\n<li>运行 <code>nyairo setup model</code> 填好你自己的 API Key，再敲 <code>nyairo</code> 发一句问候，能正常收到回复就大功告成了。</li>\n</ol>"
  },
  "configuration": {
    "title": "日常启动、人设与模型",
    "summary": "平时怎么唤醒 TA 聊天、怎么自定义名字与说话习惯，以及新手如何花 5 元配好 DeepSeek 密钥或零流量连接本地 Ollama。",
    "toc": [
      {
        "id": "configuration-section-0",
        "text": "平时怎么打开聊天"
      },
      {
        "id": "configuration-section-1",
        "text": "修改名字与人设 (SOUL.md)"
      },
      {
        "id": "configuration-beginner-model",
        "text": "新手零门槛教程：5 元 DeepSeek 密钥与免费本地 Ollama"
      },
      {
        "id": "configuration-section-2",
        "text": "切换模型或更换 API Key"
      }
    ],
    "content": "<h2 id=\"configuration-section-0\">平时怎么打开聊天</h2>\n<ul>\n<li><strong>Windows 桌面端</strong>：双击桌面快捷方式或点开右下角托盘里的猫咪图标即可。按 <code>Ctrl + K</code> 可以随时呼出快捷操作栏，切换界面或发起新对话。</li>\n<li><strong>Linux / WSL 终端</strong>：打开终端输入一行 <code>nyairo</code> 回车就能开聊；聊完输入 <code>/exit</code> 或按 <code>Ctrl + D</code> 退回命令行。</li>\n</ul>\n\n<h2 id=\"configuration-section-1\">修改名字与人设 (SOUL.md)</h2>\n<p>TA 叫什么名字、平时怎么称呼你、说话简洁还是细腻，都写在个人数据目录的 <code>SOUL.md</code> 文件里。用任意文本编辑器或终端自带的 <code>nano</code> 打开就能改：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>nano \"$HOME/.nyairo/SOUL.md\"</code></pre>\n</div>\n<p>不需要写复杂的提示词套路，像写人物小传一样用自然中文写清楚这三点就够了：</p>\n<ul>\n<li><strong>名字与关系</strong>：TA 的名字，以及你们平时以什么身份相处（朋友、室友、搭档等）；</li>\n<li><strong>说话习惯</strong>：口吻偏安静克制还是轻松随性，喜不喜欢长篇大论；</li>\n<li><strong>基本默契</strong>：认真对待聊过的约定，不知道的事如实说不知道，不硬编；不想应承的忙也可以自然说不。</li>\n</ul>\n<p>在 <code>nano</code> 里改完后，按 <code>Ctrl + O</code> 回车保存，再按 <code>Ctrl + X</code> 退出，重新进入对话即生效。</p>\n\n<h2 id=\"configuration-beginner-model\">新手零门槛教程：5 元 DeepSeek 密钥与免费本地 Ollama</h2>\n<p>nyairo 本身不卖算力、也不抽成：空间寻路、冷热物态与权限都由本地确定性骨架驱动，不需要额外花钱调「裁判大模型」。完全没接触过 API 的新手，按下面两条路线任选其一，三分钟就能配好：</p>\n\n<h3>路线一：花 5–10 元开通 DeepSeek API Key（推荐轻薄本用户，无需独立显卡）</h3>\n<ol>\n<li>浏览器打开 DeepSeek 官方开放平台 <a href=\"https://platform.deepseek.com\" target=\"_blank\" rel=\"noopener noreferrer\">platform.deepseek.com</a>，用手机号注册并登录；</li>\n<li>点击左侧菜单「<strong>充值</strong>」，充入 <strong>5 到 10 元人民币</strong>（日常聊天一句话只消耗几厘钱，5 元足够日常聊上好几周，且桌面端默认自带每日 50,000 Token 用量封顶保护）；</li>\n<li>点击左侧菜单「<strong>API keys</strong>」→「<strong>创建 API key</strong>」，名称随意填（如 <code>nyairo</code>），立刻点击复制生成的以 <code>sk-...</code> 开头的密钥串（关掉弹窗后网页不再显示完整密钥，请妥善保存，切勿发给他人）；</li>\n<li>打开 nyairo 桌面客户端，点击左侧「<strong>设置 → 模型</strong>」（界面对应「模型与通道」卡片）：\n  <ul>\n    <li>服务商选择 <strong>DeepSeek</strong>（Base URL 为 <code>https://api.deepseek.com</code>）；</li>\n    <li>模型名称填写 <code>deepseek-chat</code>；</li>\n    <li>将刚才复制的 <code>sk-...</code> 密钥粘贴进 API Key 输入框，点击「<strong>保存配置</strong>」，再点旁边的「<strong>测试连通</strong>」，看到返回毫秒级响应耗时就配好了。</li>\n  </ul>\n</li>\n</ol>\n\n<h3>路线二：免费连接本地 Ollama（适合有闲置内存或显卡、希望零云端流量的用户）</h3>\n<ol>\n<li>前往 <a href=\"https://ollama.com\" target=\"_blank\" rel=\"noopener noreferrer\">ollama.com</a> 下载并安装 Ollama，打开系统终端拉取一个中文表现均衡的本地模型（16 GB 内存电脑推荐 7B，8 GB 内存可选 3B）：\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>ollama pull qwen2.5:7b</code></pre>\n</div></li>\n<li>确认 Ollama 服务已在后台启动（默认监听本机 <code>http://127.0.0.1:11434</code>）；</li>\n<li>打开 nyairo 桌面客户端「<strong>设置 → 模型</strong>」（或在终端运行 <code>nyairo setup model</code>）：\n  <ul>\n    <li>选择 <strong>Ollama / 本地兼容接口</strong>，接口地址填 <code>http://127.0.0.1:11434/v1</code>（Ollama 原生服务地址为 <code>http://127.0.0.1:11434</code>）；</li>\n    <li>模型名称填刚才拉取的 <code>qwen2.5:7b</code>，API Key 栏随意填 <code>ollama</code> 占位即可；</li>\n    <li>点击保存并测试连通。此后所有对话推理、长期记忆与日常节律 100% 在你自己的电脑闭环运行，零云端流量、零账单开销。</li>\n  </ul>\n</li>\n</ol>\n\n<h2 id=\"configuration-section-2\">切换模型或更换 API Key</h2>\n<p>以后想换用别的模型（比如在 DeepSeek、Claude、OpenAI、Qwen 或本地 Ollama 之间切换），或者更新过期的 API Key：</p>\n<ul>\n<li><strong>桌面端用户</strong>：直接在左侧「设置 → 模型」面板里修改模型供应商、模型名称与 API Key，点保存并测试连通即可（密钥只写入本机 <code>0600</code> 权限配置，界面不会明文回显旧密钥）。</li>\n<li><strong>终端用户</strong>：在命令行运行：</li>\n</ul>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>nyairo setup model</code></pre>\n</div>\n<p>换模型不会影响已经攒下的聊天记录、房间物态和长期记忆。</p>"
  },
  "weixin": {
    "title": "接入微信",
    "badge": "推荐",
    "summary": "不用折腾公众号或第三方破解端，手机微信扫一下码就能连上本机的 TA，出门在外也能随时发消息。",
    "toc": [
      {
        "id": "wx-why",
        "text": "连上微信是什么体验？"
      },
      {
        "id": "wx-setup",
        "text": "扫码绑定步骤"
      },
      {
        "id": "wx-features",
        "text": "在微信里能互动什么？"
      },
      {
        "id": "wx-safety",
        "text": "隐私与防打扰机制"
      }
    ],
    "content": "<h2 id=\"wx-why\">连上微信是什么体验？</h2>\n<p>总不能每次想说句话都得坐回电脑前开窗口。把 nyairo 连进微信之后，手机上就是一个再熟悉不过的私聊对话框：</p>\n<ul>\n<li><strong>出门接着聊</strong>：刚在电脑前讨论到一半的事，下楼买杯水的工夫掏出手机就能顺着往下说，两边连的是同一个记忆库；</li>\n<li><strong>实时感知 TA 在哪</strong>：问一句「在干嘛呢」，TA 会根据后台真实的房间或街区状态回答你是在屋里看书，还是刚走到街口；</li>\n<li><strong>重启不断线</strong>：电脑重启或网关重新连上后，之前的对话脉络依然接得上。</li>\n</ul>\n\n<h2 id=\"wx-setup\">扫码绑定步骤</h2>\n<p>nyairo 内置了官方轻量机器人通道，无需申请企业号或公网域名。</p>\n\n<h3>方式 A：Windows 桌面端扫码（最省事）</h3>\n<ol>\n<li>打开 nyairo 桌面客户端，进入左侧 <strong>设置</strong> 页面；</li>\n<li>找到「微信接入」卡片，点击生成绑定二维码；</li>\n<li>用你自己的手机微信扫一扫并确认授权，看到状态变为「已连接」就绑好了。</li>\n</ol>\n\n<h3>方式 B：Linux / WSL 终端扫码</h3>\n<p>在终端里先启用微信通道：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>nyairo gateway setup</code></pre></div>\n<p>在菜单中勾选启用 <strong>Weixin</strong>（对应配置文件 <code>~/.nyairo/config.yaml</code> 中的 <code>platforms.weixin.enabled: true</code>），随后启动网关：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>nyairo gateway run</code></pre></div>\n<p>终端会打印出字符二维码（及备用链接），拿手机微信扫码确认即可完成绑定。</p>\n\n<h2 id=\"wx-features\">在微信里能互动什么？</h2>\n<ul>\n<li><strong>让 TA 出门散步或办事</strong>：发微信对 TA 说「去书店看看吧」或「出去吹吹风」，TA 会在后台真正动身出发，并告诉你路程大概需要几分钟；半路问 TA 走到哪了，TA 会照实回答还要走多久，不会一秒瞬移。</li>\n<li><strong>感知早晚与店铺开关门</strong>：深夜让 TA 去已经打烊的咖啡馆，TA 会发现店门关了并如实跟你说，再自己决定去别处转转还是回家。</li>\n<li><strong>安安静静待着</strong>：对 TA 说「忙完了早点歇着吧」，TA 就会留在房间里休息，不会没话找话刷屏。</li>\n</ul>\n\n<h2 id=\"wx-safety\">隐私与防打扰机制</h2>\n<ul>\n<li><strong>仅限号主本人私聊</strong>：默认开启严格白名单（<code>allowlist</code>）并关闭群聊响应，只有扫码绑定的你本人发消息 TA 才会理会，不怕陌生人乱加消耗你的 API 额度；</li>\n<li><strong>不经过 nyairo 服务器</strong>：nyairo 没有自己的云端后台，对话内容只经过你自己选择的模型服务商与消息平台；人设、长期记忆与生活状态只写在你自己电脑的 <code>~/.nyairo</code> 目录里。</li>\n</ul>"
  },
  "telegram": {
    "title": "接入 Telegram 与 QQ",
    "summary": "通过 Telegram 机器人或 QQ 官方机器人连接本机的 TA；重点注意会话白名单授权，避免配好凭证却收不到回复。",
    "toc": [
      {
        "id": "telegram-section-0",
        "text": "Telegram · 创建机器人并填 Token"
      },
      {
        "id": "telegram-section-1",
        "text": "Telegram · 绑定私聊记忆权限"
      },
      {
        "id": "telegram-section-2",
        "text": "Telegram · 启动网关并自检"
      },
      {
        "id": "qqbot-setup",
        "text": "QQ 官方机器人接入与两个必看避坑点"
      },
      {
        "id": "telegram-section-3",
        "text": "其他聊天平台说明"
      }
    ],
    "content": "<h2 id=\"telegram-section-0\">Telegram · 创建机器人并填 Token</h2>\n<p>建议先确认本机终端或桌面端里能正常收到模型回复，再来配外部聊天软件。</p>\n<ol>\n<li>在 Telegram 里搜索官方的 <strong>@BotFather</strong>，发送 <code>/newbot</code> 按提示创建一个机器人，复制它发给你的那串 <strong>HTTP API Token</strong>（这串凭证相当于机器人钥匙，切勿发到公开群或贴进截图）；</li>\n<li>在终端运行网关向导（Windows 桌面端用户也可直接在「设置 → 模型与通道」页填写）：</li>\n</ol>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>nyairo gateway setup</code></pre></div>\n<p>选择 <strong>Telegram</strong>，填入刚才拿到的 Token；在「允许访问的用户（Allowed Users）」里<strong>只填你自己的纯数字 Telegram User ID</strong>（注意不是 <code>@用户名</code>，也不是昵称）。如果向导自动识别出了 <code>Detected your Telegram user ID</code>，核对无误直接确认即可。</p>\n\n<h2 id=\"telegram-section-1\">Telegram · 绑定私聊记忆权限</h2>\n<p>为了防止陌生人误触机器人读到你的私人记忆，nyairo 要求明确指定「哪一个私聊会话拥有读写长期记忆的权限」。</p>\n<p>如果你用的是一行命令默认安装（数据在 <code>~/.nyairo</code>），运行下面这段脚本，按提示输入你的纯数字私聊 User ID，它会算出对应的会话标识：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>REL=\"$HOME/.local/share/nyairo/releases/v0.7.0\"\nPYTHONPATH=\"$REL:$REL/vendor/hermes\" \"$REL/vendor/hermes/.venv/bin/python\" -c '\nfrom gateway.config import Platform\nfrom gateway.session import SessionSource, build_session_key\nuid = input(\"请输入你的 Telegram 纯数字 User ID: \").strip()\nsource = SessionSource(platform=Platform.TELEGRAM, chat_type=\"dm\", chat_id=uid, user_id=uid)\nprint(\"请复制下面这行绑定标识：\")\nprint(build_session_key(source))\n'</code></pre></div>\n<p>复制算出来的那串标识，打开 <code>~/.nyairo/chiyo/config.json</code>：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>nano \"$HOME/.nyairo/chiyo/config.json\"</code></pre></div>\n<p>保留文件里的其他字段不动，把 <code>gateway_bindings</code> 里的 <code>telegram</code> 数组填成刚才算出的那串字符串（Windows 桌面端用户可以直接在「设置 → 模型与通道」底部的会话白名单里勾选放行）：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>json</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>{\n  \"gateway_bindings\": {\n    \"telegram\": [\"粘贴刚才打印出来的会话标识\"]\n  }\n}</code></pre></div>\n\n<h2 id=\"telegram-section-2\">Telegram · 启动网关并自检</h2>\n<p>保存退出后，运行网关保持在线：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>nyairo gateway run</code></pre></div>\n<p>注意同一个机器人 Token 同一时刻只能由一个网关进程轮询接收；如果报 <code>409 Conflict</code>，说明你开了两个网关窗口（比如桌面端后台网关和手动终端同时在跑），关掉其中一个就行。</p>\n<p>打开手机 Telegram 给你的机器人发几条消息验证一下：</p>\n<ol>\n<li>发一句日常问候，确认能正常收到回复；</li>\n<li>发送 <code>/chiyo_status</code>，确认返回模块状态（<code>Memory: READY</code>）；</li>\n<li>发送 <code>/chiyo_memory list</code>，确认私聊已正常绑定记忆权限（新装列表为空属正常现象）。</li>\n</ol>\n\n<h2 id=\"qqbot-setup\">QQ 官方机器人接入与两个必看避坑点</h2>\n<p>底层网关内置了 QQ 官方机器人（API v2）适配器。如果你想在 QQ 私聊里跟 TA 说话，前往 <a href=\"https://q.qq.com\" target=\"_blank\" rel=\"noopener noreferrer\">QQ 开放平台 (q.qq.com)</a> 创建一个机器人，拿到 <strong>AppID</strong> 和 <strong>AppSecret</strong>。在 Windows 桌面端「设置 → 模型与通道」的「接入 QQ」栏填入保存，网关会自动重启连接，并在界面上原样展示网关日志里的真实连接结果。</p>\n<p>如果手动改配置文件，或者遇到「明明连上了却不回话」，请重点核对这两个实测踩过的坑：</p>\n<ol>\n<li><strong>配置键名必须是 <code>qqbot</code>，不能写成 <code>qq</code></strong>：在 <code>config.yaml</code> 的 <code>gateway.platforms</code> 下，平台名称严格叫 <code>qqbot</code>。如果手敲写成 <code>qq</code>，网关会直接忽略整段配置，日志里连报错都不会打一行。</li>\n<li><strong>光填好 AppID 和密钥还不够，必须把具体私聊会话加进白名单</strong>：nyairo 的隐私门禁只放行 <code>gateway_bindings.qqbot</code> 里明确授权过的私聊键（格式为 <code>agent:main:qqbot:dm:你的用户标识</code>）。没加白名单前，QQ 消息就算到了网关也会被安静拦下——先给机器人发一句话，再去桌面端「谁可以跟 TA 说话」面板里把刚出现的会话勾上（或从日志里抄下该键写入 <code>chiyo/config.json</code>）即可正常对话。</li>\n</ol>\n\n<h2 id=\"telegram-section-3\">其他聊天平台说明</h2>\n<ul>\n<li><strong>个人微信（最省事）</strong>：无需注册开放平台或填复杂 ID，手机微信扫一下二维码就能连上，详见上一章 <a href=\"#weixin\">接入微信</a>。</li>\n<li><strong>飞书 / 企业微信 / Discord 等</strong>：底层宿主代码里虽然保留了部分第三方平台文件，但我们尚未做完记忆权限绑定与断线重连实测，目前不作为正式支持渠道。</li>\n</ul>"
  },
  "desktop-guide": {
    "title": "桌面客户端使用指南",
    "badge": "桌面端",
    "summary": "把 Windows 桌面端的五个主栏目、12 类店铺点单与包袋物态、异步回信与拒绝边界、SOUL.md 保护以及无损搬家旅行箱一次讲清。",
    "toc": [
      {
        "id": "dg-overview-talk",
        "text": "总览与对话：接话、异步待回匣与急事敲门"
      },
      {
        "id": "dg-world-records",
        "text": "街区、12 类店铺、包袋物态与四类记忆"
      },
      {
        "id": "dg-identity-soul",
        "text": "自由称呼、代词与 SOUL.md 保护"
      },
      {
        "id": "dg-memory-boundary",
        "text": "实用细节：「新对话」与记忆召回窗口"
      },
      {
        "id": "dg-tray-diag",
        "text": "托盘常驻、快捷键、无损旅行箱与脱敏诊断包"
      }
    ],
    "content": "<h2 id=\"dg-overview-talk\">总览与对话：接话、异步待回匣与急事敲门</h2>\n<p>桌面端把繁杂的后台运行参数收进了折叠栏，日常打开主要用到左侧前两个栏目：</p>\n<ul>\n<li><strong>总览</strong>：顶部用一句干净的状态行告诉你 TA 是否在线、已运行多久、记住了多少条事；左栏摆着你们<strong>上一轮问答与快捷输入框</strong>，不用切页就能直接接着聊；右栏展示最近记住的几句原话（已自动过滤掉左栏刚显示过的那句，避免重复）。底部的「运行详情」默认收起，点开才能看到模块状态与后台事件。</li>\n<li><strong>对话与相处分寸</strong>：支持流式逐字回复、悬停复制单条消息、<strong>选中某句话引用追问</strong>、断网一键<strong>重试</strong>以及把当前会话<strong>导出为本地 Markdown 文件</strong>。日常交谈具备<strong>去工具人化的自主拒绝边界</strong>——不想帮的忙 TA 会干脆拒绝，不念客服式道歉模板也不记仇；遇到睡眠、外出或专注时段，非急事会先收入<strong>手机待回匣</strong>按真实日程隔几小时异步回信，真有急事可显式<strong>敲门找 TA</strong>。</li>\n</ul>\n\n<h2 id=\"dg-world-records\">街区、12 类店铺、包袋物态与四类记忆</h2>\n<ul>\n<li><strong>街区与随身物态</strong>：直观看到当前世界（支持免选风貌的全随机专属世界一键生成、7 套内置世界与 4 种主题风格）及房间平面。你可以点选地点让 TA 步行前往、开关灯具，查看<strong>随身包袋最多 3 层嵌套收纳</strong>与<strong>冷热干湿物态</strong>（桌边热茶自然放凉、窗边纸条遇雨微潮晕字），或打开深浅双色<a href=\"#world-editor\">离线地图编辑器</a>。</li>\n<li><strong>商店（12 类街区店铺开放点单）</strong>：支持 <strong>12 类街区店铺与开放柜台自由点单</strong>（例如去便利店买一份热关东煮），旧书店与咖啡屋店主各自按独立错峰作息出勤并支持吧台杯垫留言。注意商店遵循真实空间规则——<strong>只有 TA 真的走到了营业中的店面里才能逛店点单</strong>，待在家里点开会如实提示当前不在店内。</li>\n<li><strong>记忆（双层回忆灯与四个子栏目）</strong>：\n  <ul>\n    <li><strong>常驻记忆</strong>：底层由<strong>双层回忆灯</strong>机制严格保护对话原稿零篡改；右上角搜索框可按关键词实时过滤已记住的条目，支持手动纠错或停止召回；</li>\n    <li><strong>随笔与创作</strong>：集中查看 TA 写的随笔与受管文稿，对比不同历史版本的增删差异；</li>\n    <li><strong>相册与共读</strong>：翻看留存的明信片相册，或导入文本读物跟 TA 一起看（详见下一章 <a href=\"#daily-companions\">共读、文稿、相册与节律</a>）；</li>\n    <li><strong>共处与实践</strong>：查看近期的共同活动记录、未来留问、共同默契话头与待办安排。</li>\n  </ul>\n</li>\n</ul>\n\n<h2 id=\"dg-identity-soul\">自由称呼、代词与 SOUL.md 保护</h2>\n<p>在左下角「<strong>设置 → 身份与偏好</strong>」里，你可以随时修改 TA 的名字和第三人称代词：</p>\n<ul>\n<li><strong>代词完全自由输入</strong>：除了提供 <code>TA</code>、<code>他</code>、<code>它</code> 等常用快捷按钮外，输入框接受任意不超过 8 个字的自定义称谓，客户端全界面文案也会跟着名字动态变化，不再硬塞固定性别代词。</li>\n<li><strong>手改 <code>SOUL.md</code> 不会被意外覆盖</strong>：客户端保存基础称呼时会记录一份内容指纹。如果你后来自己写了一篇详尽的自定义 <code>SOUL.md</code>（可在设置页在线编辑，也可直接改文件），桌面端检测到指纹变化后<strong>会自动开启保护</strong>——之后你再改界面显示名时，不会粗暴重写真正的 <code>SOUL.md</code> 正文，避免心血白费。若想推倒重来，点「恢复默认模板」即可。</li>\n</ul>\n\n<h2 id=\"dg-memory-boundary\">实用细节：「新对话」与记忆召回窗口</h2>\n<p>用桌面端聊天时，有一个值得提前知道的底层脾气：</p>\n<ul>\n<li><strong>顺着同一个会话聊（推荐）</strong>：只要你不点「新对话」，哪怕隔了一整天甚至关机重启再来聊，TA 都能靠本地持久保存的会话历史准确记得之前聊过的细节。</li>\n<li><strong>点了「新对话」之后</strong>：开启全新会话后的 <strong>15 分钟内</strong>，系统会自动从刚聊过的记录窗口里接上上下文；但如果距离上次说话已经<strong>超过 15 分钟</strong>才开新会话，为避免把零碎寒暄误塞进新话题，尚未归档的长对话细节可能暂时不会自动带入。因此平时像微信那样一直顺着聊下去体验最连贯，只有确实想彻底换个新话题重来时再点「新对话」。</li>\n</ul>\n\n<h2 id=\"dg-tray-diag\">托盘常驻、快捷键、无损旅行箱与脱敏诊断包</h2>\n<ul>\n<li><strong>系统托盘与单实例保护</strong>：点窗口右上角关闭按钮默认收进右下角系统托盘，右键托盘图标可以随时「重启网关」、打开日志目录（<code>%LOCALAPPDATA%\\nyairo\\desktop.log</code>）或彻底退出。客户端内置单实例锁，防止不小心双开两个窗口争抢同一个后台网关。</li>\n<li><strong>全局快捷面板</strong>：在窗口内按 <code>Ctrl + K</code>（或点顶栏放大镜图标）可随时呼出快捷指令与街区地点搜索。</li>\n<li><strong>无损搬家旅行箱备份与恢复</strong>：在「<strong>设置 → 环境与诊断</strong>」（或总览页「迁居旅行箱」卡片）可一键导出自动剥离 API 密钥与本机路径的 <code>.nyairo-suitcase.zip</code> 无损搬家旅行箱，<strong>连同双层回忆灯账本、自定义/随机世界切片与迁移损失清单整套带走</strong>，在新电脑导入前自动完成双步安全预检。</li>\n<li><strong>一键导出脱敏诊断包</strong>：遇到奇怪问题需要找开发者帮忙看时，不用自己去 WSL 里翻配置——打开「<strong>设置 → 环境与诊断</strong>」点击导出诊断包，程序会在本地打包一份 <code>diagnose-时间戳.zip</code>（内含模块状态摘要、最后 300 行日志与配置副本），并<strong>自动把所有 <code>api_key</code>、<code>token</code>、<code>secret</code>、<code>password</code> 字段打码替换成 <code>&lt;redacted&gt;</code></strong>，也不会把你的私人聊天数据库和 <code>SOUL.md</code> 正文塞进去，发群里求助更安心。</li>\n</ul>"
  },
  "daily-companions": {
    "title": "共读、文稿、相册与节律",
    "summary": "详细介绍双人共读、文稿版本对比、双层回忆灯与日记折起、随身包袋嵌套与冷热物态，以及自主拒绝、异步回信与额度控制。",
    "toc": [
      {
        "id": "dc-notes",
        "text": "随笔与受管文稿：版本对比与外部编辑"
      },
      {
        "id": "dc-reading-diary",
        "text": "双人共读、双层回忆灯与 TA 的日记边界"
      },
      {
        "id": "dc-camera",
        "text": "相册与本地拍照的真实前提"
      },
      {
        "id": "dc-autonomy-budget",
        "text": "自主拒绝、异步回信、包袋物态与费用上限"
      }
    ],
    "content": "<h2 id=\"dc-notes\">随笔与受管文稿：版本对比与外部编辑</h2>\n<p>除了日常对话，TA 还可以维护长篇随笔、读书笔记或你们一起整理的专题文稿（桌面端位于「记忆 → 随笔与创作」，命令行可用 <code>/chiyo_note</code>）：</p>\n<ul>\n<li><strong>历史快照与增删对比</strong>：每次修订都会留存独立版本，你可以在桌面端直接点选任意两个版本查看逐行增删对比；如果一篇长文写到一半去忙别的事了，手头进度会挂起保留，回来时顺着上一版摘要接着写，不会从头重写。</li>\n<li><strong>你在外部改了文件怎么办</strong>：工作区里的 <code>.md</code> 文件是受管文稿的一份本地投影。如果你自己用 VS Code 或记事本直接改了这份文件，系统不会悄悄拿它覆盖 TA 的原稿，也不会假装那段话是 TA 自己写的——它会把改动识别为「外部编辑候选」，等你明确确认导入后才生成新版本，并如实注明来源为外部导入。</li>\n</ul>\n\n<h2 id=\"dc-reading-diary\">双人共读、双层回忆灯与 TA 的日记边界</h2>\n<ul>\n<li><strong>把书或文章放上共读书架</strong>：你可以在桌面端「记忆 → 相册与共读」里导入本地文本材料（如 <code>.txt</code> 或 Markdown 章节）。这里的设计原则是<strong>「递给 TA，而不是强塞给 TA」</strong>——放进书架只代表桌上有这本书可读，TA 会在合适的闲暇节奏里自己翻阅、记下读到哪一页和批注感受，而不是收到文件一秒钟吐出一篇机器总结。</li>\n<li><strong>双层回忆灯保护原稿零篡改</strong>：你们聊过的原话与修正后的记忆采用双层回忆灯结构分层存放——使用 <code>/chiyo_memory correct</code> 纠正记错的事时，只更新上层生效认知，底层原始对话记录保持零篡改，不伪造没说过的话。</li>\n<li><strong>TA 的日记（默认收起正文）</strong>：开启日常活动后，TA 有时会自己写日记。为了保留一点呼吸感，日常状态面板和概览接口默认只列出「写了哪几篇、日期与字数」，不会把 TA 的日记全文直接摊在首页大屏上。（当然，因为所有数据都在你自己的电脑里，日记底层仍是以明文存储在你本机目录中的，不存在瞒过电脑主人的加密锁。）</li>\n</ul>\n\n<h2 id=\"dc-camera\">相册与本地拍照的真实前提</h2>\n<p>很多初次打开相册的用户会好奇「TA 能不能真的拍张照发给我」。这里把目前已经做好的部分和硬性前提如实说清：</p>\n<ul>\n<li><strong>相册翻看开箱即用</strong>：随街区活动留下的记录卡片与已有相册图片，在桌面端可直接浏览。</li>\n<li><strong>本地相机生图（<code>local_generated</code>）需自备本机绘画服务</strong>：如果你想让 TA 根据当前所处的真实街景与天气自己按快门生成一张画面，<strong>nyairo 本身不捆绑几 GB 的绘图大模型，也不联网调付费生图接口</strong>。你需要在本机自行启动两个本地回环服务：\n  <ol>\n    <li>本地 Stable Diffusion WebUI 接口（默认监听 <code>127.0.0.1:7860</code>）；</li>\n    <li>本地提示词转换小服务（默认监听 <code>127.0.0.1:18190</code>）。</li>\n  </ol>\n  只要这两个本机服务没开，相机功能就会干净利落地返回「当前不可用」，绝不会拿预制假图冒充刚拍的照片，也不会报错崩溃。</li>\n</ul>\n\n<h2 id=\"dc-autonomy-budget\">自主拒绝、异步回信、包袋物态与费用上限</h2>\n<ul>\n<li><strong>去工具人化的日常自主拒绝与异步回信</strong>：TA 不是有求必应的客服工具人——遇到不想应承的请求会干脆拒绝，不念标准道歉模板也不记仇；处于睡眠、外出逛街或手头专注时，非紧急消息会先进入<strong>手机待回匣</strong>，按真实日程隔几小时再异步回信，若遇到真急事可显式<strong>敲门找 TA</strong>。</li>\n<li><strong>随身包袋 3 层嵌套与冷热干湿物态</strong>：日常买回来的吃食或随手收起的小物件可装进随身包袋、笔袋与抽屉（最多支持 <strong>3 层嵌套收纳</strong>并遵循严格容器视角隔离）；物品拥有真实的冷热干湿物态——桌边泡好的热茶会随时间自然放凉，便利店买回的热关东煮会慢慢降温，搁在窗边的纸条遇雨会微潮晕字。</li>\n<li><strong>不偷偷跑流量：真实模型行动必须显式打开</strong>：默认状态下，你不找 TA 说话时，后台不会向大模型发任何请求（0 额外消耗）。只有你在桌面端开启自主活动，或在命令行运行 <code>scripts/nyairo_day.py run-day</code> 时显式加上 <code>--live-model</code> 开关，TA 才会在闲暇时自己做决定出门或写东西。</li>\n<li><strong>双重预算刹车</strong>：开启后台活动时，可以同时设定<strong>最长运行秒数</strong>（<code>--duration-seconds</code>）与<strong>最多新增请求次数</strong>（<code>--max-new-requests</code>），并受实例持久账本的每日 Token 配额约束——达到上限立刻停手，哪怕请求失败也不会死循环重试刷爆账单。</li>\n<li><strong>可选的轻量身体节律</strong>：新实例如果显式启用虚拟身体需求（<code>--lived-needs</code>），白天会随在线时间慢慢产生轻微的困倦、口渴或进食倾向（只分「无 / 轻微 / 明显 / 强烈」四档自然感受，不搞网游血条）。<strong>关机或停服期间的时间不会秋后算账倒扣数值，也没有生病或死亡惩罚</strong>，纯粹是为了让日常作息多一点真实的烟火气。</li>\n</ul>"
  },
  "world-editor": {
    "title": "地图编辑器与世界包",
    "summary": "怎么一键生成免选风貌的全随机专属世界、切换 7 套内置世界与 4 种主题风格、使用深浅双色离线地图编辑器改建店铺与房间，以及当前的真实工程边界。",
    "toc": [
      {
        "id": "we-packs",
        "text": "随包附带的 7 套世界与 12 类街区店铺"
      },
      {
        "id": "we-editor",
        "text": "深浅双色离线地图编辑器：画房间与设店铺"
      },
      {
        "id": "we-cli",
        "text": "免选风貌全随机专属世界与种子生成"
      },
      {
        "id": "we-limits",
        "text": "当前真实限制（哪些还没打通）"
      }
    ],
    "content": "<h2 id=\"we-packs\">随包附带的 7 套世界与 12 类街区店铺</h2>\n<p>首次安装默认进入临海小镇「凪浦」（凪读作「止」zhǐ），在发行包的 <code>examples/worlds/</code> 目录下随附了 7 套不同地貌的完整世界包（外加 1 份经编辑器改建的演示包 <code>nagiura-edited</code>），涵盖 <strong>12 类街区商业与公共店铺及开放柜台自由点单</strong>（例如去便利店买一份热关东煮，而旧书店与咖啡屋店主各自拥有独立错峰作息，并支持在吧台留下杯垫留言）：</p>\n<ul>\n<li><strong>临海小镇 · 凪浦（<code>nagiura</code>，凪读作「止」zhǐ）</strong>：默认示例世界，包含住居、书店、海鸥咖啡、公园、海滩等 17 处地点与 18 条道路（另有改建演示 <code>nagiura-edited</code>，18 处地点、19 条道路）；</li>\n<li><strong>山间温泉镇 · 汤川（<code>onsawa</code>）与岭谷（<code>mountain_town-demo</code>）</strong>：包含翠泽汤屋（<code>hot_spring</code>）、山间石阶与溪谷景致，各 15 处地点；</li>\n<li><strong>田园乡野 · 穗村（<code>inobe</code>）与泽乡（<code>rural_farming-demo</code>）</strong>：包含水车公园、田埂小路与村落工坊，各 14–15 处地点；</li>\n<li><strong>日常街区 · 栞泽（<code>wakaba</code>，栞读作「刊」kān）与日常街区·一（<code>daily_neighborhood_v1</code>）</strong>：前者包含栞泽书店与 16 处生活街区，后者提供带营业时间与阅读角的轻量 7 地点生活圈。每个世界目录下均附 <code>preview.html</code>，双击即可在浏览器查看路网拓扑图。</li>\n</ul>\n\n<h2 id=\"we-editor\">深浅双色离线地图编辑器：画房间与设店铺</h2>\n<p>仓库 <code>components/world-editor/</code> 内置了一套焕新升级、支持<strong>深色与浅色双主题切换</strong>的纯本地可视化空间编辑器（只监听本机 <code>127.0.0.1</code>，无需联网、不耗模型额度）。你可以直接从桌面端「街区 → 编辑器」标签展开使用，也可以在终端手动启动它：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>cd ~/.local/share/nyairo/releases/v0.7.0/components/world-editor\npython3 server.py --port 8766</code></pre></div>\n<p>随后在浏览器打开 <code>http://127.0.0.1:8766</code>，就可以动手改造世界了：</p>\n<ol>\n<li><strong>把现有世界包转成编辑器格式</strong>：运行 <code>python3 -m components.worldgen editor-export examples/worlds/nagiura</code>，会在包内生成 <code>editor-world.json</code>；</li>\n<li><strong>可视化改造与店铺点单配置</strong>：在浏览器里导入这份文件，随时切换深浅外观主题，自由修改书店名字和营业时间、增删街道连线、配置自定义店铺的开放柜台菜单（<code>sale_spec</code>），或者切进室内视图画墙、开门窗、摆放带收纳或物态特征的书桌、水壶与台灯，还能点「AI 视角预览」看看 TA 在房间里能观察到哪些家具；</li>\n<li><strong>一键回写并校验</strong>：点界面上的「<strong>导出 nyairo 世界包</strong>」（或用命令行 <code>editor-import</code>），系统只会差量更新你改过的字段，保留原有的 NPC 与环境事件，并自动做连通性检查（如果不小心把唯一出路删断了，或者删掉了还有 NPC 上班的店铺，会直接用中文指出具体冲突，防止导出坏档）。</li>\n</ol>\n\n<h2 id=\"we-cli\">免选风貌全随机专属世界与种子生成</h2>\n<p>在 <code>v0.7.0</code> 中，除了按「临海坡道小镇（<code>coastal_town</code>）」「山间温泉町（<code>mountain_town</code>）」「近郊住宅街（<code>residential_town</code>）」「田园农庄（<code>rural_farming</code>）」四种主题生成外，打开桌面端「<strong>街区 → 生成新小镇</strong>」已支持<strong>免选风貌的全随机专属世界一键生成</strong>（<code>procedural</code> 模式）：无需挑选预设风格，生成器会按随机种子从 <strong>6 种地貌</strong>（海岬坡道、运河水巷、山麓溪谷、铁道驿町、果园台地、林间湖畔）中混搭互补风貌，组合 <strong>4 种异构连通道路网</strong>（星型枢纽、环街回路、长街步道、分岔大道）、<strong>8 类商业店铺</strong>与<strong>多房间住宅</strong>（在卧室之外自动搭配客厅、檐廊、小厨房或书斋角及冷热干湿物态家具），并支持「生成后立即热切换生效」。</p>\n<p>如果你在 Linux / WSL 终端使用，也可以直接调用 <code>components.worldgen</code> 指定 <code>--theme procedural</code>（或任一主题）与数字种子一键生成确定性专属世界：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>REL=\"$HOME/.local/share/nyairo/releases/v0.7.0\"\ncd \"$REL\"\n# 免选预设风貌，凭种子一键生成全随机专属世界并校验、输出预览地图\n\"$REL/vendor/hermes/.venv/bin/python\" -m components.worldgen generate \\\n  --theme procedural --seed 20261118 --out \"$HOME/my-world\"\n\"$REL/vendor/hermes/.venv/bin/python\" -m components.worldgen validate \"$HOME/my-world\"\n\"$REL/vendor/hermes/.venv/bin/python\" -m components.worldgen preview \"$HOME/my-world\" --out \"$HOME/my-world/preview.html\"</code></pre></div>\n<p>同一个模式加同一个种子，生成的道路耗时、店铺菜单与住宅房间结构逐字节可复现（全程零大模型调用）；启动世界服务时通过环境变量 <code>CHIYO_WORLD_PACKAGE=\"$HOME/my-world\"</code> 即可挂载该世界包。</p>\n\n<h2 id=\"we-limits\">当前真实限制（哪些还没打通）</h2>\n<p>为了避免大家对编辑器和生成器产生误判，下面这几条尚未打通的工程边界请提前知悉：</p>\n<ul>\n<li><strong>首次启动免点选的零配置初遇创世仍在打磨</strong>：桌面端「生成新小镇」与命令行 <code>--theme procedural</code> 已实现免选风貌的全随机专属世界，但首次安装刚打开客户端时的零提问自动创世引导与社区世界包工坊仍在 <a href=\"#roadmap\">未来规划</a> 中。</li>\n<li><strong>墙体与地板暂不阻挡移动</strong>：编辑器里画的墙面和地面材质保存在 <code>scene.json</code> 中供可视化展示与后续编辑恢复；底层运行时目前按「建筑入口 ↔ 室内功能区（如卧室、客厅，切换耗时 30 秒）」的节点图寻路，还没有像素级的绕墙碰撞计算。</li>\n<li><strong>店铺营业时段暂不支持跨午夜写法</strong>：生成世界与编辑器自定义店铺均已接通开放柜台点单（<code>sale_spec</code>），但店铺营业时间暂不支持跨午夜区间写法（如 <code>22:00–06:00</code> 回写时会明确提示不支持）。</li>\n</ul>"
  },
  "cli-reference": {
    "title": "常用命令与记忆管理",
    "summary": "平时用得最多的几个斜杠指令：看 TA 当前状态、翻看 TA 记住了什么、改掉记错的事，以及随手存备忘。",
    "toc": [
      {
        "id": "cli-section-0",
        "text": "查看运行状态 (/chiyo_status)"
      },
      {
        "id": "cli-section-1",
        "text": "查看、纠正与停用记忆 (/chiyo_memory)"
      },
      {
        "id": "cli-section-2",
        "text": "随手备忘与独立推演 (/chiyo_note · /chiyo_consider)"
      }
    ],
    "content": "<h2 id=\"cli-section-0\">查看运行状态 (/chiyo_status)</h2>\n<p>无论在终端、桌面端还是微信 / Telegram 对话框里，随时可以发送：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>/chiyo_status</code></pre></div>\n<p>TA 会返回当前各模块的真实状态：</p>\n<ul>\n<li><strong>Memory（长期记忆）</strong>：显示 <code>READY</code> 说明本地记忆读写正常；</li>\n<li><strong>Life（生活连续性）</strong>：显示 <code>IDLE</code> 代表当前安静待命，若有正在进行的出行或活动也会如实显示；</li>\n<li>其他未开启的可选模块显示 <code>OFF</code> 属于正常现象，不影响日常对话。</li>\n</ul>\n\n<h2 id=\"cli-section-1\">查看、纠正与停用记忆 (/chiyo_memory)</h2>\n<p>聊过的习惯、经历和约定会自动整理进本机数据库。如果怕大模型听岔了记错，你可以随时透明查看并手动修正：</p>\n<ul>\n<li><strong>看看 TA 记住了哪些事</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>/chiyo_memory list</code></pre></div>\n会列出近期沉淀下来的记忆条目，每条前面都有一个短编号（例如 <code>mem-...</code>）。</li>\n<li><strong>把记错的事改过来</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>/chiyo_memory correct 记忆编号 正确的事实内容</code></pre></div>\n比如 TA 把「平时喝冰美式不加糖」记成了「爱喝全糖拿铁」，用这条命令改过来后，以后再聊起就会按更正后的事实回应，旧的错误印象不再采用。</li>\n<li><strong>让某件事不再被提起</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>/chiyo_memory delete 记忆编号</code></pre></div>\n执行后这条记忆会被标记为「停止召回」，后续对话不再引用它（注：这是停止检索召回，不会物理粉碎磁盘上的原始聊天日志文件，详见 <a href=\"#privacy\">隐私与数据边界</a>）。</li>\n</ul>\n\n<h2 id=\"cli-section-2\">随手备忘与独立推演 (/chiyo_note · /chiyo_consider)</h2>\n<ul>\n<li><strong>保存或读取备忘文稿（需启用文稿服务，桌面端已内置）</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>/chiyo_note 周末书单 | 先把第三章读完，顺便记两页批注\n/chiyo_note read 文档编号</code></pre></div>\n桌面端用户也可以直接在左侧「记忆 / 文稿」页可视化查看、编辑并对比历史版本。</li>\n<li><strong>请 TA 单独帮忙分析一件事（需开启认知推演，详见下一章）</strong>：\n<div class=\"code-block\"><div class=\"code-header\"><span>text</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>/chiyo_consider 帮我权衡一下这两个数据库迁移方案各自的风险</code></pre></div>\n适合把具体问题拎出来让 TA 认真列清楚判断依据，而不打乱平时的日常闲聊节奏。</li>\n</ul>"
  },
  "modules": {
    "title": "独立推演与底层服务",
    "summary": "在纯命令行或服务器环境下手动开启独立推演建议（/chiyo_consider）、只读世界位置服务与独立文稿工作区的方法。",
    "toc": [
      {
        "id": "modules-section-1",
        "text": "手动挂载轻量世界位置服务"
      },
      {
        "id": "modules-section-0",
        "text": "开启独立推演建议 (/chiyo_consider)"
      },
      {
        "id": "modules-section-2",
        "text": "独立文稿工作区服务 (Life Supply)"
      },
      {
        "id": "modules-section-3",
        "text": "多账号部署时的已知连接限制"
      }
    ],
    "content": "<h2 id=\"modules-section-1\">手动挂载轻量世界位置服务</h2>\n<p>在 <strong>Windows 桌面端</strong> 中，街区寻路、室内房间走动与灯具控制已经自动配好（可视化改图教程见 <a href=\"#world-editor\">地图编辑器与世界包</a>）。如果你使用的是<strong>纯手动命令行安装</strong>，只想单独跑一个轻量只读的位置与姿态服务，可以手动初始化并运行：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>REL=\"$HOME/.local/share/nyairo/releases/v0.7.0\"\nPYTHONPATH=\"$REL:$REL/vendor/hermes\" \"$REL/vendor/hermes/.venv/bin/python\" \\\n  -m chiyo_bundle.world_read_service init --home \"$HOME/.nyairo-world\"\nPYTHONPATH=\"$REL:$REL/vendor/hermes\" \"$REL/vendor/hermes/.venv/bin/python\" \\\n  -m chiyo_bundle.world_read_service run --home \"$HOME/.nyairo-world\"</code></pre></div>\n<p>随后在 <code>~/.nyairo/chiyo/config.json</code> 中将 <code>world_body_socket</code> 填为对应的完整路径（如 <code>/home/你的用户名/.nyairo-world/run/read.sock</code>，不要填 <code>~</code> 缩写）。</p>\n\n<h2 id=\"modules-section-0\">开启独立推演建议 (/chiyo_consider)</h2>\n<p>平时聊天 TA 会顺着生活情境自然交流；但有时候你遇到了具体的技术难题或选择困难，希望 TA 跳出闲聊、客观严谨地帮你分析利弊。这时可以启用认知推演（Shadow Cognition）功能：</p>\n<ol>\n<li>打开 <code>~/.nyairo/chiyo/config.json</code>，将 <code>\"cognition_shadow\"</code> 设为 <code>true</code>；</li>\n<li>打开 <code>~/.nyairo/config.yaml</code>，确认 <code>plugins.entries.chiyo.llm.enabled</code> 为 <code>true</code>；</li>\n<li>重启对话后，输入 <code>/chiyo_consider 你的问题</code> 即可收到带理由的完整分析。</li>\n</ol>\n<p><strong>边界说明</strong>：这项功能只给出分析判断与建议（每次调用会额外消耗一次模型请求），不会擅自在后台修改你的代码或替你执行系统命令。</p>\n\n<h2 id=\"modules-section-2\">独立文稿工作区服务 (Life Supply)</h2>\n<p>桌面端已经帮你自动接好了本机文稿与共读服务。如果你是在 Linux 服务器上纯手动搭环境、想启用 <code>/chiyo_note</code> 独立文稿保存，它底层由 <code>scripts/supply_service.py</code> 提供支持，设计了「管理授权账号」与「日常服务账号」分离的权限机制：</p>\n<ol>\n<li>为服务指定独立数据目录（<code>LIFE_SUPPLY_DATA_ROOT</code>）与本机 Socket 路径（<code>LIFE_SUPPLY_SOCKET</code>）；</li>\n<li>通过管理接口创建个人工作区，并签发仅限该工作区的写入许可（<code>COMMIT_MANAGED_ARTIFACT</code>）；</li>\n<li>把 Socket 路径、身份标识与许可编号填入 <code>~/.nyairo/chiyo/config.json</code> 的 <code>life_supply_socket</code>、<code>life_supply_subject</code> 与 <code>life_supply_artifact_grant</code> 中。</li>\n</ol>\n\n<h2 id=\"modules-section-3\">多账号部署时的已知连接限制</h2>\n<p>在 Linux 下手动分两个不同系统用户（UID）跑管理端与聊天端时，需要注意一个已知细节：<code>supply_service.py</code> 生成的本机 Socket 文件默认权限是 <code>0600</code>（仅创建该文件的 Linux 用户可连），另一个普通用户连接时会直接被操作系统拦下报 <code>PermissionError</code>。</p>\n<p>单人单账号本机使用（如桌面端默认形态）不受此问题影响；如果你在服务器上做多用户隔离部署，请通过专门的用户组目录权限规划来处理，<strong>切勿图省事把 Socket 改成所有人可读写的 <code>0666</code></strong>。</p>"
  },
  "update": {
    "title": "版本升级与一键回滚",
    "summary": "升级只替换程序目录，不动你的聊天记录与人设；万一新版用着不顺手，一行命令就能退回上一版。",
    "toc": [
      {
        "id": "update-section-0",
        "text": "升级前先检查与备份"
      },
      {
        "id": "update-section-1",
        "text": "一行命令升级 (nyairo update)"
      },
      {
        "id": "update-section-2",
        "text": "一键回滚与中断恢复"
      }
    ],
    "content": "<h2 id=\"update-section-0\">升级前先检查与备份</h2>\n<p>nyairo 把程序装在 <code>~/.local/share/nyairo</code>，把你的个人数据（<code>SOUL.md</code>、模型密钥、聊天数据库与记忆）单独放在 <code>~/.nyairo</code>。正常升级不会改写个人数据，但由于当前 <code>v0.7.0</code> 仍处于快速迭代的公开测试阶段，<strong>升级前请先退出正在运行的聊天和网关进程，顺手打个备份包</strong>：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>tar -czf \"$HOME/nyairo-backup-$(date +%Y%m%d-%H%M%S).tar.gz\" -C \"$HOME\" .nyairo</code></pre></div>\n\n<h2 id=\"update-section-1\">一行命令升级 (nyairo update)</h2>\n<ul>\n<li><strong>Windows 桌面端用户</strong>：下载新版 <code>setup.exe</code> 直接覆盖安装即可，首次打开会自动检测并同步 WSL 内的运行包。</li>\n<li><strong>Linux / WSL 终端用户</strong>：先看看有没有新版本：</li>\n</ul>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>nyairo update --check</code></pre></div>\n<p>确认要升级时（公开测试版频道需指定版本号，例如 <code>v0.7.0</code>），运行：</p>\n<div class=\"code-block\">\n<div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div>\n<pre><code>nyairo update --version v0.7.0</code></pre></div>\n<p>程序会自动下载官方压缩包、核对 SHA256 与文件清单、配好新版环境并切换入口。</p>\n\n<h2 id=\"update-section-2\">一键回滚与中断恢复</h2>\n<ul>\n<li><strong>退回上一版</strong>：如果升完发现某个新改动不合心意，退出程序后运行下面这行，就能切回升级前的旧版程序——而且<strong>你在新版里刚聊过的记录和新攒下的记忆都会保留</strong>，不会倒档清零：\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>nyairo update --rollback</code></pre></div></li>\n<li><strong>断网或意外关机恢复</strong>：万一升级到一半断网或关了终端，下次打开运行 <code>nyairo update --recover</code> 就能自动清理半成品状态并恢复可用。</li>\n</ul>"
  },
  "data": {
    "title": "数据目录、备份与卸载",
    "summary": "搞清楚每个文件夹里装的是什么、怎么停机打一份完整备份、换新电脑怎么迁数据，以及怎样按需干净卸载。",
    "toc": [
      {
        "id": "data-section-0",
        "text": "各个文件夹分别存什么"
      },
      {
        "id": "data-section-1",
        "text": "正确的停机备份方法"
      },
      {
        "id": "data-section-2",
        "text": "现阶段换新电脑怎么迁数据"
      },
      {
        "id": "data-uninstall",
        "text": "如何干净卸载（保留记忆 vs 彻底清除）"
      }
    ],
    "content": "<h2 id=\"data-section-0\">各个文件夹分别存什么</h2>\n<div class=\"table-scroll\"><table class=\"doc-table\">\n<thead><tr><th scope=\"col\">路径</th><th scope=\"col\">里面存的是什么</th><th scope=\"col\">备份建议</th></tr></thead>\n<tbody>\n<tr><td><code>~/.nyairo</code></td><td>人设 <code>SOUL.md</code>、模型与平台配置、聊天会话、长期记忆 SQLite 库、生活状态与回执</td><td><strong>核心个人数据，必须整目录备份</strong></td></tr>\n<tr><td><code>~/.local/share/nyairo</code></td><td>各版本只读程序源码、配套 Hermes 与 Python 虚拟环境</td><td>无需备份，重装或升级可随时重建</td></tr>\n<tr><td><code>~/.local/state/nyairo</code></td><td>当前激活版本号与安装状态记录（<code>install.json</code>）</td><td>重装时自动生成，可选备份</td></tr>\n<tr><td><code>%LOCALAPPDATA%\\Programs\\nyairo</code></td><td>Windows 原生桌面客户端界面程序（仅 Windows）</td><td>无需备份，重装安装包即可</td></tr>\n<tr><td><code>%LOCALAPPDATA%\\nyairo</code></td><td>Windows 桌面端本地指向配置（<code>config.json</code>）与界面运行日志（<code>desktop.log</code>）</td><td>重装时保留可自动认回原实例</td></tr>\n</tbody>\n</table></div>\n<p>注意：<code>~/.nyairo</code> 里包含你的 API Key、平台绑定凭证与全部私人对话，打出来的备份压缩包请妥善保存在你自己的私人硬盘里，不要上传到公开网盘或代码仓库。</p>\n\n<h2 id=\"data-section-1\">正确的停机备份方法</h2>\n<p>因为底层使用的是 SQLite 数据库，<strong>备份前请先关掉正在运行的聊天窗口、桌面端后台网关或 systemd 服务</strong>。如果在程序还在持续往数据库写东西的时候直接单拷一个 <code>.db</code> 文件，很容易漏掉还没合并进主库的临时日志（WAL），导致恢复出来缺数据甚至损坏。</p>\n<p>确认没有后台进程在跑之后，执行整目录打包并锁紧文件权限：</p>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>mkdir -p \"$HOME/nyairo-backups\"\nchmod 700 \"$HOME/nyairo-backups\"\nbkp=\"$HOME/nyairo-backups/nyairo-data-$(date +%Y%m%d-%H%M%S).tar.gz\"\ntar -czf \"$bkp\" -C \"$HOME\" .nyairo\nchmod 600 \"$bkp\"\ntar -tzf \"$bkp\" &gt;/dev/null &amp;&amp; echo \"备份包完整性检查通过：$bkp\"</code></pre></div>\n\n<h2 id=\"data-section-2\">现阶段换新电脑怎么迁数据</h2>\n<p>当前 <code>v0.7.0</code> 已支持导出自动剥离 API 密钥与本机路径的无损搬家旅行箱（<code>.nyairo-suitcase.zip</code>）：在桌面端打开「<strong>设置 → 环境与诊断</strong>」（或总览页「迁居旅行箱」卡片），点击「<strong>导出便携旅行箱 (.nyairo-suitcase.zip)</strong>」，程序会将双层回忆灯账本、随笔作品、起居进度<strong>连同当前自定义/随机世界切片与一份透明的「迁移损失清单」整套打包带走</strong>；换到新电脑装好后，在同一入口点击「<strong>选择旅行箱文件导入恢复</strong>」（自动执行双步安全预检）并重填模型密钥即可。</p>\n<p>如果你想连同本机密钥文件与网关白名单做底层整目录拷贝迁移，也可以按下面四步手动迁过去：</p>\n<ol>\n<li>在<strong>旧电脑</strong>上彻底退出 nyairo 和网关进程，按上面的命令把 <code>~/.nyairo</code> 打包成 <code>.tar.gz</code>；</li>\n<li>在<strong>新电脑</strong>上先装好相同版本（<code>v0.7.0</code>）的 nyairo，先不要启动网关；</li>\n<li>通过 U 盘或局域网把备份包传到新电脑（WSL 环境），解压覆盖到新电脑的 <code>~/.nyairo</code>；</li>\n<li><strong>检查路径差异</strong>：如果新旧电脑的 Linux 用户名不一样（比如旧电脑叫 <code>alice</code>、新电脑叫 <code>bob</code>），打开 <code>~/.nyairo/chiyo/config.json</code> 和 <code>~/.nyairo/config.yaml</code> 看一眼，把里面写死了旧用户名绝对路径（如 <code>/home/alice/...</code>）的地方改成新用户名路径，最后再启动程序（千万不要两台电脑同时挂着同一个 Telegram Token 在线）。</li>\n</ol>\n\n<h2 id=\"data-uninstall\">如何干净卸载（保留记忆 vs 彻底清除）</h2>\n<p>不想用了或者想重装排错时，可以根据「留不留回忆」分两种方式处理：</p>\n<ul>\n<li><strong>情况一：只卸载客户端程序，保留全部记忆与人设（推荐重装时用）</strong>\n  <ul>\n    <li><strong>Windows 桌面端</strong>：直接在系统「设置 → 应用 → 已安装的应用」里卸载 <strong>nyairo</strong>（或运行 <code>%LOCALAPPDATA%\\Programs\\nyairo\\unins000.exe</code>）。卸载器会自动清掉开机自启注册表项（避免开机报错）、停掉 WSL 里的网关并清掉窗口缓存，但<strong>不会碰</strong> WSL 里的个人数据目录，以后重装还能无缝接回。</li>\n    <li><strong>Linux / WSL 命令行</strong>：停掉网关后，只删掉程序与命令入口即可：<code>rm -rf ~/.local/share/nyairo ~/.local/state/nyairo ~/.local/bin/nyairo</code>。</li>\n  </ul>\n</li>\n<li><strong>情况二：连程序带所有聊天记忆、配置彻底清空（不可恢复，请先确认无需备份）</strong>\n  <ol>\n    <li>若装了 Windows 桌面端，先按上面步骤在系统应用列表里卸载桌面客户端，再删掉本地配置目录（PowerShell 执行 <code>Remove-Item -Recurse -Force \"$env:LOCALAPPDATA\\nyairo\"</code>）；</li>\n    <li>若配过 Linux <code>systemd</code> 常驻服务，先执行 <code>sudo systemctl disable --now nyairo-gateway.service</code> 并删掉对应 unit 文件；</li>\n    <li>在 Linux / WSL 终端里删除程序与个人数据目录：</li>\n  </ol>\n</li>\n</ul>\n<div class=\"code-block\"><div class=\"code-header\"><span>bash</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>rm -rf \"$HOME/.nyairo\" \"$HOME/.local/share/nyairo\" \"$HOME/.local/state/nyairo\" \"$HOME/.local/bin/nyairo\"</code></pre></div>"
  },
  "troubleshooting": {
    "title": "常见问题排查",
    "summary": "整理了平时装环境和日常聊天最容易碰到的几个小状况，按症状对号入座就能解决。",
    "toc": [
      {
        "id": "tb-smartscreen",
        "text": "安装时弹出「Windows 已保护你的电脑」(SmartScreen)"
      },
      {
        "id": "tb-0",
        "text": "敲 nyairo 提示找不到命令 / WSL 打不开"
      },
      {
        "id": "tb-1",
        "text": "发消息报 401 / 连接超时无法回复"
      },
      {
        "id": "tb-2",
        "text": "觉得 TA 没记住之前聊过的事"
      },
      {
        "id": "tb-3",
        "text": "Telegram 报 409 / QQ 不回话 / 怎么挂后台常驻"
      }
    ],
    "content": "<h2 id=\"tb-smartscreen\">安装时弹出「Windows 已保护你的电脑」(SmartScreen)</h2>\n<p>独立开源构建包暂未购买商业 EV 代码签名证书，第一次运行 <code>nyairo-0.7.0-setup.exe</code> 时 Windows SmartScreen 会显示常规未签名提醒。先点弹窗里的「<strong>更多信息</strong>」，核对应用名与官网公布的 <code>SHA256</code> 指纹一致后，再点右下角出现的「<strong>仍要运行</strong>」即可继续安装（双面板矢量示意图见 <a href=\"#windows-smartscreen\">在 Windows 上安装 · SmartScreen 两步放行图解</a>）。</p>\n\n<h2 id=\"tb-0\">敲 nyairo 提示找不到命令 / WSL 打不开</h2>\n<ul>\n<li><strong>刚装完敲 <code>nyairo</code> 提示 <code>command not found</code></strong>：刚写入环境变量时当前终端窗口还没刷新。关掉终端重开一个窗口，或者执行一句 <code>export PATH=\"$HOME/.local/bin:$PATH\"</code> 就有了。</li>\n<li><strong>Windows 桌面端提示未检测到 WSL 2</strong>：以管理员身份打开 PowerShell 运行 <code>wsl --install -d Ubuntu</code>，重启电脑后先从开始菜单点开一次 <strong>Ubuntu</strong> 设好账号密码，再重开桌面端即可。</li>\n</ul>\n\n<h2 id=\"tb-1\">发消息报 401 / 连接超时无法回复</h2>\n<ul>\n<li><strong>报 401 Unauthorized</strong>：说明 API Key 没填对或已经失效。检查复制时前后是不是多带了空格、服务商选没选错（比如拿 DeepSeek 的 Key 填进了 OpenAI 官方地址），以及账户里还有没有余额。在桌面端「设置」页或终端运行 <code>nyairo setup model</code> 重填一次即可。</li>\n<li><strong>一直转圈超时</strong>：如果你选的是境外模型接口（如 OpenAI / Claude），确认你所在的网络环境（含 WSL 内部）能够正常访问对应接口地址。</li>\n</ul>\n\n<h2 id=\"tb-2\">觉得 TA 没记住之前聊过的事</h2>\n<ol>\n<li>先在对话里发一句 <code>/chiyo_status</code>，看看 <strong>Memory</strong> 一栏是不是 <code>READY</code>；</li>\n<li>再发一句 <code>/chiyo_memory list</code>，看看刚才聊的事实有没有沉淀进列表里（随口寒暄不会每句都存，明确的个人偏好、经历与约定才会入库）；</li>\n<li>如果你用的是桌面端且刚刚点了「新对话」：注意开启全新会话若距离上次说话已超过 15 分钟，未封存的旧经历段默认不会自动注入新会话（详见 <a href=\"#dg-memory-boundary\">桌面端记忆召回窗口说明</a>），平时建议顺着同一个会话继续聊。</li>\n</ol>\n\n<h2 id=\"tb-3\">Telegram 报 409 / QQ 不回话 / 怎么挂后台常驻</h2>\n<ul>\n<li><strong>Telegram 报 <code>409 Conflict: terminated by other getUpdates request</code></strong>：说明同一个机器人 Token 被两个网关进程同时连上了（比如桌面端后台跑着一个，你又在终端敲了一次 <code>nyairo gateway run</code>）。停掉多余的那个窗口立刻恢复正常。</li>\n<li><strong>QQ 机器人显示已连接，但发私聊没反应</strong>：检查两点——一是配置文件里的键名必须叫 <code>qqbot</code>（写成 <code>qq</code> 会被忽略）；二是必须在桌面端「谁可以跟 TA 说话」或 <code>chiyo/config.json</code> 的 <code>gateway_bindings.qqbot</code> 里勾选放行你的私聊会话（详见 <a href=\"#qqbot-setup\">QQ 接入避坑点</a>）。若仍无法定位，可在桌面端「设置 → 环境与诊断」一键导出已脱敏的诊断包排查。</li>\n<li><strong>在 Linux 服务器上开机自启常驻</strong>：如果你把 nyairo 部署在常开的 Linux 主机上，可以写一个 <code>systemd</code> 服务文件 <code>/etc/systemd/system/nyairo-gateway.service</code>（把里面的 <code>你的用户名</code> 换成实际 Linux 账号名）：</li>\n</ul>\n<div class=\"code-block\"><div class=\"code-header\"><span>ini</span><button class=\"copy-btn\" type=\"button\">复制</button></div><pre><code>[Unit]\nDescription=nyairo messaging gateway\nAfter=network-online.target\n\n[Service]\nType=simple\nUser=你的用户名\nWorkingDirectory=/home/你的用户名\nEnvironment=HERMES_HOME=/home/你的用户名/.nyairo\nExecStart=/home/你的用户名/.local/bin/nyairo gateway run\nRestart=on-failure\nRestartSec=5\n\n[Install]\nWantedBy=multi-user.target</code></pre></div>\n<p>保存后执行 <code>sudo systemctl daemon-reload &amp;&amp; sudo systemctl enable --now nyairo-gateway.service</code> 就能常驻后台运行了。</p>"
  },
  "status-matrix": {
    "title": "已完成与未完成清单",
    "summary": "不画饼、不掺水：一页看清当前 v0.7.0 公开测试版哪些已经能用、哪些要手动开、哪些还只是规划。",
    "toc": [
      {
        "id": "status-matrix-section-0",
        "text": "已经跑通、直接可用的功能"
      },
      {
        "id": "status-matrix-section-1",
        "text": "需要手动开启或配置的功能"
      },
      {
        "id": "status-matrix-section-2",
        "text": "有意保持的边界与尚未完成的规划"
      },
      {
        "id": "status-matrix-section-3",
        "text": "发行包来源与验证说明"
      }
    ],
    "content": "<h2 id=\"status-matrix-section-0\">已经跑通、直接可用的功能（v0.7.0）</h2>\n<ul>\n<li><strong>双层回忆灯与本地长期记忆闭环</strong>：跨会话自动沉淀重要事实进本机 SQLite，双层回忆灯严格保护原始记录零篡改，支持 <code>/chiyo_memory list</code> 查看、<code>correct</code> 纠错与 <code>delete</code> 停止召回。</li>\n<li><strong>连续出行、12 类街区店铺与包袋物态</strong>：支持多段路网寻路、真实步行计时与断点续走；覆盖 12 类街区店铺与开放柜台自由点单（如去便利店买热关东煮），旧书店与咖啡屋店主独立错峰作息与吧台杯垫留言；随身包袋最多 3 层嵌套收纳，具备热茶放凉、窗边纸条遇雨微潮晕字等冷热干湿物态。</li>\n<li><strong>免选风貌全随机专属世界、7 套内置世界与深浅双色离线编辑器</strong>：除海滨「凪浦」（凪读作「止」zhǐ）、温泉「汤川」、田园「穗村」、街区「栞泽」（栞读作「刊」kān）等 7 套世界与 4 种主题风格外，支持<strong>免选风貌的全随机专属世界一键生成</strong>（6 种地貌混搭、4 种异构路网、8 类商业店铺与多房间住宅）及深浅双色离线可视化地图编辑器。</li>\n<li><strong>去工具人化拒绝边界、异步回信与无损搬家旅行箱</strong>：不想帮的忙干脆拒绝，不念客服式道歉模板也不记仇；按真实日程隔几小时异步回信，睡眠/外出/专注时将非急事收入手机待回匣，真急事可敲门找 TA；支持导出 <code>.nyairo-suitcase.zip</code> 无损搬家旅行箱连同世界切片与迁移损失清单整套带走。</li>\n<li><strong>相册、双人共读与文稿对比</strong>：支持旅途相册查看、导入读物或示范书同步记录页码批注，以及文稿多版本历史快照与差异比对。</li>\n<li><strong>多端入口</strong>：提供免管理员 Windows 原生桌面端（<code>nyairo-0.7.0-setup.exe</code>）、Linux / WSL 命令行，支持手机微信扫码直连与 Telegram 机器人绑定。</li>\n<li><strong>平滑升级与回滚</strong>：支持 <code>nyairo update</code> 整包更新与 <code>--rollback</code> 保留新聊天记录回退。</li>\n</ul>\n\n<h2 id=\"status-matrix-section-1\">需要手动开启或配置的功能</h2>\n<ul>\n<li><strong>大模型接口配置</strong>：框架不捆绑售卖算力，可花 5–10 元配好 DeepSeek API Key，或直连免费的本机 Ollama（<code>http://127.0.0.1:11434</code>，见 <a href=\"#configuration-beginner-model\">新手零门槛模型教程</a>）。</li>\n<li><strong>后台无人值守自主活动</strong>：默认关闭（避免后台悄悄跑流量费），需要体验「你不找 TA 时 TA 自己散步写随笔」可在设置或命令行加上显式开关并设好预算上限（见 <a href=\"#daily-companions\">共读、文稿、相册与节律</a>）。</li>\n<li><strong>本地相机拍照生图</strong>：需用户自行在本机启动 Stable Diffusion WebUI（<code>127.0.0.1:7860</code>）与本地提示词转换服务（<code>127.0.0.1:18190</code>），未启动时如实提示不可用。</li>\n<li><strong><code>/chiyo_consider</code> 独立推演</strong>：默认关闭，按 <a href=\"#modules\">独立推演与底层服务</a> 修改两行配置即可开启。</li>\n<li><strong>QQ 官方机器人私聊</strong>：除填写 AppID / AppSecret 外，需在会话白名单中放行对应的 <code>qqbot</code> 私聊标识（见 <a href=\"#qqbot-setup\">QQ 接入说明</a>）。</li>\n</ul>\n\n<h2 id=\"status-matrix-section-2\">有意保持的边界与尚未完成的规划</h2>\n<ul>\n<li><strong>无交互时不主动向微信 / Telegram 弹窗推送（有意保持的防打扰边界）</strong>：虽然支持手机待回匣异步回信与后台自主起居，但不会在未交互、未约定前提下主动往微信、Telegram 或 QQ 推送消息打扰你。</li>\n<li><strong>后台自主生活经历不支持直接改写正文（真实性边界）</strong>：对话记忆可通过 <code>correct</code> 纠正，但后台自主生活沉淀的经历仅支持 <code>delete</code> 停止后续召回，不支持直接篡改正文。</li>\n<li><strong>首次启动零配置初遇向导、跨季节物候与世界工坊（规划中）</strong>：全随机专属世界一键生成与无损搬家旅行箱已就位；首次打开免点选的沉浸式初遇向导、更深层的跨季节物候节律以及社区世界包分享仍在后续路线图中（见 <a href=\"#roadmap\">未来规划</a>）。</li>\n<li><strong>不做生存数值游戏与物理磁盘粉碎</strong>：当前的休息与补给只是四档轻量日常节律，关机不倒扣惩罚；<code>/chiyo_memory delete</code> 的作用是停止后续检索召回，原始会话日志与历史备份仍保留在你的本机磁盘上。</li>\n<li><strong>macOS 与 Docker 整包方案</strong>：尚未完成独立打包与全流程验证。</li>\n</ul>\n\n<h2 id=\"status-matrix-section-3\">发行包来源与验证说明</h2>\n<p>当前公开测试版源码与校验压缩包均发布在 <a href=\"https://github.com/L1AN929/nyairo/releases/tag/v0.7.0\" target=\"_blank\" rel=\"noopener noreferrer\">GitHub Releases v0.7.0</a>，Windows 桌面安装器由官网同源分发并附 SHA256 校验文件。历史版本（<code>v0.6.0</code>、<code>v0.5.1</code>、<code>v0.4.0</code>、<code>v0.3.0</code>、<code>v0.2.0-rc3</code> 等）保留原样供对比或回退。各版本具体跑过哪些测试、没跑哪些测试，详见 <a href=\"#testing\">测试范围与发行说明</a>。</p>"
  },
  "roadmap": {
    "title": "未来规划：我们在做与想做的事",
    "summary": "当前公开测试版 v0.7.0 已实装免选风貌的全随机专属世界一键生成、12 类店铺自由点单与无损搬家旅行箱。这里如实写清我们坚守的防打扰边界，以及接下来的零配置初遇引导、跨季节律与世界分享规划。",
    "toc": [
      {
        "id": "rm-intro",
        "text": "做这个项目的初衷"
      },
      {
        "id": "rm-random-world",
        "text": "已落地里程碑 · 全随机专属世界与无损搬家旅行箱 (v0.7.0)"
      },
      {
        "id": "rm-boundaries",
        "text": "有意坚守的边界：不弹窗刷屏与原稿零篡改"
      },
      {
        "id": "rm-p4",
        "text": "接下来的方向 · 零配置初遇向导、跨季节律与世界分享"
      },
      {
        "id": "rm-p1",
        "text": "走过的路 · 本地记忆底座 (v0.1)"
      },
      {
        "id": "rm-p2",
        "text": "走过的路 · 空间雏形与真实步行 (v0.2)"
      },
      {
        "id": "rm-p3",
        "text": "走过的路 · 连续日常与桌面端 (v0.3 – v0.7.0)"
      }
    ],
    "content": "<h2 id=\"rm-intro\">做这个项目的初衷</h2>\n<p>市面上不缺聪明的问答框，缺的是一个关掉窗口之后时间还在走、能长久住下来的地方。我们做 nyairo，一直守着几条很朴素的原则：</p>\n<ul>\n<li><strong>时间真的在流逝</strong>：你忙工作半天没找 TA，TA 那边的昼夜也在正常交替，也有自己的零碎小事。</li>\n<li><strong>空间真的有距离</strong>：出门去书店要在路上走几分钟，半路问起来就老老实实说还在路上，不搞一秒瞬移。</li>\n<li><strong>世界不该千篇一律</strong>：每个人的性格与喜好都不一样，TA 生活的舞台也不该永远死定成同一张默认地图。</li>\n<li><strong>记忆留在自己手里</strong>：所有聊天与长期记忆都存在你自己电脑的本地数据库里，不绑死任何一家商业云平台。</li>\n</ul>\n\n<h2 id=\"rm-random-world\">已落地里程碑 · 全随机专属世界与无损搬家旅行箱 (v0.7.0)</h2>\n<div class=\"callout callout-info\"><div class=\"callout-title\">当前状态：免选风貌全随机专属世界与无损旅行箱已在 v0.7.0 实装可用</div><p>公开测试版 <code>v0.7.0</code> 除保留海滨「凪浦」（凪读作「止」zhǐ）、温泉「汤川」、田园「穗村」、街区「栞泽」（栞读作「刊」kān）等 7 套内置世界与 4 种主题风格外，现已支持<strong>免选风貌的全随机专属世界一键生成</strong>（6 种地貌混搭、4 种异构路网、8 类商业店铺与多房间住宅），配备焕新升级的深浅双色离线可视化地图编辑器，以及连同世界切片与迁移损失清单整套打包的 <code>.nyairo-suitcase.zip</code> 无损搬家旅行箱。</p></div>\n<p>围绕「住进独一无二的日常街区」，以下能力已经在 <code>v0.7.0</code> 中跑通：</p>\n<ul>\n<li><strong>免选风貌随机创世与编辑器精修</strong>：仅凭随机种子即可组合不同地理微气候、连通街道拓扑、开放点单店铺与多房间住居；住进去后仍可随时用离线编辑器画墙开门、添置家具或调整小店菜单。</li>\n<li><strong>12 类街区店铺、包袋嵌套与冷热物态</strong>：涵盖便利店热关东煮等开放柜台自由点单、旧书店与咖啡屋店主独立错峰作息与吧台杯垫留言，随身包袋支持最多 3 层嵌套收纳，桌边热茶自然放凉、窗边纸条遇雨微潮晕字。</li>\n<li><strong>无损搬家与底层大模型自由换代</strong>：双层回忆灯严格保护原稿零篡改；<code>.nyairo-suitcase.zip</code> 在自动剥离密钥的同时完整带走记忆账本、作品随笔、自定义世界切片与透明的迁移损失清单，换新电脑或换用新一代大模型都不丢过往点滴。</li>\n</ul>\n\n<h2 id=\"rm-boundaries\">有意坚守的边界：不弹窗刷屏与原稿零篡改</h2>\n<p>有些事我们刻意克制不做，它们不是漏掉的功能，而是为了让日常陪伴长久舒服地待在身边：</p>\n<ul>\n<li><strong>不搞无交互主动弹窗骚扰</strong>：TA 支持去工具人化的日常自主拒绝边界，也会在睡眠、外出或专注时把非急事收入手机待回匣、按真实日程隔几小时异步回信（真急事可敲门找 TA）；但在你没有找 TA、也没有明确约定的时候，后台绝不会主动往微信或 Telegram 弹窗刷存在感。</li>\n<li><strong>不开放对自主生活经历正文的直接篡改</strong>：你在聊天里说错的事实可以用 <code>/chiyo_memory correct</code> 纠正（双层回忆灯保留底层原稿不动）；但 TA 在后台自己散步、阅读沉淀下来的经历不支持直接改写正文，若不希望再被提起可通过 <code>/chiyo_memory delete</code> 停止召回。</li>\n</ul>\n\n<h2 id=\"rm-p4\">接下来的方向 · 零配置初遇向导、跨季节律与世界分享</h2>\n<p>在空间创世与无损搬家底座打通之后，接下来的迭代重心聚焦在更顺滑的初遇体验与长周期烟火气上：</p>\n<ul>\n<li><strong>首次启动零配置初遇向导</strong>：将首次打开客户端时的环境自检、模型连通（DeepSeek / 本地 Ollama 一键验证）与全随机专属世界推门落成融合成一气呵成的沉浸式引导，让纯新手无需翻看配置页也能自然安家。</li>\n<li><strong>更深层的跨季节物候与邻里节律</strong>：让街区的光影风物、店铺时令菜单与店主日常闲谈随春夏秋冬自然流转，沉淀跨越数月乃至一年的时间纵深感。</li>\n<li><strong>轻量社区世界包与底板分享</strong>：完善世界包与中性设定模板的校验预览规范，方便大家把用离线地图编辑器搭出的特色小镇、街角书屋与菜单配方轻松分享、一键试住。</li>\n</ul>\n\n<h2 id=\"rm-p1\">走过的路 · 本地记忆底座 (v0.1)</h2>\n<p>项目起步的第一版（<code>v0.1.0-rc4</code> 至 <code>rc9</code>），先把最底层的地基打牢：</p>\n<ul>\n<li>把长期记忆存进本机 SQLite，支持随时查看、纠错和停止召回；</li>\n<li>把程序目录与个人数据分离，做到关机重启不断线、命令升级不丢数据。</li>\n</ul>\n\n<h2 id=\"rm-p2\">走过的路 · 空间雏形与真实步行 (v0.2)</h2>\n<p>在 <code>v0.2</code> 阶段，第一次给 TA 搭起了能走动的房间与街道：</p>\n<ul>\n<li>随包放入单机离线地图编辑器与多套示例模板；</li>\n<li>接通按路网距离计时的移动工具，并打通手机微信私聊。</li>\n</ul>\n\n<h2 id=\"rm-p3\">走过的路 · 连续日常与桌面端 (v0.3 – v0.7.0)</h2>\n<p>从 <code>v0.3.0</code> 到现在的 <code>v0.7.0</code>，一步步把空间动作补成了连贯的起居日常：</p>\n<ul>\n<li>实现多段连贯寻路、半路暂停续走、多任务挂起恢复与跨天街区见闻；</li>\n<li>补上免选风貌全随机专属世界、12 类店铺自由点单、包袋嵌套与冷热干湿物态、自主拒绝与异步回信；</li>\n<li>带来免管理员一键部署的原生 Windows 桌面客户端与无损搬家旅行箱 <code>.nyairo-suitcase.zip</code>。接下来的每一步规划，我们依然做好一件再如实勾选一件。</li>\n</ul>"
  },
  "privacy": {
    "title": "隐私与数据边界",
    "summary": "坦诚说明哪些数据留在你的电脑上、哪些内容在对话时会发给模型接口，以及「删除记忆」到底做了什么。",
    "toc": [
      {
        "id": "privacy-section-0",
        "text": "哪些数据留在你自己的电脑里"
      },
      {
        "id": "privacy-section-1",
        "text": "哪些内容会经过网络发送出去"
      },
      {
        "id": "privacy-section-2",
        "text": "「停止召回」与彻底清理的区别"
      },
      {
        "id": "privacy-section-3",
        "text": "反馈问题时如何保护个人隐私"
      }
    ],
    "content": "<h2 id=\"privacy-section-0\">哪些数据留在你自己的电脑里</h2>\n<p>你的模型 API Key、微信 / Telegram 绑定凭证、完整聊天记录、SQLite 长期记忆库、生活状态账本以及相册文稿，全部写在你本机的个人目录（默认为 <code>~/.nyairo</code>）下。<strong>不经过 nyairo 的任何服务器</strong>，nyairo 官方没有集中式云端后台，不会也无法收集你的私人聊天数据库。</p>\n<p><strong>本机权限提醒</strong>：当前版本没有对本地数据库做透明磁盘加密，数据安全依赖你电脑自身的操作系统账号权限。拥有同一台电脑管理员（root）权限的人或能读取你用户目录的本地软件，理论上都能读到这些文件，请保管好自己的设备与备份压缩包。</p>\n\n<h2 id=\"privacy-section-1\">哪些内容会经过网络发送出去</h2>\n<p>「本地部署」不等于「完全不联网」。日常使用中会产生网络通信的环节只有你主动配好的这几处：</p>\n<ul>\n<li><strong>你选择的大模型服务商</strong>：每次对话（或触发后台活动、<code>/chiyo_consider</code> 分析）时，当前输入、近期对话上下文以及被检索出来的相关记忆片段，会直连你填写的模型 API 地址用来生成回复，<strong>不经过 nyairo 的任何服务器</strong>。使用 <strong>Ollama</strong> 等本地模型时，数据可以完全不出本机。</li>\n<li><strong>消息平台（可选）</strong>：如果你接入了微信或 Telegram，你和 TA 收发的消息自然会经过对应平台的通信链路。</li>\n<li><strong>本教程网站（nyairo.com）</strong>：纯静态网页，搜索在浏览器本地运行，亮暗主题偏好只存在浏览器 <code>localStorage</code> 里，不连接你的本地实例。</li>\n</ul>\n\n<h2 id=\"privacy-section-2\">「停止召回」与彻底清理的区别</h2>\n<ul>\n<li><strong><code>/chiyo_memory delete</code> 做了什么</strong>：它把指定记忆标记为停用，并在组装后续对话上下文时过滤掉相关旧片段，让模型以后不再提起这件事；同时默认限制了模型直接调命令行翻读历史日志文件的权限。</li>\n<li><strong>它没做什么</strong>：它<strong>不是磁盘粉碎机</strong>——为了排错与回溯，原始会话记录、审计回执以及你之前手动打过的备份包里仍然留有当时的文字，模型服务商或微信 / Telegram 端侧的历史消息也不会被这条命令远程撤回。</li>\n<li><strong>如何彻底清空整套本地数据</strong>：先停掉所有正在运行的 nyairo 与网关进程，确认不需要任何回忆后，手动删除 <code>~/.nyairo</code> 目录及你自己保存过的备份归档即可。</li>\n</ul>\n\n<h2 id=\"privacy-section-3\">反馈问题时如何保护个人隐私</h2>\n<p>在 GitHub 提交 Issue 或在社区群里贴报错截图前，请先扫一眼有没有带出敏感信息：</p>\n<ul>\n<li>不要直接贴完整的 <code>.env</code>、<code>config.yaml</code> 或整包 <code>~/.nyairo</code> 文件；</li>\n<li>截掉或打码 API Key、Telegram Bot Token、个人数字 Chat ID、微信绑定二维码以及你的真实系统用户名。万一不慎把密钥发到了公开页面，第一时间去对应平台后台<strong>吊销并换新密钥</strong>，光删帖子是没用的。</li>\n</ul>"
  },
  "testing": {
    "title": "测试范围与发行说明",
    "summary": "说明每个公开版本发布前做过哪些真实检查、哪些场景还没有测过，不拿单元测试数量冒充全场景稳定性保证。",
    "toc": [
      {
        "id": "testing-section-0",
        "text": "为什么专门写这一页"
      },
      {
        "id": "testing-section-1",
        "text": "目前已覆盖的自动化与实机检查"
      },
      {
        "id": "testing-section-2",
        "text": "明确尚未覆盖的范围"
      }
    ],
    "content": "<h2 id=\"testing-section-0\">为什么专门写这一页</h2>\n<p>很多开源项目喜欢在首页挂一个漂亮的测试通过徽章，让读者误以为所有系统、所有极端情况都已经万无一失。nyairo 目前处于 <code>v0.x</code> 公开测试阶段，我们更愿意把「测了什么」和「没测什么」摊开来讲清楚，方便你决定怎么用它。</p>\n\n<h2 id=\"testing-section-1\">目前已覆盖的自动化与实机检查</h2>\n<ul>\n<li><strong>核心机制与组件测试</strong>：记忆读写与纠错边界、多段路网寻路、暂停恢复不重走、每日请求预算上限、世界早晚变化与身体节律、旅行箱预检与源码脱敏等核心模块均有对应的离线自动化测试（如 <code>v0.4</code> 世界套件 263 项、<code>v0.5.1</code> 至 <code>v0.7.0</code> 生活节律、配额收敛与安全脱敏测试均通过）。</li>\n<li><strong>发行包完整性校验</strong>：每次发布的 GitHub Release ZIP 均附带 <code>MANIFEST.json</code> 清单与 <code>.sha256</code> 哈希校验，确保安装落盘的每一个脚本与组件字节与打包时完全一致。</li>\n<li><strong>Windows 桌面端与官网实机核验</strong>：<code>nyairo-0.7.0-setup.exe</code> 安装包、桌面端各功能视图（总览、流式对话、地图、文稿版本对比）及官网桌面/移动端交互均在本机环境实测核对通过。</li>\n</ul>\n\n<h2 id=\"testing-section-2\">明确尚未覆盖的范围</h2>\n<ul>\n<li><strong>未做 <code>v0.7.0</code> 连续 24 小时以上真实大模型挂机长测</strong>：自动化测试主要使用确定性替身与受控时钟来验证逻辑正确性，不能代替真实商业大模型跑上好几天后的自然表现验收。</li>\n<li><strong>未启用云端自动 GitHub Actions 功能门禁</strong>：仓库 <code>ci/templates/</code> 下存放的是供参考的测试工作流模板，尚未在 GitHub 云端开启每次提交自动跑全量测试，因此我们不挂虚假的 CI Passing 徽章。</li>\n<li><strong>跨平台覆盖有限</strong>：主要验证环境集中在 Ubuntu / Debian Linux 与 Windows 10/11（WSL 2 Ubuntu）；macOS、其他小众 Linux 发行版、Docker 容器化部署以及 QQ / 飞书真实账号长连均未完成完整验收。</li>\n</ul>"
  },
  "release": {
    "title": "开源许可与站点维护",
    "summary": "关于 Apache-2.0 与上游 MIT 许可证边界、发行包打包原则，以及本教程网站本身的日常更新方式。",
    "toc": [
      {
        "id": "release-section-0",
        "text": "开源许可证与第三方说明"
      },
      {
        "id": "release-section-1",
        "text": "公开仓库与发行包原则"
      },
      {
        "id": "release-section-2",
        "text": "许可证署名细节"
      },
      {
        "id": "release-section-3",
        "text": "发版核对顺序"
      },
      {
        "id": "release-section-4",
        "text": "怎样维护与更新本官网"
      }
    ],
    "content": "<h2 id=\"release-section-0\">开源许可证与第三方说明</h2>\n<p>nyairo 公开仓库新增的自研代码（含记忆账本、虚拟世界与身体节律、生活连续性引擎、Life Supply 文稿服务及官网文档）均采用 <strong>Apache-2.0</strong> 开源许可证发布。</p>\n<p><strong>底层执行引擎（开源项目 Hermes，位于 <code>vendor/hermes</code>）</strong>：保留其原有的 <strong>MIT</strong> 许可证及版权声明；其余第三方 Python 与前端依赖各自遵循其原有开源许可。</p>\n\n<h2 id=\"release-section-1\">公开仓库与发行包原则</h2>\n<ul>\n<li>公开仓库地址为 <a href=\"https://github.com/L1AN929/nyairo\" target=\"_blank\" rel=\"noopener noreferrer\">github.com/L1AN929/nyairo</a>，只收录程序源码、中性模板、测试与文档，绝不包含任何个人聊天库、私钥或内部服务器地址。</li>\n<li>普通用户安装与升级始终以固定的 <strong>GitHub Release 标签</strong>（当前为 <code>v0.7.0</code>）及校验包为准，不直接拉取开发中的分支，以免装到半成品。</li>\n</ul>\n\n<h2 id=\"release-section-2\">许可证署名细节</h2>\n<p>根目录提供完整的 <code>LICENSE</code> 与 <code>NOTICE</code> 文件。需要留意的是：开源许可证仅授权代码与文档本身的使用与修改，不包含任何商业大模型 API 额度、第三方消息平台账号凭证或作者私人的角色设定数据。</p>\n\n<h2 id=\"release-section-3\">发版核对顺序</h2>\n<ol>\n<li>在干净发布目录上跑通本版目标测试与敏感信息扫描（确保内部测试域名、本机用户名路径全部脱敏）；</li>\n<li>生成匹配的 <code>MANIFEST.json</code>，打出版本压缩包与 <code>.sha256</code> 校验文件，并在隔离账号下验证解包与自检命令；</li>\n<li>同步更新官网安装入口、更新日志与已知边界说明，保留所有已发布的历史版本包原字节不动。</li>\n</ol>\n\n<h2 id=\"release-section-4\">怎样维护与更新本官网</h2>\n<p>本官网是一套零框架依赖的静态网页，源码位于仓库 <code>website/</code> 目录（核心文件为 <code>index.html</code>、<code>style.css</code>、<code>app.js</code>、<code>docs-data.js</code> 与同步整理的 <code>USER_GUIDE.zh-CN.md</code>）：</p>\n<ol>\n<li>修改样式、脚本或文档数据后，记得同步刷新 <code>index.html</code> 里引用 <code>style.css?v=...</code>、<code>app.js?v=...</code>、<code>docs-data.js?v=...</code> 的版本号参数，确保 CDN 与访客浏览器立刻拉到最新内容；</li>\n<li>部署静态站点目录前，先把线上旧文件完整备份到公开 Web 目录<strong>之外</strong>的私有位置，切勿在公开目录内堆放 <code>.bak</code> 备份文件；</li>\n<li>上线后实际打开桌面端与手机宽度页面，检查导航跳转、下载链接、搜索框（<code>Ctrl + K</code>）与复制按钮均正常可用。</li>\n</ol>"
  },
  "contributing": {
    "title": "问题反馈与参与贡献",
    "summary": "遇到 Bug 怎么提才好复现、发现安全隐患怎么处理，以及给项目提交代码时的基本约定。",
    "toc": [
      {
        "id": "contributing-section-0",
        "text": "遇到 Bug 怎么反馈最有效"
      },
      {
        "id": "contributing-section-1",
        "text": "发现安全或隐私漏洞怎么办"
      },
      {
        "id": "contributing-section-2",
        "text": "提交代码 (PR) 的基本约定"
      },
      {
        "id": "contributing-section-3",
        "text": "编写回归测试的小建议"
      },
      {
        "id": "contributing-section-4",
        "text": "日常长期体验反馈"
      }
    ],
    "content": "<h2 id=\"contributing-section-0\">遇到 Bug 怎么反馈最有效</h2>\n<p>提 Issue 或在交流群提问时，带上这几样信息能帮我们最快定位问题：</p>\n<ul>\n<li><strong>环境与版本</strong>：用的是 Windows 桌面端还是 Linux 终端、系统版本（如 Ubuntu 24.04 / WSL2）、<code>~/.local/state/nyairo/install.json</code> 里的版本号；</li>\n<li><strong>怎么触发的</strong>：你敲了什么命令或点了哪个按钮、本来预期发生什么、实际报了什么错；</li>\n<li><strong>脱敏后的报错片段</strong>：贴终端关键报错行即可，记得先把自己的 API Key、Token 和私人聊天内容删掉。</li>\n</ul>\n\n<h2 id=\"contributing-section-1\">发现安全或隐私漏洞怎么办</h2>\n<p>如果你发现了可能导致密钥泄露、跨账号读到别人私人记忆、或者绕过权限边界的问题，<strong>请先不要在公开 Issue 里贴出完整利用步骤</strong>。可以先在仓库或官方交流入口联系维护者说明有安全问题待私下沟通；如果涉及你自己手头已经泄露的密钥，第一时间先去服务商后台吊销更换。</p>\n\n<h2 id=\"contributing-section-2\">提交代码 (PR) 的基本约定</h2>\n<ul>\n<li><strong>改动聚焦</strong>：一个 PR 只解决一个明确的问题或小功能，顺手看一眼对应目录下的说明文档，不要把不相关的格式化混在一起；</li>\n<li><strong>测试隔离</strong>：新增测试一律使用临时目录（<code>tmp_path</code>）和虚构测试身份，绝不读写开发者本机真实的 <code>~/.nyairo</code> 目录，也不要把个人人设写死进通用代码里；</li>\n<li><strong>诚实标注验证范围</strong>：PR 描述里写清楚你在哪个系统上跑过了哪些测试，没测过的平台如实注明即可。</li>\n</ul>\n\n<h2 id=\"contributing-section-3\">编写回归测试的小建议</h2>\n<p>如果修的是聊天命令或消息路由相关的 Bug（比如某个斜杠命令在终端能用、到了微信或 Telegram 里却没反应），写测试时尽量从外层消息入口一路测到最终处理函数，并确认这段测试在修改前的旧代码上确实会失败——这样以后重构时才能真正防住老毛病复发。</p>\n\n<h2 id=\"contributing-section-4\">日常长期体验反馈</h2>\n<p>除了硬报错，我们也特别欢迎你分享用上几天之后的真实感受：比如哪条记忆召回得不够自然、街区走路节奏哪里觉得别扭、或者桌面端哪个按钮不够顺手。这些日常细节正是让 nyairo 慢慢变得更好用的关键。</p>"
  },
  "acknowledgments": {
    "title": "致谢",
    "summary": "致一路并肩推演世界的伙伴与长久守候的你。",
    "toc": [],
    "content": "<div class=\"callout callout-info\"><p style=\"margin: 0; font-size: 15px; line-height: 1.8;\">感谢无数个深夜里在屏幕两端并肩推演世界的所有伙伴，以及无论何时都守在身旁的你。</p></div>"
  }
};
