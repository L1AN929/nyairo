# rc1 阅读工作台与私人笔记：父级实际验收

## 结果与基线

本轮限定本地读写链通过，无范围内阻塞。唯一施工根 `D:\nyairo-rc1-body-backend`，分支 `work/rc1-phone-world-body`，HEAD 仍是 GitHub `v0.3.0-rc1` 的 `41e818faaa24115fbf43db2977b15585c04144af`。承接手机/世界/身体/实体购物房间成果，不重置、不重新 prepare-demo、不重买。

产品及定向测试由国内 WorkBuddy DeepSeek 实施，父级负责设计裁定、独占写集、接线核对、真实页面与重启验收。源码仍未提交/推送/打标/打包/部署；不是新发布版本。

关联：[阅读契约](rc1-reading-contract.md)、[核心结果](rc1-reading-owner-result.md)、[服务与工具结果](rc1-reading-api-result.md)、[界面结果](rc1-reading-ui-result.md)、[用户指南](rc1-reading-userguide.md)、[前轮房间验收](rc1-room-parent-acceptance.md)。各实现者报告是当时交付快照；最终接线状态以本报告实际验收为准，P/U 已完成，普通 notes 的 reading_anchor 已接通。

## 当前可用入口

**本机 <http://127.0.0.1:39091/> → 阅读**。验收后保留运行，不是自启动或生产部署。

- 同实例 `/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`。
- 家中客厅、坐姿、双手空；原书桌/椅子/灯及原点心摆放保留，灯仍 on。
- 仍 **2860 虚拟 JPY / 四笔购买**，阅读不扣钱；本轮无真实支付。
- 现有绑定阅读材料保留身份，当前正文为父级明确提供的“阅读工作台验收材料 · 第二版”，两页；显式停点为当前版本第 2 页。
- 第一版第 2 页的一条私人笔记和一条疑问保留，带原版本并标 stale，不冒充第二版注释。普通笔记列表有这一条真实 memo，不再是前轮空态。
- 最终 serve PID 18882，Supply 18883，World 18884，仅为历史记录；未来停机必须重新核对 `/proc/<pid>/cmdline` 与 cwd，不盲用 PID。

同根正常重启命令（先正常停自己的 serve；不要再 prepare-demo）：

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve \
  --instance /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094 \
  --port 0 --tick-seconds 5 --with-room-living
