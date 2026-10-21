# v0.4 父级结果：有内容的相处与可延续共同作品

Status: MECHANISM_AND_LOCAL_BROWSER_PASSED / REAL_MODEL_VALIDATION_FAILED。

结论：正常 owner 机制与真实手机浏览器路径已接通；**自然模型整段目标尚未证明，不报全目标完成**。
国内 DeepSeek 在邀请决定处一次请求后 AG1 INVALID_SCHEMA，没有 committed Decision；已关闭本阶段窗口，不继续刷接纳。

## 1. 目标与边界

同一隔离实例：原私人阅读 → 明确选材邀请与新的接纳决定 → 至少三轮用户输入/主体可见回应 →
双方贡献形成真实作品版本 → 显式结束 → fresh RESUME 原 Activity 实际读下一页 → 同实例关闭重开，
作品、归属、结果与断点可读回，动作不重放。严格原始 JSON 替身证明全正向机制；自然模型行为独立记。

用户明确继续是实施授权；宿主 goal 卡审批状态未查询，不虚称。唯一开发树
`D:/nyairo-rc1-body-backend`，分支 `work/rc1-phone-world-body`，HEAD
`41e818faaa24115fbf43db2977b15585c04144af`。保留全部 dirty、旧目录与原实例；未删除/reset/clean/stage/commit/push/deploy。
施工按用户指定使用国内 WorkBuddy DeepSeek；产品验证 `cn:deepseek-v4.1-flash` 经本地7863路由。
原实例 Gemini/model_env、旧额度与 Memory UNBOUND 未改；生产外发拒绝 provider/kill switch 保持关闭。

## 2. 已落地的连接

- 共同作品是一件既有 ManagedArtifact，经 PrivateProjectService/LifeArtifactPort 写，不另建聊天/作品库。
  A 服务分 user/life 角色，主体写必须核 owner 来源；open 原键同意图跨服务重建幂等，异意图拒绝，
  `operation(operation_key)` 只读 owner receipt、不重发、不另建账。见[服务结果](v04-shared-practice-service-result.md)。
- 接纳前不打断私人活动；只有当前 OPEN 共处会话、owner ACTIVE joint 与对应 OPEN 作品持久绑定时，
  一条来话才触发一次严格内容请求。PAUSED/ENDED/无joint/错绑定不自动回应；内容与 AG1 共用持久预算，
  正式装配透传发送 admission。显式 END 完成 joint、LR4 开资格，新的决定才可恢复原 Activity。
  见[Life结果](v04-shared-practice-life-result.md)。
- 正式 PhoneService 按实例绑定 A 的 user 侧；手机只投邀请/LOCAL_MESSAGE/END 到同一 inbox，
  不 claim、不跑模型、不替她接纳/回应/完成活动。GET 纯读，POST 原 Host/Origin/Sec-Fetch/CSRF 守卫保留。
  主屏可选明确材料、提交三轮、查看实际回应/正文/版本、结束与重载。见[手机结果](v04-shared-practice-phone-result.md) §9。

## 3. 父级发现并修复的两处真实产品缺口

1. **手机回应全被扣空**：工厂将 reading artifact 列进项目 excluded，A 重新核验已共享来源时得到
   PROJECT_ARTIFACT_EXCLUDED，正确治理逻辑因此遮蔽所有派生文本。共同实践工厂现在只排除私人事项台账；
   选定阅读材料仍每次经 owner 核权限/当前版本，非共同实践工件仍不能通过本服务写门。
2. **END 查不到邀请**：手机扫描邀请 result.practice_session_ref，但接受结果早于开作品，没有该字段。
   改为纯读现有协议 co_presence 的作品/邀请/事件绑定，再核 inbox 原邀请确实 ACCEPTED；
   绑定缺失/另一作品/未接纳/不可读均拒绝，手机不补写历史结果、不直接结束 Activity。

`tests/test_v04_shared_practice_phone_transport.py` 新增明确阅读source回归，END测试不再 override 查找方法，
而是落协议绑定后重读、验证纯读零写/错误作品拒绝。旧 codec 固定字段集合遗漏已发布 material_refs：
父级先复现1fail/exit1，再同步一行契约断言，最终模块组合通过。旧“A没有排队”提示同步为真实inbox语义。

## 4. 父级独立完整机制与真实浏览器证据

所有脚本/原始证据在会话 scratch
`C:/Users/28556/.pi-desktop/scratch/966ed2b0-27ad-4c2c-a4b3-9021a8121e37`。

