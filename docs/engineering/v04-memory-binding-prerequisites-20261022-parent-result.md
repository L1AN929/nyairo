# Memory 绑定前置核对 · 父级验收（2026-10-22）

结论：**前置核对已交付，实际绑定未执行。** 推荐下一工单复用现成 `enable-instance-memory`，不新增业务实现；操作前必须现场核实同实例停止、lease、身份与预算。不是 Memory/G1/恢复安全交付。

## 工单与交付

从 rev93 恢复，`python -X utf8 scripts/task_resume.py` 实际 READY，HEAD/branch/anchors 无冲突。
两个只读 dispatch 均通过现有脚本直接后台运行，模型 `workbuddy-wg/cn:deepseek-v4.1-flash`，30轮上限、1200秒、无模型回退：

- entry：任务 `20261022-memory-binding-entry-audit-task.md`；后台 b6e4fea2e，exit0；日志 `.agents/worker-runs/20261022-230905-memory-binding-entry-audit.log`；报告 `v04-memory-binding-entry-20261022-audit.md`。
- safety：任务 `20261022-memory-binding-safety-audit-task.md`；后台 b803oyzf4，exit0；日志 `.agents/worker-runs/20261022-230905-memory-binding-safety-audit.log`；报告 `v04-memory-binding-safety-20261022-audit.md`。

每个 worker 只获一份不相交报告写集。父级只管理任务、报告表述、NOW 与检查点，未打开 components 实现、未直接施工业务。

## 采纳与纠正

采纳既有显式 CLI 方案 A：`phone.py enable-instance-memory --instance <phone root> --owner <核实后的既有身份> --operation-key <key>`。
入口已含备份、独立 evidence/controls、三个环境名、健康与幂等；operation_key 仅回显，不是持久操作账本。
入口未强制检查 lease 或 serve/resident 停止，因此操作前必须独立确认，不凭历史 FREE 启用。发现活进程/HELD/未知 writer 即停，不顺手杀进程。

不采纳提前改 enable 增门方案 B：当前可以在有界操作前完成检查，不因尚未执行就造新工具。
手工改 JSON/SQL 方案否决；新增恢复权威不在本工单。

父级已直接纠正文档中三处越界推论（非业务代码）：

1. Memory owner 与手机 workspace subject 是不同身份域，不能要求相等。先查已有绑定与 resident 身份优先级，全部缺省才用 chiyo；身份来源冲突停止。
2. 新绑定空库仅表示其中没有可召回记录，不能说“她没有过去”；列表/上下文健康空结果与不存在 id 的 NOT_FOUND 分开。
3. 入口不支持迁移不能推导“只能删库重建”。禁止删库/重指向，遇迁移需求停问。

两份报告中的现场路径、UNBOUND、服务/lease、账本状态仍未本次复测；代码层面的入口事实由只读 worker 给出，父级未自行通读业务实现。

## 父级实际验证

按两份原任务及补充父级验收任务 `.agents/worker-runs/20261022-memory-binding-parent-acceptance-task.md`：

- 两份文档长度和必备节断言：分别 ENTRY_AUDIT_OK / SAFETY_AUDIT_OK，shell0；纠正后两份 STRUCTURE_OK。
- 新文件行尾空白扫描：AUDIT_WHITESPACE_OK，shell0。原 `git diff --check` 不覆盖未跟踪报告，不能单独拿它当新文件空白证据，因此补了精确扫描。
- 检查点 JSON、8000字符界限、revision95、两 writer done：CHECKPOINT_OK，shell0。
- `git diff --check` 限 NOW/current/两报告：shell0；Git 提示 NOW/current 的 CRLF 将在下次触碰时转 LF，无错误。

未跑离线旧绿套件、notes 门禁、全库、浏览器。只读报告与管理状态变更没有核心实现/新决策 note 交付，因此不把旧86篇门禁重跑成新证据。

## 停点与下一工单

依 `D:/CLAUDE.md` 一个工单结束即汇报，不自动开启下一工单。此次前置核对到此结束，无活 dispatch。
下一次“继续”执行 0 产品调用的同实例绑定：先写完整任务书，WSL `/root/nyairo-day-20261022`，同 run:one-day；先核对进程/lease/owner/文件/96配置/账本/材料与Activity基线，再仅通过既有入口显式启用；验收 READY 回执、备份、三个环境名增量、二次幂等与只读健康。父级复跑工单列的只读验收，不重跑启用或产品段。

未操作 WSL 主实例，无新增产品调用；共同13/96、余83仅沿用历史，未现场复测。Memory 历史 UNBOUND、understanding UNKNOWN、V-C 恢复 FAIL、G1 未闭合、24h 未做全部保留。未提交、推送、部署。
