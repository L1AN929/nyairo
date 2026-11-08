# C：三小时自然观察新批预登记（待批准）

本人直接批准后的唯一C已于2026-11-02 19:26:41.870 CST到20请求结束，run/audit0，非跑满3小时；坐下及四件作品v2已发生，未出门/返程未覆盖，详见[C结果](v06-world-natural-c-result-20261102.md)。最终共同212/300余88，不再prepare/run。下文待批/未运行文字为事前阶段约定，不是当前状态或新许可。B已经结束，未用19次不续用，完整结果见[B报告](v06-world-natural-b-result-20261102.md)。

## 目的与判据

延长新实例可经历的真实时间，观察是否出现新的有效处境、真实模型决定与自然移动/返程；不保证身体变化必然触发请求。B实际检查间隔1分钟，不能把改30分钟说成只延时。C保留1分钟检查/5秒tick，只将观察上限由1800改10800真实秒，不改生产触发/指纹/静默/TTL/候选排序或权限规则。

B唯一请求明确NO_ACTION、MOVE合法而未选；319 NOT_DUE/29 QUIET不是反复模型安静。本批即使仍一次请求，也只能说此环境和窗口未观察到更多有效触发，不能证明没有自然变化来源、身体没有变化、模型不愿出门或系统永久卡死。身体六变量的连续变化是否进入指纹/跨门、既有维护是否执行，要有实际读口证据才能下结论；本次不为制造样本补生产接线。

## 新身份、额度与固定时间建议

- 新封闭key `v06-natural-c`；root `/tmp/nyairo-v06-natural-20261102-c`；独立authority `/tmp/nyairo-v06-natural-20261102-c-memory-authority/authority.db`。run `run:v06-world-natural-20261102-c`，domain `v06:world-natural:20261102-c`。尚未检查这些路径是否存在；获批后首次prepare前必须核含symlink的新鲜性，已有即停，不删除重建。
- 历史保守192/300（191确认+相机未知1），新增硬cap20含Decision、内容及失败预留，共享一本持久账本，最坏212/300余88。无并发新增时才成立，现场漂移就停重核。不退失败、不挪B余额、不补第二run、不换根刷新额度。
- 单natural phase，真实窗口10800秒，check_interval=1分钟、tick=5秒，全部业务与安全/权限/预算时间真实，不压缩日/快进/冻结墙钟，不把检查次数当模型请求配额。
- 建议最晚启动2026-11-02 20:00 CST，停止新提交取started+10800秒与2026-11-02 23:05 CST绝对线较早者；正常收尾最多额外60秒，收尾期间零新请求/Action。最晚启动时正常结束约23:01。过线不启动、不缩窗、不自动改日期或延期。
- 标准新实例、标准World地图、空Memory/既有准入，不导入B的决定/作品/静默状态。无新增材料、任务、回家提醒、operator暂停重启或故障注入；不是空地图，标准世界事实不冒额外注入。不强制MOVE/返程或安排吃睡。

## 模型、gate、隔离与停止

沿B已核的既有custom endpoint与`cn:deepseek-v4.1-flash`，temperature0、非stream、Decision4096/内容2048；不换供应商/模型或重试政策。endpoint只在父级获批真实启动时显式传入，公开文档不留内部地址；密钥仅唯一run shell source既有项目外文件，不读出正文、不写仓库/日志。当前不做网络探测或加载密钥。

`WORLD_BODY_INTENT_MUTATION_GATE=canonical`只映射新C phase/runtime/自有World child环境；父进程与生产默认、旧实例原值保持。World全部bridge写面、Supply、Memory、账本、HOME/socket和独立authority明确隔离；缺失/越界/symlink逃逸前拒。离线scratch与真实root分开，不能调用旧B/e真实入口验证拒绝或延长旧窗口。