| 验证 | 实际结果与证据 |
|---|---|
| 独立真 Supply/World/runtime/正常 runner + 正式 PhoneService factory | **11 checks / exit0**；`shared-practice-parent-smoke-1792541920374184578.json`，root `/tmp/day-life-parent-j-184578` |
| Edge从主屏真实点击，非DOM/fetch stub | **12 checks / exit0**；`shared-practice-browser-1792542498657.json` 与同名PNG，root `/tmp/day-life-parent-u-115454` |
| Edge中的真实owner推进 | 邀请PENDING≠接纳；fresh接受后3轮各真实内容步骤，作品version1→5→9→13；END完成joint/作品，fresh RESUME同Activity `actv_5fb9deb091213ecf` 实际page2；关闭重开无content重放 |
| UI与隐私 | 回应/双方归属/正文重载保留，业务正文不入localStorage/sessionStorage，390px无溢出，0页面异常 |
| 自有资源关闭 | 浏览器Browser.close；server回执resources_closed=true，WSL PID979不存在；模型fixture资源关闭且进程不存在 |

**这些机制链的决定/内容来自严格原始 JSON 替身，不是自然模型行为。**
父级早期隔离适配 root/home 错误与浏览器等待文案/按钮错误留成独立失败证据，没有修改产品身份闸门来求绿。
浏览器同一夹具的邀请与第1轮已经落地后，仅按真实owner回应数续接，未重复投递已完成动作。

## 5. 独立真实模型结果：失败、封窗、不重试

唯一共享授权上限12 HTTP attempts /900秒，从持久pre-send reservation开始。实际只用 **1 attempt**：

- fixture `/tmp/day-life-parent-n-889258/f-54wdclsl/instance`，原初始page1为操作者脚本准备。
- 用产品 ProductionAgencyCognitionAdapter/AgencyModelClient/正常runner，默认决定输出上限512，
  内容上限700（本次未走到内容），仅隔离transport换为国内桥；没有改strict parser或补造Decision。
- HTTP200 / finish_reason=stop，11419ms，返回模型元数据 `deepseek-v4.1-flash`；**不是合法决定证据**。
- owner安全读面：`datt:e3ed144061185709` FAILED，
  **DECISION_ATTEMPT_FAILED:INVALID_SCHEMA**，retry_count=0；无committed Decision。
- 联系事件 UNKNOWN / DECISION_NOT_COMMITTED；0内容轮、0joint、0共同作品。原Activity
  `actv_0f113385275bdfc7` ACTIVE/page1/checkpoint不变。不是自然拒绝、不是自然接纳。
- 桥attempt1与实例ledger0→1一致，5项计数核对全真；overall **exit2**。
- `shared-practice-model-budget.json` 已 **ENDED_NO_FURTHER_CALLS**，没有退款/重开/追加调用；
  12是上限，不要求用满。资源已关，原实例未启动。

证据：`shared-practice-real-model-1792542709111889258.json`、
`shared-practice-real-model-failure.json`、`shared-practice-model-attempt-1.json`。
仅记录安全状态/长度/身份；凭据、私有prompt、内部推理与raw模型答案不落盘。
桥不具备生产pause发送握手，只用于隔离验收；本次不证明生产cutover或自然连续下午。

## 6. 验证命令与范围

### 父级最终执行

| 范围 | 实际结果 |
|---|---|
| Windows `tests.test_v04_local_contact_port` + `tests.test_v04_shared_practice_phone` + `tests.test_v04_shared_practice_phone_transport` | **67 tests / OK / exit0**，无skip；phone两模块29 |
| WSL `tests.test_v04_shared_practice_life_seam` + `tests.test_v04_shared_practice_service_supply` | **43 tests / OK / exit0**，无skip；含换版、归档派生文本扣留、CAS/原键/纯读对账 |
| Node `tests/test_v04_shared_practice_phone_ui.mjs` | **11 pass /0fail /exit0**；app.js node --check0 |
| 父级修改5个Python文件AST | 全通过；不用py_compile |
| 全局笔记tree/format | **79篇 / exit0 / exit0** |
| git diff --check | **exit0** |
| 保护文件、暂存、tracked删除、stash | 四项输出均空、退出码0；分支/HEAD未变 |

手机组合测试中途一次安全守卫HTTP遇WinError10053，保留为一次连接中止，不改守卫求绿；
同项及新回归定点3项exit0，最终67项组合exit0。历史生成的ignored pyc保留，未擅自删除。

