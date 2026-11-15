# 0.7 第九阶段：私人研究闭环、日记/相册与六域时间线、S03 声明与共同实践真来源

Status: implemented

## Problem

前八阶段已把日常链、阅读、购买、布置、睡眠多世界和「自己排今天」跑通，但 v0.7 默认新实例
仍缺四块端到端证据。第一，研究作品只有普通 `QUESTION_FROM_MATERIAL` 小册子链，真实 research
seam 从未在**同一 root** 上被正式 START→CONTINUE→ABANDON 走完，也没证明 cycle 真正交给 writer
的 `research_context` 与 `earlier_versions` 里有可读版本片段，而不是只有键名；零命中与 seam 未接
更是两回事。第二，手机私密日记与相册导入没有和「可续写作品」划清边界：相册 manifest 是不是会
混进 `PrivateProjectService` 作品列表或 `ArtifactContinuationSource` 候选，六类真实记录能不能在
同一个业务日里合并进时间线，聚合视图会不会带出日记正文/相册 caption/图片字节，都未验证。第三，
S03 的 OBSERVED/REPORTED/INFERRED、同槽冲突 DISPUTED、维护后 STALE 没有证明能进**正式 Decision
实收**的 `situation_context['claims']`，也没证明 claims 操作本身不越权写 World/Memory。第四，
共同实践虽然已有 owner，但默认 `resolve_shared_practice` 与 `LifeSourceVerifier` 没有用本实例
真实的 decision/action/adoption/inbox 引用逐类验过，伪造来源是否具名拒绝也未证明。

## Decision

全部接在默认主 demo 的**同一 root、同一实例**上（`_run_stage9_chain(demo, booklet, booklet_bare)`），
不另起第三 root、不手造 fake seam/owner、不改两个锚点。四段都用既有 owner 与正式 Decision 路径：

1. **研究闭环**：用本实例默认装配里真实存在的 research seam（`research_workspace_service` + cycle
   原生接线，不改 assembly）。先由 operator 显式导入一份本地原创阅读材料（`source_kind=user_supplied`，
   `origin=operator:stage9:local_original`，声明来源、不冒亲历），经正常 private items/Decision 产生
   unclear 疑问，让 `project_options`→AG-0 真正给出 `RESEARCH_FROM_QUESTION` popt；材料命中按
   `artifact_id + source_revision`，不是只比标题或直接造 candidate。ScriptedDailyCognition 正式
   START 该 popt，由 `ResearchWorkspaceService` owner 创建带 `nyairo-research:v1` marker 的研究
   （初版 v2，`source_refs` 精确引用当时可读版本，调用 service 生成 marker 而非手拼），AR-0 写新版本，
   再正式 CONTINUE 到 v3，最后 ABANDON 释放前台。marker 与旧 `source_refs` 不被后续 writer 抹掉；
   幂等/CAS/UNKNOWN 沿现机制。`ModelContinuationWriter` 新增只读的 `last_context`，记录它**实际收到**
   的上下文，demo 用脚本 writer 检查 `research_context`（含 own research 与引用版本片段）和
   `earlier_versions`（前版引用）确有内容，而不是另造 dict。研究资料与结果只当文档上下文，不进
   `previous_experiences`、不自动成为 Memory 确证或稳定信念；零命中走 `NO_MATERIAL_UNCERTAINTY`，
   与 seam 未接的 `NOT_WIRED` 分开对照（工作台真有材料但筛不到，不靠把 seam 置 None 冒充无资料）。
   换版负控只在新材料上做：新 revision 落地后旧版本读不到，来源判 STALE。
2. **私人日记/相册与时间线**：在主链同一 root 用真实 `PhoneService.diary_write` 写今日私密日记，
   用真实 `PhoneService`/`PhoneAlbum.import_image` 导入本地确定性合成的 1×1 PNG（非真实照片、
   不开 SD/图片 API、不下载素材），走 owner 的权限/结构校验/operation 幂等，同 key 重放
   `already_applied`，不直接写 media 或清单。`is_album_manifest` 在
   `ArtifactContinuationSource.refresh` 里把相册 artifact 以具名原因
   `ALBUM_MANIFEST_NOT_A_WORK` 排除，同时验证它不进普通 `PrivateProjectService` 作品列表；不是把
   所有 JSON 一律排除，真实普通作品/研究仍有合法读口。真实 `day_timeline.timeline_envelope` 在
   同一实例上合出 activity、plan、world_action、wallet_transaction、diary_write、album_saved 六类
   各至少一条，沿用 Stage8 既有计划/交易/Activity/World 记录。operator 视图默认 `skeleton_only`；
   日记正文/标题、相册 caption/scene/prompt、图片 bytes/base64 在聚合输出不出现，用私文哨兵做负控
   且不 dump 内容；actor=model 走现有能力/读口，带 `detail_ref` 但聚合仍不复制正文/字节。不同
   `time_basis` 分域保留；读前 `flush/journal settle` 与纯读分离。根因是 owner 的 serve 循环每服务
   一条连接就会顺手跑一次 `tick()`，lived-needs 的 live-window 结算会写 `body/runtime.json` 与
   `body/transitions.jsonl`（墙钟驱动，与手机读口无关）；因此只读窗口内暂停这个后台 tick（真 owner、
   真 socket 不变，经一次 owner 自己的 `ping` 确认在途 tick 已结束，读完立即恢复），读前后再对状态与
   media 必要文件做字节快照（非 SHA）零变化；只读重开不跑迁移/建表、不新 timeline store、不补写事件。