```

`--port 0` 输出新 URL，需核对 Windows loopback 可访问性。本次最终用 39091；没有修改全局网络或防火墙。旧房间启用 flag 仍只作用于 serve 内存，不持久化改 features。

## 真实页面与同实例链

内置浏览器再次返回“Browser view did not become visible for the calling session”，父级使用独立 headless Edge + CDP 加载真实产品页面、填写表单与点正常按钮；业务写入均走页面，fetch 仅做只读回核，不用 stub DOM 冒充体验。

1. 开工只读核对当前钱包、世界、库存、笔记、收藏、阅读元数据。已有 reading_artifact_id 真实存在；原 72 字占位演示材料单独备份到 scratch，instance.json 留时间戳副本。没有复制整棵树、直接写 owner 数据库或恢复任何旧备份。
2. 三个独占写者分别实现 Life/Supply 客户端、phone 服务/工具、web。World/Body/economy/Supply 服务零新增改动。
3. 核对自己的进程身份/cwd后正常停止旧 serve。固定 35931 启动返回 `PHONE_SERVE_FAILED / OSError`，CLI 未暴露 errno；没有足够证据把本次根因确定写成 EADDRINUSE。保留失败日志，换 40173 后同根正常启动、Windows HTTP 200；不删 socket、不改端口安全门。
4. 页面打开阅读，先只翻页不保存；私人 head 不变且无 POST，证明确实 GET 零写。再明确粘贴父级自写非秘密三页文本，确认“替换”后形成新材料版本。不是外部抓取、不是模型创作、不是自主亲历。
5. 第一页包含字面 `<script>window.readingAcceptanceInjected=true</script>`：实际正文显示，页面变量未置 true，未执行注入。理解始终 UNKNOWN。
6. 翻到第 2 页，显式保存停点；写“书桌与阅读是两条事实”的笔记及“为什么未知光不能显示成关闭？”的疑问。两种 typed 条目和版本页码分别正确，成功后界面重新获取 private_head，连续写入未用旧头覆盖。
7. 回第 1 页再次明确保存。实际页面链通过，390px 无横向溢出、截图成功、runtime exceptions 0；请求证据仅留 URL/字段名/operation_key/page/kind/revision，不记录 CSRF header 或私人正文到请求数组。
8. 保存重启前快照，核对 PID/cwd 后正常停止 18677；同根重启 18882，换 39091，未新建实例。**15 项稳定事实全部一致**：钱包、位置、手持、家具、surfaces、灯身份/电源/since、姿态、收藏、库存、材料/正文、书签、批注、private_head、notes、阅读元数据。灯累计秒数与 Body 浮点不作冻结等值。
9. 新开真实页面默认回到最后明确保存的第 1 页，不是最高到过的第 2 页；再浏览第 2 页，原笔记和疑问真实显示。
10. 页面显式导入第二版两页文本。旧书签 needs_reread=true，两条原批注 stale=true，界面有旧版警告，不偷偷映射到新正文同页。再翻到新版第 2 页，明确保存后书签属于当前版本、needs_reread=false。
11. 再新开只读页面仍回新版第 2 页，旧版批注保留；390px 无溢出、截图成功、runtime exception 0。最后原钱包/世界九项事实与开工前一致，HTTP 200。

本轮只有第一版读写后的**一次**同根重启；新版停点证明是后续新页面只读重开，不夸为第二次重启。此轮 WSL UID 0，不是普通账户/Windows 原生部署验收；页面自动化不是人工全设备/完整软键盘视觉验收。

## 定点接线与故障修复

父级只做一次接口核对及随后具体故障返修，没有重复泛化审查：

- 初核心批注重放只比 kind/page/revision，未比正文；现在稳定意图摘要绑定正文等参数，同键改字拒绝。CAS 前置真正使用 caller expected_head，不是先检后读新 head 提交。
- 初疑问 helper 同句跨页合并且归属缺 item_id；手机路径改按操作键派生条目，同句不同页各有真实归属，旧调用兼容。
- 初材料重放只比较当前正文，可借别的操作键冒充成功，且后续改版会反向误判；现在只读核对 owner 的 artifact/kind/request_digest，旧操作描述它原来落下的 version/parent，revision_current=false 不声称当前正文仍相同。Supply 服务与 store 未修改，仅窄 port 加既有 lookup_operation 读口。
- notes 未投影 reading_anchor 的漏接由 P 补齐，协议 ID 原字节、旧 memo 无键兼容、state 不带正文/锚点。
- 父级 POSIX conformance 的第 3 项曾因测试夹具 pid+毫秒同名而红，原夹具保留。P 改为 time_ns+进程序号，连续同前缀独立目录；原 6 项相关用例 WSL 无 skip 全绿。不是产品失败，也不靠删除或重试洗绿。

## 定向检查与证据

- C 最终真实 owner 16 项 + 精确受影响 PDI 14 项，退出 0；含原键/跨 kind/改正文、旧版本重放、真正 CAS 交错、跨页疑问、UNKNOWN 对账等定向反例。未运行旧源码负控，不能称旧源码负控已完成。
- P 新 API 41 + tool 19。父级 WSL 全 60 项日志显示 OK；首次 shell 包装没有打印真实退出码，故不把那个空 `EXIT_CODE=` 当可靠退出证据。随后小 runner 跑 3 项曾真实 exit 1 夹具撞名；修后原 conformance+anchor 六项 WSL OK，作者工具观测 exit 0，日志保留。Windows 相关真实 port skip 与旧 serve-flag `os.geteuid` 失败均在 P 报告保留，不冒称 Windows 原生通过。
- U 新阅读 UI 25 项通过；此前精准受影响旧 UI 34/25/21 已由作者运行，父级不重复刷套件。stub DOM 证明与本轮真实 Edge 证明分别报告。
- 最终 44 notes tree/format、`git diff --check`、rc1 三保护文件检查退出 0；HEAD 未变、索引空、无 tracked 删除。
- World/插件既有 diff 与本轮前一致（PowerShell初文本比对 False 为换行规范问题；Python统一UTF-8/换行核对 true，未据前者误报通过）。三保护文件与插件入口的有限路径基线一致，不做全树 SHA。

会话 scratch 保留：`reading-parent-before.json`、`reading-original-material-backup-20261018-045350.json`、`reading-parent-before-load.json`、`reading-serve-loaded.log`、alternate/restarted serve日志；`reading-browser-initial-1792300677777.json`、`reading-browser-after-restart-1792300731782.json`、`reading-browser-readonly-1792300756652.json`，三张 reading-mobile PNG；前后重启快照、`reading-comparison-before-restart--after-restart.json`（15 true）、`reading-comparison-before--final.json`（9 true）、`reading-source-close.json`；POSIX 首绿/夹具红/修复日志。所有失败保留，没有物理删除或清理夹具。

## 上限与停用边界

单一实例绑定的文本材料，不是书库、PDF/EPUB解析、外网抓取、实体书购买或书架容器。正文、位置和批注都归现有 Supply owner；私人正文不自动进 Memory、不调用真实模型、不签发理解/信念/自主Decision。书桌/灯不硬绑阅读资格，也没有特定椅子接触物理或新 Body 数学。

旧版文本由 Supply 保留版本，但当前 facade 不提供历史正文读取端口；旧锚点能识别旧版，不代表能在页面打开任意历史正文。typed 文档内阅读操作摘要有 200 条有界保留，超界后不能保证原细粒度归属长期可查；owner操作存在但归属不足时失败关闭，不重新写。没有多用户、长期 24h、生产/发布资格证明。

停用/恢复不等于历史回滚：不把旧材料/PDI/经济/World备份覆盖新历史。正常停止自己服务并保留当前数据；控制恢复需另外明确数据与历史责任。无本轮范围内阻塞，至此停止扩张，发布另需授权。
