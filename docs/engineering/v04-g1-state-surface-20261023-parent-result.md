# G1状态读面修复 · 父级独立验收（2026-10-23）

结论：**状态误报最小修复通过，G1中断/接续场景仍未通过。** 现场不再把PAUSED前台活动说成不存在；本功能内部接着只读核对其合法RESUME路径，不清理活动、不盲跑。

## 交付与过程

诊断先前60轮耗尽exit1无报告；收窄补交bw2ixc0yi exit0，父级STATE_AUDIT_DELIVERED通过，确认原classify_state读取未落盘的summary.last_tick.due.material，导致权威有前台却IDLE。父级先落NOW再写修复任务。

第一次修复启动b35pi1wi4被父级检查点JSON漏逗号拦截，dispatch未启动、业务写集无变化；父级修正JSON并单独验证CHECKPOINT_VALID后启动bt7cgqa2k，worker exit0，日志20261023-001526-g1-state-surface-fix.log。两次管理JSON错误如实保留，不算业务测试失败。

选方案A只改读取侧，不往resident_summary增加last_tick，不改落盘格式/枚举/owner。实际写集为one_day_life.py、新test_g1_state_surface.py、新life-status-authority note和worker结果。父级仅读报告/范围及测试输出，未读components实现、未写业务。git diff --stat显示one_day_life.py 170增15删；新文件列在status，未提交。

## 具名实现事实（worker交付，非父级通读源码）

activity_view纯文件读取canonical_life_state，不构造会建目录/lock的store；recent_windows有界读取journal尾部64KiB/40条。分类保留pause/STOPPED/RUNNING优先级，真实前台ACTIVE/PAUSED/WAITING映射既有WAITING并在basis给ref/status/revision；真实已读空前台才basis断言无前台。缺失/损坏/不可读用既有枚举加具名未核实basis，不新增UNKNOWN契约。

BLOCKED取最后一条真正考虑过事情的窗口，尾随NOT_DUE静默不是恢复。两参调用继续支持，但basis写明未读权威。

## 父级独立实际验收

均在唯一工作树执行：

1. WSL `/tmp/rc104-venv/bin/python -B -m unittest -v tests.test_g1_state_surface`，PYTHONDONTWRITEBYTECODE=1：6例、0.108秒、OK、shell0。
2. `npx tsx C:/Users/28556/.claude/skills/write-notes-like-deepseek/scripts/verify-agent-note-tree.ts`：87篇tree/相对链接通过，shell0。
3. 同路径verify-agent-note-format.ts：87篇通过，shell0。
4. 同路径verify-archived-agent-notes.ts：archive0/seal0通过，shell0。
5. WSL现成one_day_life.py status纯读验收，父级在同一命令前后记录state目录文件mode/size/mtime、读取canonical/ledger/Memory并只读检查lease。shell0，安全证据 `.agents/worker-runs/20261023-g1-state-surface-status-parent.json`，九项checks全真：WAITING、前台ref可见、PAUSED可见、canonical revision15、ledger13、Memory绑定存在、前后文件快照相等、lease FREE、status shell0。原pause仍absent/false。

`git diff --check`限写集shell0。notes门禁不代表G1行为链通过；新增6例是按落盘形状手写的文件替身，不是真owner/模型。父级未重跑产品段、旧绿套件、浏览器或全量。

## 边界与下一步

- 权威未知仍落既有IDLE桶但basis明确未核实；机器枚举区分UNKNOWN需新契约，不本轮改。
- timeline两参调用仍未读Activity权威，但不再basis假称读过；不扩大本轮写集。
- 当前PAUSED活动仍占前台。状态面修复不让它自动可执行、不修其World目标、不恢复阅读、不证明G1。
- 本轮无产品调用，实例ledger13，加旧预检1=共同14/96余82；本功能请求cap5已用1余4，不刷新额度。
- 本功能内接着只读核对新鲜RESUME既有活动的合法前置，不能把合法RESUME误当重放旧START，也不能在字段不全时盲跑。

Memory runtime空库读取此前已证，非记忆影响选择；G1中断/接续与其他场景未闭合，恢复FAIL、理解UNKNOWN、24h未做保持。没有commit/push/deploy。