3. **S03 claims**：使用本实例真实默认 `claims_service`。OBSERVED 来自真实 World snapshot/观察入口
   与 world revision/receipt；REPORTED 显式 `operator_report:`/`report:` 来源；INFERRED 用真实已存在
   claim 的 BASIS。制造合法同 slot 冲突使 DISPUTED，过期走既有 maintenance 并由**注入业务 clock**
   推进到 STALE（不改系统墙钟）；`current_view` 保持纯读。断言两次正式 Decision 的
   `situation_context['claims']` 实收 OBSERVED/REPORTED/INFERRED 与 DISPUTED/STALE，而不是只读
   service 内部视图。claims 操作前后对 3 类底层文件做字节快照零变化（同一窗口同样暂停后台 tick，
   免得把 owner 读 World 快照顺带触发的 tick 写算成 claims 越权），保留「当前声明≠World 真相」，
   不改 World 位置/Memory 准入。
4. **共同实践真来源**：默认 `resolve_shared_practice` + 真实 `LifeSourceVerifier`（绑定本实例
   inbox/decision/action/adoption 读口，绝不 AlwaysTrue 或 FakeActionRead/FakeDecisionRead）。用本
   实例已经发生的真实 decision/action/adoption 与经正规 local contact 导入的 inbox 事件，四类来源
   逐类验证并各落一条合法 life 记录（同一共同实践作品）；伪造/不存在的四类来源逐类具名拒绝
   （`SOURCE...`），owner head/version 与 response/contribution 计数不变。用户来话不能冒充主体回应
   （`SHARED_PRACTICE_ACTOR_NOT_ALLOWED`）；adoption 不冒已执行、真实 source_ref 不自动证明具体回应
   内容为真。sharing 锚点未改。
5. **离线守卫**：demo `main()` 进程内硬拒 AF_INET/AF_INET6/DNS/`create_connection`，保留 AF_UNIX，
   并在 stdout 打安全 marker；只护本次 demo 进程，不改全局网络、施工网关或系统配置。

单测另留一个传 `demo=None` 的自建实例 helper 走同一段真链；原 67 例断言与主链 1—8/山地步骤
全保留，新增 2 例（`test_stage9_real_chain_completes`、`test_album_classifier_does_not_claim_unrelated_json`）。

## Alternatives considered

- **手造 fake research seam，或用第三 root/新建 owner 跑研究闭环。** 最强理由：绕开主链几十轮真实
  Decision 的交错，建根最快、旧 67 例最不容易被碰红。否决：任务与用户明确要求同一实例默认装配的
  真实 seam；fake seam 证明不了生产接线，第三 root 等于用新实例冒充主链闭环，断言全绿也不算交付。
- **把相册 manifest 当普通 JSON 全部排除，或在 helper 里只断言分类函数。** 最强理由：改动最小、
  不会误伤续写候选。否决：会把真实 JSON 作品一起排除；且只有真实 `materialize/refresh` 才能证明
  候选隔离，纯 helper 断言证明不了 owner 读口与候选取舍。改为按 `is_album_manifest` 具名跳过，
  并保留普通作品的合法读口。
- **直接读 `claims_service.current_view` 当证据。** 最强理由：无需跑两轮正式 Decision，快且稳。
  否决：那证明不了 Decision 层真的收到有界 claims，正是本阶段要验的接线；必须从 Decision 实收
  `situation_context['claims']` 检查。
- **用 AlwaysTrue verifier 或 FakeActionRead/FakeDecisionRead 让共同实践全绿。** 最强理由：省掉
  真实 owner 依赖，四类来源都「通过」。否决：那正是本阶段要排除的假证据；必须用本实例真实读口，
  并让伪造来源具名拒绝、计数不变。
- **为凑 STALE 改系统墙钟或直接写 World 位置。** 最强理由：一行就能让 claim 过期。否决：改墙钟
  会影响其他 owner 的真实落账时域，写 World 更是越权篡改真相；改用既有 maintenance + 注入业务钟。
- **用全局 sitecustomize/site-packages 装网络守卫。** 最强理由：所有子进程都覆盖。否决：会改
  全局环境、影响施工网关与其它任务；只在本 demo 进程内装，AF_UNIX 保持可用。
- **把只读窗口里的 Body 变化当作「后台正常写、不算」，或删掉 body 两个快照文件/放宽断言，或加
  sleep 赌时序。** 最强理由：改动最小、只读断言立刻绿。否决：那等于取消「纯读零写」这条本阶段要
  证明的性质，也掩盖了真实调用者；正确做法是具名定位到 owner serve 循环的连接后 tick，并在读窗口
  合法暂停它，保留真 owner 与快照集合。

## Consequences

