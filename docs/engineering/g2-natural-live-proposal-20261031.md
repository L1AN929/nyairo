# G2 A自然采集：待本人直接批准的一次运行方案

> 注：本文记录的内部网关地址已脱敏为 `gw.example.invalid`；历史观察、失败与结论保持原样，不表示当时实际使用占位地址。

状态：仅方案；未prepare、未创建正式根、未source密钥、未派启动适配writer。监护转述的「开/G2自然采集」不是本窗口真实运行授权。推荐保留生产规则，不转B造历史。本人若批准，范围须包含必要scripts启动适配、相关离线验收和下面唯一一次真实run；绿灯前不得prepare。完整G1仍未过，本方案不因本次G1限定成功自动越过原G2依赖；是否现在开展这项限定G2实验也须本人明确选择。

## 身份与现场新鲜度

- 批次：`g2-natural-20261031-a`。
- 实验根：`/root/nyairo-g2-natural-20261031-a`。
- 主实例：`/root/nyairo-g2-natural-20261031-a/run/main`。
- run：`run:g2-natural-20261031-a`；domain：`g2:natural:20261031-a`。
- Memory authority：`/root/nyairo-g2-natural-20261031-a/run/memory-authority`（现成接口能否接受此布局须离线核；不可接受即STOP，不临场换路径）。
- 承接分支父目录：`/root/nyairo-g2-natural-20261031-a/run/branches`，隔离A/B子根；同一本总预算账位于实验根run内，不能分支复制后各自续杯。
- 2026-10-31 18:25 CST只读实测以上根/main/authority/branches均ABSENT（同时检查路径与symlink）；Windows相关派工/产品Python匹配空、WSL相关runtime Python匹配空，两侧父gate UNSET。仅具名进程匹配，不冒全机任意进程/lease证明。开跑前必须重新实测，当前ABSENT不是预占。

## 预算与时间硬限

- 沿原建议：自然采集≤8＋影子40（20对）＋承接≤4（每臂≤2）＋诊断0＝新增硬cap52。Decision、内容及失败预留都算；阶段不挪额、失败不退、重开不重置；第53请求在发送前硬拒。
- 当前保守history为**185**（184确认＋相机未知1），不是168。最坏185＋52＝**237/300**；当前余115可以覆盖52，仍余**63**。168→220是f之前旧口径，不得用于新启动。
- 自然采集沿原**1800真实秒、最多31次纯读、每60真实秒至多一次**，不清quiet、不快进；前台维护和条件满足沿正常runner/业务时钟。无变化允许零请求。
- 提议整批唯一run总硬限**5400真实秒**（含采集、冻结、40影子、最多4承接及业务推进）；全部阶段共享同一起点monotonic总窗，各次发送还受2026-11-01 01:00 CST绝对截止，取更早者。1800是采集窗口，不是声称全部52请求都能在1800内完成。5400为此次新增待批总窗，不是原预登记已有批准。
- 不启动超过剩余硬时间的调用；超时不重试、不追赶。到截止关闭发送锁并停止业务推进，按已有正规路径收尾自有服务；正在结束的传输/进程收尾如跨过时限如实记，不声称所有进程瞬时消失。40次不完整就不计算完整比较结论。

## 执行顺序与出口

1. 自然采集只沿正式runtime/runner，模型自己决定、自己生成内容。H机械锁首个owner确认version_created且对应正式M0经历可读的精确作品/版本，不挑事后最有利样本；无H到窗口/预算终点为`NO_EFFECTIVE_HISTORY`。
2. 有H仍允许正常自然换事/静默/有条件PAUSE，到结构门真确认H与另一不同作品都可执行且NO_ACTION合法才冻结；不替模型指定PAUSE、时刻或资格。有H但终点仍不可比为`SCENARIO_NOT_IDENTIFIABLE`。本次时间夹具双合法仅证明机制，不能挪入真实采集造场景。
3. 冻结后按原20对固定AB/BA顺序发40次影子请求，只改历史text；正式最终渲染请求其余差异发送前拒，治理扣留/裁剪即干预未送达。影子不提交Decision、不改主Activity/Action/M0；统计沿原精确双侧McNemar p<0.05且绝对比例差≥0.30。完整数据无检出不等长期记忆无效。
4. 隔离承接最多4请求，沿原影子选择与同一冻结目标映射，不将RESUME场景硬改为CONTINUE。当前离线已验CONTINUE映射不自动证明RESUME映射；现成合同无法承接则具名`NOT_RUN`，不改生产合同求绿。
5. **任何阶段首次传输/供应商失败立即持久停批**，两发送口共用锁，不自动retry/fallback、不补剩余额度、不换root/run重来、不做第二次run。结果`BATCH_ABORTED`并记具名故障；统计为`NOT_TESTED_INCOMPLETE`，不是NO_EFFECTIVE_HISTORY或未检出。已形成H/效果保留不回滚。f实际是17预留后失败停、余3未用，不是证实3次传输失败；成功概率未知，接受本次早停。

## 隔离、参数和启动阻点

- World gate显式off，父env不patch；不启动不必要World服务。所有相关World/Body homes、registry、action lifecycle/状态、Memory evidence/controls/authority及分支实例路径必须解析在本实验根run内。共享authority例外若不能满足本批隔离，STOP，不改Memory准入/信任合同；本地同根authority布局不宣称V-C独立新鲜度。
- 仓库默认lifecycle现场为18584 bytes、mtime_ns=1793412031178025500；正式执行前后核零新增，另外所有相关默认写面须在适配工单圈齐，不凭单一台账证明全隔离。发现新写先停，保留产物不删不回滚。
- provider沿现有custom / https://gw.example.invalid/v1 / cn:deepseek-v4.1-flash，temperature0、stream=false、Decision cap4096/content2048；凭证只获准run的同shell source，不输出、不改配置。不换网关探路。
- **不是现成可run**：g2_next_offline.py真实入口仍拒绝；自然采集→冻结→真实影子→可选承接的统一持久身份/预算/时间/失败锁/隔离需新scripts封闭启动适配。现成clone_branch runtime_env硬绑旧RUN_ID，须离线解决新身份接缝，不能把新实验冒旧run或改旧实验root。若必须改生产/owner/恢复合同，具名STOP另请本人，不以批准一次运行代替新合同批准。
- 适配只新scripts/profile及其相关离线用例、原新说明/note；父级只固定新入口离线验收一次，不重旧绿/全库。绿后再核fresh/相关并发/占额/父gate，首次prepare→audit(startnull/ledger0/history185及全部身份正确)→唯一run；任何漂移停报，不source/run试错。

**本人批准题：是否批准按以上范围（含必要启动适配、总窗5400秒/采集1800秒/新增cap52、接受故障即整批结束）开展一次限定G2 A实验？** 此文件交付与监护转文均不算批准；本窗口没有本人直接答复之前不施工启动适配、不prepare、不建根、不source、不run。01:00截止不刷新，旧root/默认配置/Memory门/删除/外发/git发布边界不变。