### 作者结果（非父级重复全套）

A契约59、真Supply14；B正常整链3、seam29、social14（WSL0skip）；C手机28与DOM11均已报告exit0。
父级不因为压缩重跑所有旧绿套件。零调用预算守卫5项、关闭工具6项、判定负控8项均exit0。

## 7. 未闭合与诚实边界

- **自然模型整段目标失败/未证明**：AG1 schema挡住邀请，未验证自然三轮内容、共同作品与恢复；本阶段预算已关闭。
- LifeArtifactPort无list_versions：正式历史head-only/complete=false；可证明当前作品与真实版本推进，
  不宣称任意旧版本全文都能从手机取回。
- source_version_id表达用户选材时所见版本，邀请目前只携artifact引用，接受时重读当下来源；
  **不声称跨排队锁定客户端所见版本**。本次机制链来源未在邀请与接受之间变版；变版后写闸门与归档治理另有真Supply验证。
- 作者测 `tests.test_life_v1_integration` 56例有7fail+1error（AR0 closeout/continuity及相关分支），
  无等价baseline、不称历史无关；本次未修该更大分支。private_projects双artifact前缀、先前memory/wait红例仍无本次修复声明。
- 无远程外发、关系进展、稳定人格、24小时/多日自治、生产部署或完整设计完成声明；GET可读不等于远端已收到/理解。

本阶段源码/本地交互机制交付已完成；自然模型资格仍NO_GO。后续若要继续真实行为验证，需先离线定位严格schema不兼容并重新明确授权，不能复用本次已封窗口。

## 8. 用户换端点后的追加真实验证（2026-10-21）

用户指定 `https://gw.929711.xyz/v1` / `cline-pass/deepseek-v4.1-flash`，并明确要求立即验证。
本次独立隔离验证复用正常 Life 场景与严格 parser，只换 transport；旧cn验证账本保持封存。
网关凭据从原实例one-day/model_env只读到内存，不修改文件、不回显、不落盘。

- GET /models：HTTP200，精确model id在列，0completion；这只证明模型被列出。
- 正常runner真实决策：**1 attempt**，约2.400秒返回 `PROVIDER_SERVER_ERROR`；产品transport该分支
  对应HTTP5xx，异常没有保留status属性，故不猜具体500/502/503。不是此次AG1 schema失败。
- AG1 `DECISION_ATTEMPT_FAILED:MODEL_PROVIDER_ERROR`，retry_count0，无committed Decision；
  来访UNKNOWN/DECISION_NOT_COMMITTED，0共同内容轮。原Activity ACTIVE/page1/checkpoint保留。
- 新账本stage `shared-practice-gw-cutover-v04`，上限12/900，实际1；实例ledger0→1，5项记账核对全真。
  验证整体exit2，资源关闭且无本次进程，账本ENDED_NO_FURTHER_CALLS，未重复请求。

证据在同一scratch：`shared-practice-gw-preflight.json`、`shared-practice-gw-attempt-1.json`、
`shared-practice-gw-real-model-1792543783798162326.json`、`shared-practice-gw-model-budget.json`。
fixture `/tmp/day-life-parent-g-162326/f-zd7ndcxc/instance`。未改全局配置/原实例，未部署。

## 9. 用户指定本地 Magpie 的实际验证（2026-10-21）

路由 `http://127.0.0.1:3425/v1`，精确模型 `workbuddy-wg/cn:deepseek-v4.1-flash`。
认证从现有本地调用脚本只读到内存，未写密钥、未改网关或原实例。
GET models200且精确id在列；随后正常Life真实1attempt，HTTP200/stop，7710ms，返回657字符正文。
**本地网关实测可回答，不是网关故障。** AG1则为INVALID_SCHEMA/retry0，没有committed Decision；
当前安全日志只有类别，无法指出具体缺失/多余字段，不猜原因、不放宽parser。
原Activity ACTIVE/page1/checkpoint保留，0共同内容轮；overall exit2。新Magpie账本与实例0→1一致，
账本ENDED_NO_FURTHER_CALLS，自有资源关闭、无残留进程。旧gw/cn结果均不覆盖。
证据同scratch：`shared-practice-magpie-preflight.json`、`shared-practice-magpie-model-attempt-1.json`、
`shared-practice-magpie-real-model-1792544371888091595.json`。fixture
`/tmp/day-life-parent-k-091595/f-1uyn83n1/instance`。尚未证明自然共作成功。