- 现在同一实例上可证明：研究作品 START→CONTINUE→ABANDON 走真实 marker/版本，旧 marker 与
  `source_refs` 保留，writer 实收 `research_context`/`earlier_versions` 有真实版本片段；零命中
  与 `NOT_WIRED` 分开，换版后旧版读不到且来源 STALE。日记/相册经真实 owner 写入且幂等，相册不进
  作品列表与续写候选；六域时间线两视角骨架只读、零私文零字节，读前后 47 个状态+media 文件字节零变。
  S03 五类状态进正式 Decision 实收，claims 窗口 3 类底层文件零变；共同实践四类真来源落账、四类
  伪造来源具名拒绝、用户来话不能替主体回应。demo 进程内 AF_INET/AF_INET6/DNS/连接全部拒绝且
  AF_UNIX 可用。
- 代价：主 demo 更长（研究/媒体/claims/实践四段真实 owner 往返），`ModelContinuationWriter` 多一个
  只读 `last_context` 记录位；`PhoneService` 增加可选 `world_subject`，只给 C 的 World/economy
  facade 用，Supply/私文主体不变（默认仍是原 subject，旧调用不受影响）。
- 边界：demo/test 的 Decision/内容/阅读响应都是脚本替身，`provider=stub`；1×1 PNG 是本地合成，
  不是真实相机照片或模型创作；inbox 是明确脚本外部刺激，不证明独立社会反馈；业务钟是注入机械
  推进；不宣称 24h/crash/自然共处。产品模型请求 0，未改权限/默认开关/预算/候选 16/Memory 准入/
  Body 阈值/对外协议/schema/依赖。两个锚点 `autonomous_life_assembly.py`、`life_sharing_service.py`
  字节未变（SHA256 相对 Stage9 baseline 相等）。
- 父级首轮红与定点返修（2026-11-10）：父级真实命令首轮
  `tests.test_v07_daily_life` 在 stage9 只读窗口报 `body/runtime.json`+`body/transitions.jsonl`
  变化（Ran 69 / 94.088s / errors=1）。返修只动 `scripts/v07_daily_life_demo.py`：新增
  `_owner_tick_frozen` 在 stage8 手机只读、stage9 两视角时间线、claims 越权守卫三个窗口内暂停
  owner serve 循环在连接后触发的 tick（一次 `ping` 保证在途 tick 结束），不删快照、不改 Body 阈值/
  状态、不换 fake owner。返修终稿 `tests.test_v07_daily_life` 串行三次各 69 例 exit0，耗时
  89.674s / 95.919s / 92.479s；完整 demo exit0，默认主链/山地/Stage8/Stage9 全跑完；日志
  `.agents/worker-runs/20261110-v07-stage9-readonly-fix-unit-{1,2,3}.log`、`-demo.log`，
  返修前的红日志保留为 `-unit-1-red1.log`。两锚点 SHA256 仍与 Stage9 baseline 相等。这不代替
  父级独立验收。
- 父级最终独立验收（2026-11-10）：`tests.test_v07_daily_life` 串行连续三次各 69 例 OK/exit0，
  78.411s / 79.822s / 79.285s；完整 `scripts/v07_daily_life_demo.py` exit0，默认主链、山地第二世界、
  Stage8 与 Stage9 全部跑完。研究 marker/版本来源与两个写作上下文、零命中/STALE 对照、日记相册隔离、
  六域时间线两视角隐私、S03 来源/冲突/过期和共同实践四类真来源/四类伪造拒绝均通过。
  手机读窗口的 47 个状态与 media 文件字节不变，包含两个 Body 文件；该结论以验证窗口内暂停并排空
  **本实例 owner tick** 为条件，真实 socket/读口未换，不等于生产服务在后台运行时文件永不变化。
  两个冻结锚点 SHA256 与本阶段施工前 baseline 相等。日志为
  `.agents/worker-runs/20261110-v07-stage9-readonly-final-parent-unit-{1,2,3}.log` 与 `-demo.log`，
  父级结果为同目录 `20261110-v07-stage9-final-parent-result.md`；原父级红、返修红与网关 503 历史保留。
  产品模型请求 0、累计 573/1000 余 427，无 commit、发布或旧实例根改动。

相关：研究工作台 [v0.4 research workspace](2026-11-03-v04-research-workspace-phone.md)、
研究上下文 [research-life-context](../architecture/2026-11-06-v04-research-life-context-and-creation.md)、
相册导入 [private album import](2026-10-22-v04-private-album-import.md)、
手机日记 [phone diary](2026-10-26-phone-diary.md) 与
[日记能力](2026-10-27-phone-diary-capability.md)、
时间线 [phone day timeline](2026-10-27-phone-day-timeline.md)、
S03 [claims lifecycle](2026-10-27-s03-claims-lifecycle.md)、
共同实践 [service artifact owner](2026-11-23-v04-shared-practice-service-artifact-owner.md) 与
[life continuation](2026-10-20-v04-shared-practice-life-continuation.md)、
同实例前序 [v0.7 Stage8](2026-11-09-v07-stage8-own-day-phone-needs.md)。
