# Agent Note: 探针成功退出码不再被吞，日常实例换用真实公版节选

Status: implemented

## Problem

两件事叠在同一个验收口上：

1. `scripts/day_capability_probe.py` 在 `_run_report` / `status_probe` / `cmd_*` 里写成
   `int(value or EXIT_REFUSED)`。成功段 `exit_code=0` 是假值，于是被改写成 `1`；真实 owner
   明明正常收尾，shell 却看到红灯。反过来，缺失或 `None` 的退出码也没有独立的失败关闭路径，
   只能靠这个错误表达式“碰巧”报 1。
2. 主实例 `/root/nyairo-day-20261022` 的阅读材料是 `prepare` 写入的合成验收夹具。第一段真机
   日常确实读了它两页，随后把种子作品 ARCHIVED；夹具自述“不是她的私人阅读”。这条链证明了
   阅读入口和翻页，但不能当作她的真实日常阅读，也不该继续拿合成文本消耗后续窗口。

用户要求：先修退出码，把合成夹具换成真实可读材料，再续跑；只交工具与离线验证，真实实例的
修改与 180 秒续跑由父级验收后执行。

## Decision

1. **退出码只认既有入口的真实 `exit_code`。** 新增 `_exit_code(value, default=1)`：`0` 原样
   保留，非零原样透传，缺失/`None`/不可转换才失败关闭为 `1`。`_run_report`、`status_probe`、
   `cmd_prepare/status/run` 全部走它；`ok: true` 本身从不被当作成功码的依据。旧测试没有断言
   “成功报 1”，因此不需要纠正错误断言。
2. **新材料由真实 Supply owner 创建。** 新增父级一次性工具 `scripts/day_reading_material.py`，
   `install --root <existing-root>` 只调用既有 `LifeArtifactPort.create_artifact`
   （owner 的 `create_artifact` 命令，首版由 owner 自己写）。不建 store、不直连 SQLite/JSONL、
   不改 canonical 表，也不新增 owner。
3. **材料是陶渊明《桃花源记》开篇节选，东晋原作、公版、逐字使用已核对的开篇，不冒充全文。**
   标题 `陶渊明《桃花源记》开篇` 带作者；ManagedArtifact 没有来源列，完整来源 URL、
   “节选/公版/operator 提供/非 AI 生成”记在工具回执与报告里，不塞进正文。不规定她必须读，
   也不替她写阅读位置。
4. **只改阅读候选的绑定元数据。** 安装把 Life 实例文件里的 `reading_artifact_id`、
   `CHIYO_LIFE_READING_ARTIFACT_ID` 和手机实例文件里的 `reading_artifact_id` 指向新 artifact；
   写前各留字节备份。旧材料、旧 `source_id` 的取得/停点记录、私人事项、请求账本、run id、
   预算上限全部不碰。因为新 artifact 有新 id，旧阅读凭据不会指向新正文。
5. **幂等键由材料内容派生。** 同一标题+正文+subject+workspace 得到同一个
   `operation_key`；第二次 `install` 先读回执、真实 owner 回读标题/正文/版本，并确认三处绑定
   都仍指向它，直接返回 `ALREADY_INSTALLED`，不新建 artifact、不改文件、不清游标、不重放动作。
   首次建件前先查 owner 的操作行，冲突就拒绝而不是换键。
6. **`verify --root` 纯读。** 它用真实 owner 回读新材料的标题/正文哈希/版本，核对三处绑定，
   回读旧材料 head 与版本数证明旧历史仍在，核对同一 run id、预算上限不变、账本计数未下降；
   不建件、不写文件、不发模型请求。为避免 `LifeRequestBudget.view()` 以 `a+` 打开锁文件，
   verify 直接按既有账本 JSON 形状只读字段，不创建任何锁文件。
7. **服务归属单一。** 实例当前 socket 上有活的 owner 就直接复用、不拥有也不停；否则用既有
   `phone.demo` 子进程助手启动**一个**自己的 Supply owner，`finally` 只停自己启动的 pid。
   既有 lease 被活进程持有时，安装在任何写之前拒绝；不抢 lease、不启动 World/resident。
8. **不改 `prepare` 对新实例的合成材料。** 本轮只换既有主实例的阅读候选；`prepare` 的合成文本
   仍明确标注为验收夹具，避免把两件事混成一次隐性改写。

## Alternatives considered

- **在每个 `or EXIT_REFUSED` 处各写一个 `if value is None`。** 立论是改动面最小、不新增函数。
  否决：四处语义必须一致，散写迟早漏一处；集中成一个 helper 才能同时钉住 0、非 0、缺失、
  `None` 四种情况，并让测试直接覆盖命令与 CLI 边界。
- **原地给旧合成 artifact 提交一版新正文。** 立论是绑定 id 不变，改动最少。否决：旧的取得证据
  和停点都按 `source_id + source_revision` 记账，原地换文会让旧阅读凭据指向新正文，正是任务书
  明确禁止的“旧凭据指向新正文”。
- **新建一个阅读材料 owner/store 或把正文塞进私人事项文档。** 立论是不用碰既有 artifact 绑定。
  否决：材料正文的唯一 owner 已经是 ManagedArtifact；另造 store 等于第二份真相，塞进 PDI 又会
  把材料与阅读记录混成一个写面。
- **在安装时顺便写一个阅读位置或播一条“请读它”的候选提示。** 立论是保证下一次窗口真的会读。
  否决：材料由 operator 提供，不是她的选择；安装工具替她决定“必须读”会伪造经历。候选绑定
  改变本身就是机会来源，读不读由她自己的 tick 决定。
- **直接改主实例数据库/实例文件而不启动 owner。** 立论是快。否决：那会绕过 owner 的授权、
  idempotency 与 fencing；本条工具只通过 owner 建件，绑定文件是实例元数据而非 canonical 真相。

## Consequences

- 探针成功段现在如实返回 0；父级可以用 shell 退出码判读，同时仍以 JSON 的 `status/ok` 看业务
  结论。缺失/`None` 仍失败关闭，不把“没答案”读成成功。
- 下一次 180 秒窗口的阅读候选会指向真实的公版节选；旧合成材料与旧阅读记录仍在 Supply/PDI 里，
  可回看，不再被当作“她的私人阅读”。
- 材料身份的上限：owner 只存标题与正文，来源 URL 只在工具回执里；本工具不证明模型会选它、
  更不证明模型真的读了或理解了。旧 `stop_point` 仍可能指向已归档的种子作品，本轮不修。
- 工具只做一次性安装与只读核对；它不是产品入口，也没有把“安装成功”升级成“日常已通过”。
  真实实例的安装、续跑与请求增量由父级执行和验收。