达到cap20、10800秒、绝对线、首provider/transport故障、坏账或隔离异常就锁停，不靠补样/清静默凑覆盖。UNKNOWN只对账原execution，不裸重发。既有维护可按真实owner事实结算已经发生的动作，但不得用收尾再提交新步骤回家。自有runtime/World/Supply正规close/shutdown/drain，父级总60秒界限后只终止匹配的本批自有进程树；保留在途/未知和数据，不裸unlink/删除/回滚效果，不冒全部grant撤销。不能排空如实UNKNOWN/STOP。

## 原定实现写集与接缝（实装见末节）

1. 新 `scripts/v06_world_natural_c.py`：封闭offline-check/prepare/audit/run，固定身份、192基线/20上限/10800/最晚启动/绝对线与安全结果。不重造Life循环。
2. 新 `tests/test_v06_world_natural_c.py`：新C局部参数、时间/预算/根/网络/子env/关闭负控；不改旧测试断言。
3. `scripts/g1_real_adoption.py`：仅新增C profile及必要按profile选取的运行参数、自然路由、三处gate/隔离/guard/坏账与总收尾接线；旧a～f及v06 B值、默认、CLI集合和行为不改。不能全局把WINDOW_SECONDS=1800或MAX_TICKS=900改大。
   - 同一C窗口必须用于prepare/prereg、写window、phase、发送前截止、loop、audit、父级等待；尤其Python默认参数早绑定不能漏。
   - 5秒tick的3小时最多约2161轮，C专属tick上限须不被旧900提前截断；不加速tick、不保证到上限。
   - 原trace上限4000可能被两类逐轮记录耗尽，C可单独给6000条观测副本容量，候选来源/排序/模型输入不随之改变；达到容量明确truncated/dropped，不报全记录。旧B容量不动，涉及真实落盘/对外schema新合同则STOP。
   - C自己继承B已验的局部坏账failclosed、离线父/phase/两个owner网络守卫及正常结束后60秒上限，不顺手改其他批。测试业务钟仅限新offline scratch，不持久污染真实C，真实入口参考钟必须写前拒。
4. 本预登记与新 `.agents/notes/proposed/testing/2026-11-02-v06-natural-c-long-window.md`；实现后按已落地离线部分另留implemented事实，真实运行未批仍保proposed。工单、十行结果和安全日志在ignored worker-runs，离线scratch保留。

不动components、旧tests/fixtures、B专属入口/A driver、默认配置、Memory准入/恢复权威、依赖、系统/网络/代理/权限设置、网站/发行物/git发布。若只有新参数接口不足，可在上述scripts接缝内解决；新产品触发规则、权限/状态机/格式合同或写集外修复必须具名停报，不造自然变化源。

## 最小验收及执行批准

