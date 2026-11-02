# PAUSE 材料差额：240 → 680 与静态系统提示

日期：2026-10-30。状态：指定监护按三条标签限定接收，本单收口。仅父级有限文本提取/字符串计算，未派模型、未运行测试或全 prompt 审计；本次收口不再修改业务。

统一标签：改前是重建，不是独立快照；可见增量是「新增事实句 + 恢复被240截断的完整规则尾段」，不是只加一句；暂停视图与每轮静态底稿同时变化，输入变了，行为是否变、为什么变，未知，不作单因素因果归因。

## how 改前与改后（逐字）

来源限定：当前 `pause_condition_view` 字面量及其拼接顺序，adapter 的空白归一化后截断。改前是删除本单新增的 abandon_cost、恢复240上限的**重建文本**；没有独立改前源码快照，因此不把重建说成历史发送现场，更不冒 a/b/c 的逐请求原文。以下仅公开静态说明，不含任何 prompt 私文或运行数据。

### work/body：改前240字符

```text
choose PAUSE and copy condition_ref into reevaluation_hint_ref: the Activity stays PAUSED and a successful PAUSE releases the execution slot for another activity, the owner alone decides when the condition holds, only then is an eligibility
```

### work/body：改后538字符（680内完整保留）

```text
choose PAUSE and copy condition_ref into reevaluation_hint_ref: the Activity stays PAUSED and a successful PAUSE releases the execution slot for another activity, the owner alone decides when the condition holds, only then is an eligibility OPEN and nothing resumes by itself, and a later Decision may choose RESUME; a background RESUME never preempts another foreground activity While the Activity is PAUSED, ABANDON is not available through the formal decision path; if you want to abandon it, make that choice while it is still ACTIVE.
```

### park：改前240字符

与 work/body 上述240字符逐字相同；park 后缀原本全在截断点之后。

### park：改后644字符（680内完整保留）

```text
choose PAUSE and copy condition_ref into reevaluation_hint_ref: the Activity stays PAUSED and a successful PAUSE releases the execution slot for another activity, the owner alone decides when the condition holds, only then is an eligibility OPEN and nothing resumes by itself, and a later Decision may choose RESUME; a background RESUME never preempts another foreground activity While the Activity is PAUSED, ABANDON is not available through the formal decision path; if you want to abandon it, make that choice while it is still ACTIVE.; the instant comes from this same answer's later_item with park_current=true -- there is no default timer
```

上面 `ACTIVE.;` 是当前字面拼接实际结果，本次不顺手改代码。

### 无 basis：改前167字符（原240内已完整）

```text
a conditional PAUSE needs a condition this round can really check; without one PAUSE is still allowed, but it is unconditional and can never become a resume candidate.
```

### 无 basis：改后326字符（680内完整保留）

```text
a conditional PAUSE needs a condition this round can really check; without one PAUSE is still allowed, but it is unconditional and can never become a resume candidate. While the Activity is PAUSED, ABANDON is not available through the formal decision path; if you want to abandon it, make that choice while it is still ACTIVE.
```

无 basis 原有的 `can never become a resume candidate` 也在这里逐字列出：它是既有说明，不是本次放宽截断新露出的内容；本次未将它改成其他永久性承诺。

## 实际多出的内容，不只两项

1. 新增事实句：PAUSED 期间正式 ABANDON 不可用，想放弃须在 ACTIVE 时决定。三个分支均新增。
2. **原来被240截断的通用尾段**：` OPEN and nothing resumes by itself, and a later Decision may choose RESUME; a background RESUME never preempts another foreground activity`。work/body 与 park 都新增可见。这不只是 park 说明：它明确资格、非自动恢复、自选 RESUME、后台不抢占四个既有规则。
3. park 另新增可见的旧后缀：`; the instant comes from this same answer's later_item with park_current=true -- there is no default timer`。
4. 无 basis 没有原先隐藏的尾段；只新增第1项。现有三个分支最长644字符，当前680上限不再截断它们。

因此不能把本次输入差异记成「只有一句事实与 park 说明」。第2项也是实际新增可见内容；它不是新规则，但确实改变模型输入。没有在这三个已提取分支发现另外的 how 内容；这不是对任意外部传入该字段文本的穷尽保证。

## 静态系统提示：独立、每轮基础材料变更

`agency_cognition_adapter.py` 中描述 PAUSE 的静态系统提示已由“保留前台”对齐为“释放执行槽、另一活动可开始，保留原停点”。这不是模块注释，不能混在注释修正里。它属于使用该正式 adapter 构造请求时每轮共有的基础说明，不只影响恰好含可绑条件的视图；离线/未发请求不等于已发送给模型。

所以本次不只是一个局部条件块多一句话，而是**每轮基础材料同时变化**。之后使用新代码的观测与 a/b/c 使用的旧输入不同，不能当同输入/同策略横向比较，更不能把行为差异唯一归因于放弃提醒。仍不证明模型已经理解或会因此继续做，不恢复 r23 已不合法的 CONTINUE。

## 保留的验证与失败史

worker 初次短测试因夹具缺 `causal_parent_refs` / `provenance_refs` 报 ERROR；补夹具后通过。父级独立原模块1例0.002秒OK、exit0。此次差额核对没有重跑这个已绿模块，没有新增产品调用；占额141/300不变。原结果见 [文案单结果](../../.agents/worker-runs/20261030-pause-abandon-cost-copy-result.md)（本地ignored交付记录，不作为公开发行附件）。

指定监护已接收全部具名差额：240把规则截成半句话，放宽恢复完整表述，不引入新政策；但输入变化必须完整标注。新增事实句 + 恢复被240截断的完整规则尾段，与静态系统底稿变更同时发生，不能挑其中一项解释后续行为。本单到此收口，`ACTIVE.;` 留作已知瑕疵，不顺手修；不派新任务、不准备或启动任何真实观测，159剩余额度不构成新批授权。
