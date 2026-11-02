# G1限定真实采用：第二批固定范围

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

本人在本窗口直接批准：再开一次，全新root/run，最多20次新增预留，失败不退，只跑一次不续跑；必要离线准备后运行，不改生产规则、不碰旧实例、不外发、不提交推送，截至2026-10-30 17:00 CST。

固定新身份：root `/root/nyairo-g1-real-20261030-b`，独立authority `/root/nyairo-g1-real-20261030-b-memory-authority`，run `run:g1-real-20261030-b`，域 `g1:real:20261030-b`。创建前均须不存在；若已存在或被占用就停，不换名追成功。第一批及其authority/started/账本/结果不读写或重开。历史保守101/300，不退款；新批硬cap20（Decision、内容及失败共用，跨PID同账本），最坏121/300。

观察和故障路线沿原计划，不新增行为：真实模型自主选择作品/内容，首个合法作品结果机械定义A；operator显式中断与两PID有序停启；仅合法A写入各一次丢回执/原operation对账及并发head冲突；观察模型NO_ACTION后续作。普通NO_ACTION不是ABANDON。模型没有选择某环节就NOT_COVERED，不造资格/改候选/解除静默或强制续作。

总窗口1800真实秒含跨PID停启，monotonic和墙钟均有界，今日17:00更早截止；实例check interval固定1分钟，原静默TTL保留。默认World gate不改。provider `custom`、base `https://gw.example.invalid/v1`、model `cn:deepseek-v4.1-flash`、温度0、非stream、Decision4096/内容2048，不传seed/top_p。密钥只在父级真实run同shell source既有 `/root/.nyairo-model.env`，不读取/回显或落盘。provider/transport失败、坏账/错绑定/安全拒绝即整批停，不重试/诊断探测追成功。

必要适配只限新实验CLI封闭批次身份分派及子进程传播；保留旧批映射及默认入口，不开放任意root/run/预算参数，也不修改components/owner/生产规则。新批明确选择b后，每个phase/reserve/账本/预登记/audit/窗口必须一致；缺失/错批拒绝，不能父级b而子进程a。版本引用和作品身份配对已接收修复保持。

父级最小验收仅新批 `offline-check`（具体CLI由worker同步），HTTP硬拒、替身模型、真owner，两PID共享cap20、身份传播、默认旧批不漂移、错root/重复启动拒绝。通过后父级首次prepare、audit prepared/startnull/ledger0，再唯一真实run。所有scratch/失败根保留，不SHA仪式、不旧suite或全库。

## 实装命令（离线已通过，尚未真实运行）

`scripts/g1_real_adoption.py` 增加封闭的 `--batch a|b`，缺省 a 与原入口完全一致：

```bash
# 离线检查（0 产品调用；HTTP 硬拒、决策/内容替身、owner/runtime 真）——父级验收命令
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B \
    scripts/g1_real_adoption.py offline-check --batch b

# 以下仅列命令，尚未执行
python -B scripts/g1_real_adoption.py prepare --root /root/nyairo-g1-real-20261030-b --batch b
python -B scripts/g1_real_adoption.py audit   --root /root/nyairo-g1-real-20261030-b --batch b
python -B scripts/g1_real_adoption.py run     --root /root/nyairo-g1-real-20261030-b --batch b \
                                              --execute-approved
```

映射只有两档，不开放任意 root/run/预算：a 保留原 root/run/域/历史 98；b 固定新
root `/root/nyairo-g1-real-20261030-b`、独立 authority
`/root/nyairo-g1-real-20261030-b-memory-authority`、run `run:g1-real-20261030-b`、
域 `g1:real:20261030-b`、历史 101。身份随 `--batch` 贯通 prepare/run/audit/phase/子进程：
phase 子进程从 config 读回批次，`prereg` 与 `batch` 落盘都带批次并逐字段核对；缺身份、
错批、错 root、另一批的 prereg 一律 `BATCH_IDENTITY_MISMATCH` 拒绝。`prepare` 在写入前核
新 root 与 authority 都新鲜，已有 authority 直接 `MEMORY_AUTHORITY_NOT_FRESH`，不删除重建。

最终报告分开列：真实模型自主选择与实际owner效果；operator干预；各场景未覆盖；真实持久预留/可证发送响应及进程收尾。修复仪器不等G1通过，不把预算数当行为成果，不把本批结果倒写第一批。