C入口已实现，下列固定入口已由worker运行，父级独立检查结果另列，不混作worker证据：

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && /tmp/rc104-venv/bin/python -B scripts/v06_world_natural_c.py offline-check'
```

唯一worker普通3600秒/300轮，只做新离线scratch；父进程、phase和真owner子进程均硬拒外网，Decision使用脚本替身、产品模型零调用；父级随后同入口独立一次。不实际等3小时或复跑A/B/全库，用新局部时钟/纯参数/受控短超时证明10800窗口、旧值不变、20→21发送前拒、过期/重复/错根前拒、实际子env及正式owner动作、总收尾和trace容量边界。脚本替身与时间测试不能冒真实C行为。

建议本轮只批准此最小实现与离线检查。真实prepare/run必须另行本人批准：先重新核无并发/预算192未漂移、正确root/authority新鲜、配置/源码冻结，再唯一prepare/audit零账，之后唯一run；不因离线检查通过自动建正式根或source密钥。run命令仅在获批后由父级执行：

```bash
/tmp/rc104-venv/bin/python -B scripts/v06_world_natural_c.py run --execute-approved --capture-candidates
```

## 观察怎样收口

只读既有安全ID/固定枚举/布尔/计数/时间：每次真实请求及NO_ACTION/DEFER/失败分型，NOT_DUE/QUIET、已存候选合法性与最终结构分层，MOVE选择/正式出发/到达/回程可供给/回程选择/最终位置、各execution票据和结算，最终预算与关闭。完整prompt/completion/reasoning/私文/token/header/env全文不落报告。

若现成安全读口已有身体状态分类/时间/处境变化标记，可在正常tick附带纯读、有界采样，明确采样时点与缺失；不额外造事件/调用认知、不把两次身体值不同直接判指纹不同。拿不到就UNKNOWN，不新增全量身体监控或为解释结果跨生产写集。最终仍只有一次请求就按实际收口，不推缺生活源；有更多请求也不能把它们唯一归因身体。

## 取舍

改30分钟检查可减少轮数，但同时改变B的节奏，不利于判断只延长窗口的结果，因此保留1分钟。保留30分钟窗口成本最小，但B已经显示该窗仅一次请求，延长真实观察是本次选的实验方向；这不是修复结论。单纯无限延长或追加cap可能增加样本，却无法保证有效触发且失控，所以选10800/20固定边界。注入身体变化或修改指纹最易造第二请求，但改变生产行为/实验输入，本方案否决。

三小时新根与B不是同一次冻结状态，provider时段和自然状态不同，本实验不是严格单变量因果比较。自然出门/返程可以未覆盖；完整G1/G2/V-C/24h的原限制不变。当前不支用预算，不安排自动开跑或明日续期。

## 离线实装（2026-11-02，真实运行仍未批准）

本人本窗口直接「批准离线实现」后，已只完成离线部分；真实 `prepare`/`run` 仍未获批准、
未执行，真实 root 与 authority 仍不存在。

- 新 `scripts/v06_world_natural_c.py`（offline-check/prepare/audit/run，固定 C 身份，无任意
  root/时段覆盖）与 `tests/test_v06_world_natural_c.py`；`g1_real_adoption.py` 新增
  `C_PROFILE` 与**按 profile 解析**的窗口/tick/trace helper，旧 a～f 与 B 的值、默认、
  CLI 枚举不动。
- 实际落地的 C 局部参数：窗口 10800 真实秒、tick 上限 3000（覆盖 2160 次 5 秒 tick）、
  trace 行数上限 6000；旧批仍 1800/900/4000。窗口从 prepare/prereg 一直贯通到
  `_write_window`、发送前截止、phase 循环、`CandidateTrace`、audit 与父级超时。
- 离线参考时刻对带启动线的批夹到启动线之前：C 取 `20:00 - 5 分钟 = 19:55`，否则
  C 的离线 start_run 会被自己的最晚启动线拒绝。B 仍是 19:00，旧批仍是截止前 1 小时。
- 固定入口实测 exit 0、44 项全 PASS、18.0 秒：真实 phase 子进程的标准 World READY、
  canonical gate 与根内隔离、脚本 START 的正式 POSE Action（SUCCEEDED/回执/同
  execution APPLIED）、父与两个 owner 的网络四拒一允、正常关闭、20→21 与坏账发送前拒、
  最晚启动/截止/重复/错根前拒、trace 6000 容量与 60 秒收尾参数。产品模型 0 请求。
- 首轮 exit 3，唯一红项是检查代码把 `deadline_basis` 结尾多算一个右括号；修正断言后
  重跑通过，实现未改。证据在 ignored `.agents/worker-runs/20261102-v06-natural-c-offline-*`，
  scratch `/tmp/v06-natural-c-offline-off1kod_` 与首轮 `/tmp/v06-natural-c-offline-ujmepixe` 保留。

上述只是离线接线证据。3 小时真实窗口、真实模型请求、自然移动/返程、极端收尾与完整
G1/G2/V-C/24h 均未验证；真实 C 仍需本人另行批准，批准后仍要先核新鲜/并发/预算 192
未漂移，再唯一 prepare/audit 零账、唯一 run。
