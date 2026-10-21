# rc1 + 手机 + 世界与身体：父级实际体验验收

## 结论与来源

限定本地体验链通过。直接父版本为用户指定的 GitHub [v0.3.0-rc1](https://github.com/L1AN929/nyairo/releases/tag/v0.3.0-rc1)，标签提交 `41e818faaa24115fbf43db2977b15585c04144af`；远程 `git ls-remote`、本地 HEAD 和公开 Release API 均核实。Release 当前无独立 assets，未声称下载 rc1 安装包。rc1 是 `345df15` 后两次移动工具修复的后继。

施工根 `D:\nyairo-rc1-body-backend`，分支 `work/rc1-phone-world-body`。先从精确 rc1 建 worktree，再按白名单迁入 0.4 手机，随后国内 WorkBuddy DeepSeek 分工实现世界/身体。父级只做规格、监工、测试脚本和实际验收。未暂存、提交、推送、打标、打包或部署；MANIFEST 仍是 rc1 发布清单，不是当前开发树的完整性声明。

rc1 的 `MANIFEST.json`、`scripts/fix_platform_world_move.py`、`scripts/fix_weixin_world_move.py` 最终与 HEAD 无差异；插件保留 rc1 移动引导/注册，只追加手机注册 12 行。原 v03 HEAD `7f09d26df64e054faf51ba35ad85c64f73a5a837` 与空索引保留。旧 `D:\nyairo-v04-backend` 留存但不再续工；旧树内有中断半成品，不能作为本次成果运行根。

迁移来源与混合片段剥离边界见 [迁移报告](rc1-phone-baseline-transfer.md)。没有完整旧验收时刻快照，不能宣称混合文件逐字节复原；移植与行为验证分别记录。

## 当前可体验入口

**本机 <http://127.0.0.1:35931/>**，验收结束时保留运行，不是生产或自启动服务。

- WSL Ubuntu 实例 `/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`。
- 实际 UID 0，不称为普通账户/多用户部署验收。
- 最终 serve PID 5651、Supply 5653、World 5654，仅作历史记录；未来停止前必须重验 PID 与实例归属。
- `/proc/5651/cwd` 实测 `/mnt/d/nyairo-rc1-body-backend`，cmdline 明确为同一 instance 的 `scripts/phone.py serve`，不是旧源码进程。

停止自己的现有 serve 后，在 WSL 同根重启：

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve \
  --instance /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094 \
  --port 0 --tick-seconds 5
```

打开输出中的新 `url`；端口可能变化。前台用 Ctrl+C 停止，等待 owned children 退出。不要重复 prepare 已有根、批量杀进程或删除 socket。另建全新实例才使用 `prepare-demo --root <新绝对目录> --with-world`。说明见 [用户指南](v04-world-phone-userguide.md)。

## 真实页面体验与保留的失败

内置浏览器因当前会话视图无法显示而拒绝打开，父级改用独立 headless Edge + CDP，实际加载本机产品 HTML/JS、点击按钮与确认弹层；业务 mutation 没有绕过 UI，快照 fetch 仅用于读回核实。不是 stub DOM 或模拟服务。

1. 初始 `home/bedroom`，真实室内家具与床面；主屏八个入口。
2. 从商店购买唯一上架点心，10000 → **9760 虚拟 JPY**，一笔购买。未发生真实支付。
3. 首次返回世界仍显示旧缓存，无拿起按钮；owner 已有原点心且允许拿起。修复 purchase/use 后世界快照失效。原 FAIL `rc1-world-browser-proof.json` 保留。
4. 原点心拿起到左手成功；随后 PLACE 409。取证复算授权 args_digest 确认真 payload 把显示遮罩 `item-[已隐藏的长标识]` 当 ID，而非当前 area 无效。修复手持展示/协议字段结构分离，新增点击级长 ID 回归。原 FAIL `rc1-world-browser-continuation.json` 保留。
5. 核心同时复现并修复两个直接相关缺陷：跨室内 area 放置原来未受限制、拒绝详情被压成 UNKNOWN。当前 area 允许，远处/非法 area 保守拒绝；未放宽能力、归属或可达门。
6. 核验 PID/cwd 后正常停旧 serve，同根重启加载修复；左手原物件和 9760/一笔交易均保留。没有重买、重置或重新拿起。
7. 新真实页面直接放下原物件成功；坐下成功，恢复仍 inactive 且低负荷显示无需恢复；站起成功。
8. 从卧室出发去客厅：出发后仍在卧室且 `travel=in_progress`；父级实际等待 **31390ms** 后到 `home/living`。不是瞬移或注入时钟验收。
9. 身体页显示真实姿势/定性信号投影，无裸浮点调节入口。日志同执行 `phone-exec:6344ac03e6c9ceadcd4dcc98e29bdee1` 的后果为 **30.0s**、`measured_travel_recovered`，不是固定 600s；该名称诚实表示到达落盘后 tick 补结算。
10. 最终再次正常停服、同根重启；11 项只读对比全真：位置、物件、身体投影、执行身份、钱包、库存、笔记、收藏均一致；9760/一笔、客厅、双手空。重启后的真实 Edge 页面只读复核通过。

原物件 `item-7bd7c347b2cb6d134816f4020ad912440db3c42ef408462151c9d06ad48aab2c` 当前放在卧室，未消费。本轮的新实例并未写新笔记或收藏；两者重启比对是空态一致，不冒充本实例的新写入验收；手机新建/修订机制与此前真实体验及迁移定向检查分别见手机报告。

## 验证范围

- UI 最终世界用例 25 项、旧手机 34 项，施工者报告 exit 0；缓存与手持 ID 均有精确负控失败再恢复。
- 核心最终机制 27、owner 20；HTTP 世界 9、工具世界 7，以及直接受影响检查按各自结果报告。父级未再次刷全套。
- 休息合法开启/推进/停止/负荷打断/恢复持久化为真实 owner 隔离测试（含注入时钟），不是此低负荷浏览器实例的正向恢复实钟证明。当前实例只实际验证低负荷拒绝。
- Edge 1280px 和 390px 的 scrollWidth 分别等于 viewport；390px 世界页 SVG 与折叠详情存在，页面 runtime exceptions 0。成功截图已保存，但未声称全设备/人工像素级审美验收或完整键盘无障碍覆盖。
- 父级最终 38 篇 notes tree/format、`git diff --check`、rc1 保护项检查全 exit 0；两棵原树/新树未暂存发布。
- 证据在 session scratch：`rc1-world-browser-proof.json`、`rc1-world-browser-continuation.json`（两份原红）、`rc1-world-browser-final.json`、`rc1-world-post-restart-browser.json`、`rc1-world-restart-checks.json`、before/after 快照、serve/restart 日志及 PNG。

## 仍然有限的地方

地图是有来源的可达路线板，不是有地理坐标的完整地图/3D世界。MOVE 每次一条边，多跳分段；只实际走了室内一段，未声称全街区路径都人工走过。家具来自世界包，家里没有可拿小件就从真实虚拟商店购买；咖啡馆杯子存在但本轮父级未走去拿。

身体仍是既有六指标与规则：未改速率/阈值/POSE_STRAIN，不加饥饿、营养、回血或强制吃睡。姿势持续时长仍为已有假设而非测量；sensory_load/warmth 没有权威环境输入，不编噪音温度；本轮不是完整身体系统或身体历史适应。REST 接通不代表自主选择休息。没有真实模型、全天/24h、生产切换、外发、真实支付、既有个人实例迁移或 Windows 原生运行证明。

## 操作偏差

核心施工者报告曾用 py_compile 产生缓存后物理清理 `.pyc` 与空缓存目录，违反本轮不删除要求；不能宣称全程零删除。其报告未涉及源码/实例数据删除，父级未重现或恢复已清缓存；后续定点返修明确禁止并报告未删除。该偏差保留，不因功能通过抹掉。

本地指定体验链当前无阻断项，到此停止扩张。发布、版本号和安装包另需授权。