## 10. schema 修复后的真实窗口（2026-10-21，stage `shared-practice-schema-repair-v04`）

`docs/engineering/v04-decision-schema-repair-result.md` 的修复落地后，按父级授权开了一个全新账户
（12 attempts / 900 s，独立 ledger `shared-practice-schema-repair-model-budget.json`），端点与模型仍是
`http://127.0.0.1:3425/v1` / `workbuddy-wg/cn:deepseek-v4.1-flash`；旧 cn/gw/magpie/social/pacing 账本
全部未碰。

**只用了 1 次 attempt**：HTTP200、`stop`、7423 ms、645 字符正文、2974 字符推理。AG-1 仍然拒绝，
但这次实例 journal 里有字段级定因：
`schema_refusal = {failure_class: DECISION_ATTEMPT_FAILED:INVALID_SCHEMA, field: short_rationale,
rule: TOO_LONG}` —— 四次真实窗口里第一次能指名道姓。

**归类**：SYSTEM_PROMPT 本轮已明确写 "at most 280 characters"（开跑前实测为 True），因此这是
**模型不遵守已说明的界**，不是产品契约缺口。按边界不放宽 280、不静默截断、不在已 ENDED 的账本里
重试（三条都会把问题掩盖）。

**结果**：邀请 `UNKNOWN` / `DECISION_NOT_COMMITTED`、无 committed Decision、无 joint Activity、
0 轮共作内容；原私人 Activity 仍 ACTIVE / page1、checkpoint 未变；fixture
`/tmp/day-life-parent-s-574943` 保留；账本 1/12、墙钟 13.2 s、`ENDED_NO_FURTHER_CALLS`；
实例自身 request ledger 0→1；五项会计全真；自有资源关闭、无残留进程。

**结论**：schema 诊断修复已被真实窗口验证（类不再是黑箱）；自然共作链**仍未通过**。剩余 11 次额度
已随 `finally` 永久 ENDED，本阶段不重开、不改账本；继续需要父级另开一个新的有界窗口。

## 11. 简短输出约束修复后的真实窗口（2026-10-21，stage `shared-practice-concise-v04`）

`docs/engineering/v04-decision-schema-repair-result.md` §9 记录的 prompt 改进（公开短理由 vs 内部考虑、
建议 ≤80 字符、明写 characters-not-tokens、280 硬上限不变、null 合法、决定无关的形状示例）落地后，
开了独立账户（12 / 900 s，ledger `shared-practice-concise-model-budget.json`，旧四个账本全未碰）。

**1 次 attempt 的结果**：HTTP200 / `stop` / 整轮 26.7 s；Decision **正常 commit**
`dec:1fc362665f79e6b4`；`short_rationale` **82 字符**且被接受——上一窗口同字段 >280 被整条拒。
即：**长度拒不再发生，schema 修复与简短约束修复都被真实窗口验证**。

**但模型选择 CONTINUE**（继续读她自己的材料页 1），reason_codes
`CURRENT_ACTIVITY_CONTINUITY / CONTINUITY_PREFERRED / CAPACITY_UNKNOWN_DEFERRED / USER_REQUEST_DEFERRED`，
邀请候选落在 `deferred_candidate_refs`；按产品语义"任何别的答案都是拒绝"，邀请 `DECLINED`，
joint 未开始、practice 未打开、0 轮共作内容；原 Activity 仍 ACTIVE / page1、checkpoint 未变。
**这是合法的不接受，我尊重它**：没有重放同一邀请、没有换说法刷接受、没有把 prompt 推向 START。

窗口账：attempts **1/12**、墙钟约 173 s / 900 s、`ENDED_NO_FURTHER_CALLS`（owner 显式关闭，
不是额度耗尽）；实例自身 request ledger 0→1；bridge 与持久预算一致；fixture
`/tmp/day-life-parent-c-029002` 保留；自有资源关闭、无残留进程；prompt/raw/推理未落盘。

**结论**：schema 与简短约束两处产品缺陷都已修好并被真实证据证明；自然共作链**仍未通过**，
剩余阻塞是模型自己的选择（是否接受邀请），不是代码缺陷——不得靠偏置 prompt 或重放事件制造。
另有两条更正/观察写在 §9.4（我的 harness subject 判断错、被拒邀请的 `joint_activity_ref` 字段命名）。
