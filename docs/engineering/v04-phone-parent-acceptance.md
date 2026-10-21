# v0.4 手机与虚拟购物：父级体验验收

## 结论

本地可体验范围通过：同一实例的真实浏览器笔记新建/编辑、收藏、钱包、确认购买、物品物化、实际消耗，以及进程重启后的持久化均已走通。不是静态手机壳或模拟成功页面。产品由国内 WorkBuddy DeepSeek 分工实现；父级负责边界、集成与实际体验验收。

当前源码在独立 worktree，未提交、未发布；版本元数据仍为发布基线 v0.3.0。本报告的 v0.4 是开发阶段代号，不表示已有 v0.4 安装包、标签或官网入口。

## 当前体验入口与复用

- 当前本机入口：<http://127.0.0.1:40173/>。服务在本轮验收结束时保留运行，不是自启动服务。
- 实例：`/tmp/nyaw05r.jdK3q/v04-phone-p-accept1`，使用 WSL Ubuntu，实际验收 UID 为 0；不能称为普通账户验收或生产部署。
- 源码：`D:\nyairo-v04-backend`；WSL 路径 `/mnt/d/nyairo-v04-backend`。
- 当前 Linux serve PID 11079，Supply 11080，World 11081；这些是本次记录，不可直接当作未来停止命令。

在 WSL Ubuntu 中，停止当前实例后重用同一目录：

```bash
cd /mnt/d/nyairo-v04-backend
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve \
  --instance /tmp/nyaw05r.jdK3q/v04-phone-p-accept1 --port 0
```

打开启动输出中的 `url`；`--port 0` 每次可能不同。前台运行时用 Ctrl+C 停止，等待启动器收尾完成。后台停止前先验证 `/proc/<pid>/cmdline` 的实例归属，只向自己的 serve 发送 SIGTERM，不批量杀进程或删 socket。

准备另一个全新体验实例才使用下面命令（自行选未使用的绝对目录，不要对已有实例重新初始化）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py prepare-demo \
  --root /absolute/new/phone-demo
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve \
  --instance /absolute/new/phone-demo --port 0
```

非空根拒绝初始化；10000 虚拟 JPY 是显式 demo 创世补助，不是每次启动发钱。详细操作及工具配置见 [用户指南](../USER_GUIDE.zh-CN.md)第 18 节。未修改现有个人 profile 来开放工具。

## 实际浏览器链

1. 从真实表单建立“手机第一次购物”，由 Supply 保存，memo 为 `memo:2e8f8a3ed46b0627`。
2. 钱包初始 10000；商店选 `virtual_snack_wafer`，240 虚拟 JPY，实际二次确认购买。余额变 9760，一笔 PURCHASE，物品在 home/bedroom 物化。
3. 第一次界面使用失败 `NOT_AN_ECONOMY_INSTANCE`。原因是前端把完整物件 ID 当展示文本脱敏后又提交。保留失败，没有换实例、重买或重置。DeepSeek 将协议 ID 和展示标签分离，新增完整长 ID 的传输断言。
4. 刷新同一服务后，从原物品界面确认使用成功。execution 为 `phone-exec:e1f98aca912f7d1e00903418c51d1934`，真实 `world_consumption_receipt` 为 `world-consumption:5b9e805b13064c9985c4f8e996814083`。使用只发生一次，后续不可再用。
5. 编辑同一 memo 正文为“花了240虚拟JPY，余额9760。点心已经实际使用一次，不能再吃。”；新增收藏“手机与物件设计”，来源 `design:CHIYO-v2-final@2.0-final`，明确 `read=false`、`content_fetched=false`。
6. 验证归属后向旧 serve PID 10843 发送 SIGTERM。父级 10 秒等待窗口超时，原失败保留；后续确认旧 serve/Supply/World 三进程均消失，启动日志 `status=stopped`。没有强制杀掉它们。
7. 同根重新启动为新 PID 11079、新 Supply generation、新端口 40173。重启前后 notes/references/wallet/inventory/原购买与使用查询响应逐项相等：9760、一笔购买、consumed=true、usable=false、materialization=consumed、pending_count=0。新 URL 的真实页面显示已连接及编辑后的笔记。

购买 operation：`40d186bc-071e-4b4a-bc24-6b779ad081bd`。
失败使用 operation：`8d737063-e220-4706-b30a-a3914510b87e`。
成功使用 operation：`dbf42e5d-ae54-4897-bcd6-0c4302e7c5ad`。

## 验证与原始证据

- 32 条笔记 tree/format 两门禁退出码均为 0。初次 diff check 发现用户指南 EOF 多一空行，已修复，最终 diff/tree/format 三项退出码均为 0。
- UI 最终实现者定向检查 34 项通过；API 笔记定向 5 项、工具 11 项通过；购物 owner 的 key/只读/终态修复按各自结果报告定向验证，未再次刷旧全套。
- 父级重启对比 9 项断言全通过，真实子命令退出码 0。
- 原 v0.3 工作树 HEAD 仍为 `7f09d26df64e054faf51ba35ad85c64f73a5a837`，索引空；v0.4 索引也空。无 push/tag/打包/部署。
- 会话 scratch 的 `v04-parent-before-restart.json`、`v04-parent-after-restart.json`、`v04-parent-restart-checks.json`、`v04-parent-phone-serve.{log,err}`、`v04-parent-phone-restart.{log,err}` 保留实际证据。截图工具曾超时，未声称截图成功或桌面/移动全设备视觉验收；DOM 和真实 HTTP 体验已完成。

## 明确不包含

- 手机商店目前只上架一种可消耗点心；不是丰富商城。原有实体商店商品不等于已全部接入手机。
- 身体页是已有六指标只读视图；消费仅接已有 2 秒轻操作后果，没有饥饿、营养、治疗或完整身体系统。
- 无新手机 PICK_UP 操作或完整手持操控；OWNED 不等于手持。
- 笔记可新建/改正文，改标题会明确拒绝；收藏不抓正文，阅读位置不等于理解。
- 已注册个体可用的 `chiyo_phone` 工具并通过真实 handler 测试，但本轮未调用真实模型，不能称为模型自主购物或完整自主生活日。
- 无真实支付、外发、Agenda 集成、全天/24h、生产切换、Windows 原生运行或旧个人实例迁移证明。

到此停止扩大范围；当前无阻断这条本地体验链的已知问题。未来如要发布，应另获授权做版本、安装包及发布核验，不把本次开发验收冒充发布。
