# v0.2 全闭环验收总报告（2026-11-18：Phase A + Phase B）

## 1. 验收范围与证据边界

本报告汇总 `0.2` 总体设计（`13-产品界面与跨模态生活体验设计.md` §7 二十四切片、`14-研发工作包与验收体系.md` §5 十二必备端到端场景与 V-D 消融、`17-工程契约与证据完整设计.md` §5.3–5.4 CP01–CP12 与 §6.2 / Q51 二十反假绿门禁）在 Phase A 与 Phase B 的完整工程收口与实测验证结论。

所有结论严格区分：
- 隔离测试/模拟运行（`tests/test_v02_*` 在 `tempfile.TemporaryDirectory()` 中执行、`llm_calls == 0`）；
- `90` 虚拟天长跑明确标注为 `simulated=True` / `evidence_tier="SIMULATED_90D"`，不冒充真实生产挂钟天数。

---

## 2. Phase A：后端闭环套件（67 tests PASS，已由 `v02-verifier` 独立复核）

运行命令（WSL `/root/nyairo-v051/vendor/hermes/.venv/bin/python -m unittest -v ...`）：

| 套件 | 通过数 | 覆盖与关键验证点 |
|---|---|---|
| `tests/test_v02_cp01_12_kill_and_partial_action.py` | 18/18 PASS | 真实 `subprocess.Popen` + `proc.kill()` / `os._exit(137)` 覆盖 CP01..CP12（含 `NYAIRO_CP12_PHASE=before_commit`/`after_commit`）；SQLite `journal_mode='wal'`、`synchronous=2`、`PRAGMA integrity_check == 'ok'`；Compound 3-child DAG `PARTIAL` 仅重试 `UNKNOWN` 子动作；有界对账耗尽收敛为 `QUIESCED_UNRESOLVED` (`outcome == 'UNKNOWN'`)；`HUMAN_ATTESTED_EXPERIENCE` 与 `PROVIDER_RECEIPT` 分离；`NOT_EXECUTED` 双重证明门禁与负控。 |
| `tests/test_v02_representation_lease_and_forget.py` | 14/14 PASS | `.md` `UNBOUND`/`CURRENT`/`MISSING`/`DIVERGED` 与 `409 BLOCKED_CONFLICT` / `EXPLICIT_IMPORT`；路径穿越与符号链接拒绝；Ch.14 场景 8 派发前撤权（`403 LEASE_REVOKED_BEFORE_DISPATCH`、零投递）；`CombinedVeto`；`UNKNOWN` 保留一次性 lease；模糊忘记入 `HOLD_PENDING_SCOPE_CLARIFICATION`（零原文秘密回显、+72h/+96h 不超时放行）；6 派生面多跳 `HMAC-SHA256` 墓碑零明文泄漏；`WAITING_RETENTION`；旧代际晚到写拒绝 `409 STALE_GENERATION_REJECTED`。 |
| `tests/test_v02_autobiography_skill_and_budget_drop.py` | 14/14 PASS | P12 `episode -> period_summary -> versioned_narrative_snapshot` 与伴侣隐私剥离；失败弧线保留不回填旧快照；跨月矛盾与 `temporary_choice` 隔离；`importance_to_self` vs `verified_capability` 分离；4 路技能归因；`2x DEGRADED -> 3x SUSPENDED` 忽略瞬时网络故障；`UNVALIDATED_AFTER_MIGRATION`；零写 `project_situation_provenance_zero_write`；F-191 90% 预算骤降生存与跨午夜/时钟回拨 `UNKNOWN` 费用结转。 |
| `tests/test_v02_lived_slices_and_vd_2x2.py` | 9/9 PASS | 残片回收箱 `salvaged_scrap_folder`（零假装喜欢/鸡汤）、工具版本键位与个人脚本精简、双充分性不互相绑架、协作降尺度与修复过渡期、双 2x2 因果实验（Experiment A/B）、3 个可启动 P13 负控变异体。 |
| `tests/test_v02_wave2_end_to_end_closure.py` | 13/13 PASS | 真实 loopback `PhoneHTTPServer` `GET/POST /api/phone/v02/honest-ux`；4 项伴侣动作（含 `actor_side="partner"` 与 `operation_key` 幂等）+ 5 项主体动作 `403 FORBIDDEN_PARTNER_ACTION`；GET 零写与零秘密回显；Forget 屏障、`DIVERGED`、`UNVALIDATED_AFTER_MIGRATION` 与残片匹配接入 `v02_v03_daily_loop_bridge.py` 及 `lived_context_bridge.py`（`LIFE_UPDATE_SURFACES == 37`、`MAX_LIVED_CONTEXT_CHARS == 1800`、`len(_PHONE_AND_WORLD_GUIDANCE) == 1199`、`calm == 0` 字符）。 |

---

## 3. Phase B-1：桌面 Go <-> 真实 Python `PhoneHTTPServer` 跨栈联调（已由 `desk-verifier` 独立复核）

- 文件：`D:\个人\nyairo-desktop\v02_honest_ux_test.go`、`v02_honest_ux.go`、`web\js\v02-honest-ux.js`
- 关键核验：
  - `TestV02HonestUXCrossStackLive` 从 `/root/nyairo-v051` 启动真实 `components.phone.server.create_server` + `PhoneService`（零 `LiveHuxHandler` mock）；
  - 独立关闭端口与未挂载路由 `404` 诚实降级为 `unavailable`；
  - `GET /api/v07/v02-honest-ux` 隐私剥离与实态投影；5 项主体动作 `403 FORBIDDEN_PARTNER_ACTION`；全部 4 项伴侣 `POST` 动作写入真实 Python 状态并在后续 `GET` 持久化；
  - `web/js/v02-honest-ux.js` 通过 `REASON_LABELS` + `formatActionFailure` 消除英文错误码外露，Headless Chrome 点击验收通过（`CHROME_V02_HONEST_UX_AUDIT_OK`）。
- 桌面验证：`go vet ./...` PASS、`node --check web\js\v02-honest-ux.js` PASS、`TestV02HonestUX` 6/6 PASS（`7.012s`）、`go test -count=1 ./...` PASS（`17.737s`）。

---

## 4. Phase B-2：Ch.13 二十四切片总验 + 90 天虚拟长跑与二十反假绿（48 tests PASS）

### 4.1 `tests/test_v02_ch13_twenty_four_slices_capstone.py`（24/24 PASS，5.092s）

`test_slice_01` 至 `test_slice_24` 逐一覆盖 Ch.13 §7 全部 24 个生活切片，直接驱动真实后端 owner（`v02_representation_lease_forget`、`v02_crash_and_partial_reconciler`、`v02_agency_depth_engine`、`v02_common_ground`、`v02_crossmodal_perception`、`v02_recall_projection`、`v02_autobiography_skill_provenance`、`v02_lived_slices_and_vd_protocols`、`v02_runtime_governance_migration`、`v02_v03_daily_loop_bridge`、`lived_context_bridge`、`life_co_presence`）及真实 `PhoneService`（`autonomy_rhythm_fixture.build_stack`）：

1. `#01` 桌面半页草稿跨重启续写（S01 + SQLite CP 恢复 + tray + `v02_honest_ux`）；
2. `#02` 今日不打开旧收藏 `NO_ACTION`（不虚构不喜欢、不偷看、GET 零写）；
3. `#03` 照片成为配色材料（`album_import` + `album_image` + `analyze_image_palette` + `apply_palette_to_work`，不晋升个人记忆）；
4. `#04` 看过但没读懂保持 `UNKNOWN` / `seen_not_understood`（页码不冒充理解）；
5. `#05` 通知到达但未读不进入共享信念（`SemanticWakeCoalescer` + `v02_common_ground`）；
6. `#06` 共同听歌仍写自己东西，不强制全局 pause；
7. `#07` `ATOMIC` 步骤中普通聊天可回应且不破坏原子操作（`FiniteCoPresence`、`owners_written == 0`）；
8. `#08` 工具升级使旧技能转为 `UNVALIDATED_AFTER_MIGRATION`，需 held-out 复验；
9. `#09` 作品失败保留可匹配调色残片，拒绝伪鸡汤 `growth_speech`；
10. `#10` 喜欢地点换安静角落，由负荷与可见机会驱动，无随机反差；
11. `#11` 旧物退休方法同时保留可追溯来历与关联作品可读性；
12. `#12` 主动放下个人愿望，外部约定无交接拦 `409 EXTERNAL_COMMITMENT_REQUIRES_HANDOFF`；
13. `#13` 册子完成归档并关闭结算信封后允许健康 `IDLE`（`QUIET_HEALTHY`）；
14. `#14` 线上购买经经济核实扣款后才入库（假物品拒绝且零扣款）；
15. `#15` 消息超时保持 `UNKNOWN`、有界查询不盲重发（`total_invocations == 1`）；
16. `#16` 局部关系误会修复仅更新对应物件 `obj_kettle`，不改无关物件状态；
17. `#17` 私人笔记单句授权分享（`max_uses=1`、`payload_binding`），原材料零泄漏；
18. `#18` 遗忘安排经按需查档 `consulted_archive=True` 恢复细节，底层承诺仍保留；
19. `#19` 安静无事件日保持 `QUIET_EVENTLESS_DAY`、`llm_calls == 0`、`calm == 0` 字符；
20. `#20` 预算骤降 90% 保留断点、待履约责任与删除屏障，不伪装离线；
21. `#21` 换载体迁移保留作品断点与 `migration_loss_manifest`；
22. `#22` `acceptance` 管理验收事件仅入审计，不进入个人生活记忆与 P12 叙事；
23. `#23` 外部删除照片后源失效与 `execute_explicit_forget` 阻断旧 `vector_index` 派生复活；
24. `#24` 多年后重新复用旧方法经当前环境复验通过，可溯源且无血统等级。

### 4.2 `tests/test_v02_ninety_day_virtual_soak_and_20_antigreens.py`（24/24 PASS，11.152s）

- **90 虚拟天加速长跑（4 tests PASS，显式 `simulated=True` / `evidence_tier="SIMULATED_90D"`）**：
  - `test_soak_01`：跨 90 虚拟天混合唤醒与每 10 天 SQLite `T0..TC` 提交，`wake_tickets` 严格封顶 `<= 128`、`fold_trace <= 16`、状态文件 `< 512 KB`、`PRAGMA integrity_check == 'ok'`、`journal_mode == 'wal'`、`synchronous == 2`；
  - `test_soak_02`：1 虚拟秒内 `300 events/s` 洪峰压测下，控制/前台事件即时旁路（`CONTROL_IMMEDIATE_BYPASS` / `FOREGROUND_GUARANTEED`），背景噪声按小时限流与合并折叠，持久化状态不丢关键票；
  - `test_soak_03`：90% 预算骤降后挂载 `UNKNOWN` 上界费用预留，连续跨越 90 个本地午夜并在第 45 天注入时钟回拨，预留始终结转不被误释放，最终 `SETTLED` 对账释放差额；
  - `test_soak_04`：12 小时静默窗逐小时核验 `llm_calls == 0`、`calm == 0` 字符，且主动外发在静默窗内 `12/12` 拦截（`QUIET_HOURS_ACTIVE`），零未受邀打扰。
- **20 项 Ch.17 Q51 反假绿负控（`test_anti_false_green_01`..`20`，20/20 PASS）**：
  - 逐一覆盖并击穿 Q51 全部分条：DB `SUCCEEDED` 无 S01 回执、仅文件存在、Memory 自述完成、hash 匹配无操作回执、UUID 绕过、仅测拒绝跳过正路径、内存字典代替 SQLite WAL 强杀、两次内存重试代替外部账本幂等、`finally` 代替 `os._exit(137)`、串行代替多线程并发抢占 `max_uses=1` lease、`notfound` 未证伪直接重发、超时改 `FAILED` 清空队列、缺证据伪 `NO_CHANGE`、仅删主表未封多跳与 V-C 恢复、忽略旧代际晚到写、无关键词代替孪生世界非干涉、新模型高分冒充习得、连续日记代替版本化延续 2x2、JSON 可解析冒充语义迁移、虚拟计数器代替显式 `simulated=True` 与三变异体消融。

---

## 5. 不变量与冻结锚点确认

- 全过程 `llm_calls == 0`，未修改任何设计目录文档（`D:\CHIYO-总体设计-v2.0-final`、`v3.0`、`v0.4`）；
- 五份封存基线文件零改动；
- 新增测试与文档均严格使用纯 LF（`\n`）、零 emoji、中性“TA”、`ROOT = Path(__file__).resolve().parents[1]` 与外部 `TemporaryDirectory()`。

---

## 6. Phase C：WSL `/root/nyairo-v051` 同步、实机 `:3831` 与 Windows GUI 发行核验

- **WSL `/root/nyairo-v051` 同步与回归**：
  - 全部新增/修改文件以纯 LF 镜像至 `/root/nyairo-v051`；
  - 五份封存基线文件 SHA256 在 Windows 与 WSL 保持一致且零改动：
    - `components/life/scripts/autonomous_life_assembly.py`: `f14fe6291e1a604520318ebf69acedb583dfc876bfde42f538d6f91017f37434`
    - `components/life/scripts/life_sharing_service.py`: `0becf7df4d9b34881817ff6f1defdcc15929eacbd6183d4c8a38c5a048d46038`
    - `components/native/native_recall/readonly_recall_adapter.py`: `6667ff66fe8105f28f436609f0cd8d744b185c0580e7f1642aafef0b873af78a`
    - `NOW.md`: `d77be1900e94b5e997a389831b864aab5207459bfc5ac763d1b9d90c6c181152`
    - `plugins/chiyo/__init__.py`: `8d6ba0f9295677c9371f0e9be07442096eba67438451ae99fa57312ca622bed7`
  - 联合回归（7 组 Phase A/B `test_v02_*` + `test_v08_storefront_and_manifest_hygiene.py` + `test_release_metadata.py` + `test_bootstrap_installer.py`）：`135 tests PASS`；`test_v05_*` `180/180 PASS`、`worldgen` `166/166 PASS`、`world-editor` `74/74 PASS`。
- **实机 `:3831` 零写探针**：
  - 实机服务 `nyairo_day.py start --root /root/.nyairo-day --port 3831` 已挂载并运行；
  - 对 `/api/phone/v02/{honest-ux,agency-depth,common-ground,crossmodal,runtime-migration,recall-projection}` 等 `v02` 只读路由实测均返回 `200 ok=True`，采用纯只读文件打开、在无活跃写者时使用 SQLite `?mode=ro&immutable=1`，零磁盘写入且不产生瞬态 `-wal`/`-shm` 辅助文件；对历史 `/api/phone/{status,life,today,today-timeline,today-story}` 路由则通过 `LifeSupplyDB(read_only=True)` 优化为无活跃 `-wal`/`-shm` 时走 `immutable=1`，消除读取期间辅助文件抖动（`llm_calls == 0`）。
- **Windows GUI `nyairo.exe` 发行构建**：
  - 三处路径（`D:\个人\nyairo-desktop\nyairo.exe`、`D:\个人\nyairo-desktop\dist\nyairo.exe`、`%LOCALAPPDATA%\Programs\nyairo\nyairo.exe`）字节大小一致（`13,090,816` bytes）、`PE_Subsystem == 2`（Windows GUI），SHA256 一致为 `E07A1CD6C983DD0FBFEDBFF2809E909C5518725C6302011A94B2974287A62FE5`；
  - `go vet ./...` 退出码 `0`，`go test -count=1 ./...` 全绿。

---

## 7. Step 3.2：Ch.03 F-001..F-193、P01–P35 / ADR-012–036 与 Ch.15 二十机制缺口全矩阵审计

- **审计套件**：`tests/test_v02_ch03_f001_f193_matrix_audit.py`（`24/24 PASS`，`llm_calls == 0`，纯 LF，零 emoji，中性“TA”，外部 `TemporaryDirectory()`）。
- **覆盖矩阵**：
  1. **Ch.03 全集 193 条清点与 0.2 范围审计（`03-功能与设计资产清单.md` / `数据清单/功能清单.json`）**：
     - **P0（15 条，4 域）**：`S05` (`F-063..F-065`)、`S10` (`F-113..F-115`)、`S13` (`F-140..F-142`)、`S14` (`F-149..F-154`)；
     - **P1（56 条，8 域）**：`INFRA` (`F-155..F-167`)、`S01` (`F-008..F-014`)、`S03` (`F-033..F-039`)、`S04` (`F-047..F-051`)、`S05` (`F-066..F-071`)、`S08` (`F-093..F-097`)、`S09` (`F-103..F-108`)、`S10` (`F-116..F-122`)；
     - **P2（75 条，11 域）**：`INFRA` (`F-168..F-174`, `F-187`, `F-191`, `F-193`)、`S01` (`F-001..F-007`)、`S02` (`F-023..F-028`)、`S03` (`F-040..F-043`)、`S04` (`F-052..F-062`, `F-186`)、`S05` (`F-072..F-077`)、`S06` (`F-078..F-083`, `F-188`)、`S07` (`F-088..F-092`)、`S08` (`F-098..F-100`)、`S09` (`F-109..F-112`)、`S11` (`F-123..F-133`)；
     - **P3/P4/P5 边界清点（47 条：`21 P3 + 19 P4 + 7 P5`）**：已落地的 `S12` 技能/迁移与 `S13` 迁移套件在 `v02_autobiography_skill_provenance` 与 `v02_runtime_governance_migration` 核验；`RESEARCH_RESERVE` 与 `PROPOSED_NOT_ACCEPTED` 条目保持诚实边界，不虚构未授权运行时宣称。
  2. **Ch.17 §7 `P01–P35` 工程强化契约 & `数据清单/待裁定与提案登记.json` (`ADR-012..ADR-036`)**：逐群实测分域拒伪、类型化绑定、UTF-8 编码、上下文重检、双 `2x2` 因果协议（`Experiment A / B`）、`P13` 三变异体负控、`NOT_EXECUTED` 双证明门禁、`HMAC-SHA256` 墓碑、2x `DEGRADED` -> 3x `SUSPENDED`、三层计量与跨午夜/时钟回拨 `UNKNOWN` 费用结转、以及 `manifest/release` 一致性。
  3. **Ch.15 §10 二十项机制缺口（Gap 1..20）**：逐项绑定真实 `v02_*` 模块（`v02_representation_lease_forget`、`v02_crash_and_partial_reconciler`、`v02_agency_depth_engine`、`v02_common_ground`、`v02_crossmodal_perception`、`v02_recall_projection`、`v02_autobiography_skill_provenance`、`v02_lived_slices_and_vd_protocols`、`v02_runtime_governance_migration`、`v02_v03_daily_loop_bridge`）与 `PhoneService` 读口/写口，并复核 `v02_honest_ux_view.read_v02_honest_ux_view` 在空根与有态根下 `100%` 零写。
  4. **清单一致性**：`MANIFEST.json` 仅增量更新 `tests/test_v02_ch03_f001_f193_matrix_audit.py` 与 `docs/engineering/v02-full-closure-verification-20261118.md` 两项 POSIX 条目及 `file_count`（`13824`）、`tree_sha256`，Windows 与 WSL `/root/nyairo-v051` 字节级一致，`verify_manifest.py` `changed_or_missing == []`。

---

## 8. 17:30–18:00 DeepSeek 一对一补链与全 22 组 `test_v02_*` 全绿收口（rev 1101+）

- **状态归并消除脑裂（`components/phone/v02_honest_ux_view.py`）**：将 `v08_open_affordance` 桌面/房间物件（`matter_space.objects`）、`v02_runtime_governance_migration.project_chatless_life_review`、`v02_recall_projection.project_p12_autobiographical_narrative` 叙事章节、旧版 `LOW_BUDGET`/`DORMANT` 运行档位与 `v02_common_ground` 轻量共在协议全部以 100% 零写方式并入 `GET /api/phone/v02/honest-ux`，并保持 `Forget` 屏障与伴侣隐私过滤。
- **TA 脑手全量接通（`chiyo_bundle/phone_tool.py`、`v02_agency_depth_view.py`、`v02_runtime_migration_view.py`、`server.py`）**：在保持 `len(LIFE_UPDATE_SURFACES) == 37` 与 `len(_PHONE_AND_WORLD_GUIDANCE) == 1199` 不变的前提下，把 `v02_honest_ux` 接入 `LIFE_READ_SURFACES`（带本地主体 capability），并把 5 项主体独占动作（`salvage_work_scrap`、`simplify_personal_tool`、`record_period_snapshot` 挂入 `v02_agency_depth`；`explicit_import_representation`、`approve_pending_forget_hold` 挂入 `v02_runtime_migration`，并在 `_post_v02_runtime_migration` 放行 `artifact_id`）全链路打通且在伴侣门严守 `403 FORBIDDEN_ACTOR_SIDE` 与嵌套非法键 `400 INVALID_FIELDS`。
- **全仓 0.2 锚点与全 22 组套件统一收绿**：将 `v02_v03_daily_loop_bridge.py` 及 4 份历史 `test_v02_*` 套件中过时的 `5f51c9...` 对齐为当前封存基线哈希 `f14fe629...`（5 份封存基线文件字节一字未改），新增 `tests/test_v02_unified_state_and_brain_wiring.py`（8/8 PASS，含 `phone_tool.phone_handler` 经真实回环 `PhoneHTTPServer` 跑通全部 5 项主体动作）。
- **WSL 全 23 组 `test_v02_*.py` 与实机 `:3831` 最终复核**：在 `/root/nyairo-v051` 运行全部 23 组 `test_v02_*.py` 与 `test_v08_storefront_and_manifest_hygiene.py`，更新纯 LF `MANIFEST.json`（清单内条目两端 `verify_manifest.py` `changed_or_missing == []`，工作区未入清单的本地工具目录 `.agents/` 与构建产物目录 `dist/` 按清单外工作目录保留）并重启 `:3831` 验证 `/root/.nyairo-day` 的 `GET /api/phone/v02/honest-ux` 真实返回屋里物件且零磁盘写入。

---

## 9. 独立对抗审计复核与全项修复闭环

针对独立对抗审计发现的六项主要缺口、两项计数侧信道与四项边界/交互瑕疵，已完成跨后端与桌面端全量修补并纳入回归套件 [`tests/test_v02_gpt_audit_remediation.py`] 实测闭合：

1. **伴侣视角四层隐私与计数侧信道收敛**：`v08_universal_matter.py` 与 `v02_honest_ux_view.py` 沿容器/区域链向上递归排查私人祖先并剥离私密子件计数；`v02_honest_ux_view.py` 对无物件对应的历史痕迹不再默认公开；`v02_recall_projection.py`（含 `stages/P12` 工作台与情景回忆）及 `PhoneService.v02_recall_projection` 严格校验 `has_subject_capability`；伴侣读口脱敏 `crash_reconciler_summary` 与未公开域内部计数。
2. **统一遗忘屏障与暂缓范围隔离**：`v02_representation_lease_forget.load_unified_forget_barrier_readonly` 统一按原始 `bytes.fromhex` HMAC 摘要、`opaque_source_token` 与 `v02_vc_authority_watermark.json` 校验删除屏障并接入 `v02_honest_ux_view`、`v02_v03_daily_loop_bridge`、`lived_context_bridge` 与 `v02_recall_projection`；`resolve_hold_case` 与 `honest-ux` 对 `clarify_forget_scope` 严格核对 `actor_side`（非共享伴侣记录拦 `403 FORBIDDEN_FORGET_CASE_SCOPE`）并按所选范围真实收窄并释放范围外 source 的 `hold_active`。
3. **休眠/省电与轻共在单一账本对齐**：`DORMANT` 固定压低可用预算至 `0%` 且关闭探索性认知（ASP 10% 压降同步反映到运行态视图而不覆盖 `DORMANT`）；`v02_common_ground` 轻共在安排与 `confirm_rescaled_copractice` 双向同步，前后端及 Go 网关统一规范 `paused_no_debt` -> `pause_without_debt`。
4. **权威回执核验、TOCTOU 守卫与两阶段幂等重放**：`settle_v02_v03_round_consequences` 跨 `canonical_life.sqlite3` / `external_provider_ledger.json` 核验真实 `SUCCEEDED` 执行回执；`explicit_import_representation` 校验 `expected_observed_sha256` 防复核后篡改；`honest-ux` 写路径采用 `pending_ops` + `state_tx_guard` 512 条有界淘汰环兜底中断重提。
5. **去抽签化 2x2 实验、`CP05` 强杀时机与桌面错误/刷新保真**：`Experiment A/B` 彻底移除 `p_ok` 概率抽签，改为真实物件可供性注册表与磁盘多分支 `ManagedArtifact` 确定性执行（`Experiment A` 同步补齐负控）；`CP05` 仅在 `SIGKILL` 恢复时才消费 outbox 进收件箱；桌面端改用 `fetch` 保留后端真实中文错误原因、读取失败不再伪报核实状态、范围澄清刷新后显示处理后状态，`Headless Chrome` 与跨栈 Go 联调全量重验通过。
6. **最终复核与发行同步证据**：`23` 组 `tests/test_v02_*.py` 全量回归 `385/385 PASS`（含 `tests/test_v02_gpt_audit_remediation.py` `7/7 PASS`）与配给服务 `components/supply/lifesupply/tests` `142/142 PASS`；`MANIFEST.json` `13,826` 项在 Windows 与 WSL `verify_manifest.py` 下均为 `changed_or_missing == []`，且两端 `config/public-scan-exceptions.json` 字节一致；桌面端 `go test -count=1 ./...` 连续两次全量 `PASS`（`14.974s` / `15.169s`），三处 `nyairo.exe` SHA256 一致为 `E07A1CD6C983DD0FBFEDBFF2809E909C5518725C6302011A94B2974287A62FE5`。
7. **第二轮自检对抗审计补全闭环（5 项隐蔽缺口同轮收口）**：
   - **实机历史 5+ 读口与配给子服务真·零目录抖动**：`lifesupply/db.py` 新增 `readonly_sqlite_uri` 与 `LifeSupplyDB.read_only_scope()`，`LifeSupplyService.dispatch()` 对全部 `READ_OPS` 强制以 `?mode=ro&immutable=1`（无活跃 `-wal/-shm` 时）绑定只读连接（同步接入 `artifact/store.py`、`governance/authority.py`、`workspace/store.py` 与 `components/phone/life.py`），在 `:3831` 重启后两轮静默窗内逐路由递归监控 `/root/.nyairo-day`（含 `supply/data`）确认 19 条 GET 路由（`state/living/today/today/timeline/today/story/desk/presence` 与 `v02/*` 等）零目录 `mtime_ns` 变化与零 `-wal/-shm` 辅文件生成；
   - **伴侣 `POST /api/phone/v02/recall-projection` 防越权收紧**：`service.py` 伴侣侧拦阻 `tombstone_memory`（`403`）并透传 `has_subject_capability` 至 `v02_recall_projection.py`，禁止伴侣覆写私密经历（`403 FORBIDDEN_PRIVATE_MEMORY`）或写入 `visibility != shared` 记录；
   - **V-C `"vc:"` 水印前缀对齐**：`v02_representation_lease_forget._candidate_hits_barrier` 同时比对 `nyairo-vc-seal-v1:` 与 `vc:` 两种摘要域分隔符，彻底阻断旧备份恢复后被 `vc:` 封存记忆复活；
   - **动作回执与桥接前缀防伪加固**：`v02_v03_daily_loop_bridge._verify_authoritative_owner_receipt_zero_write` 在 `canonical_life.sqlite3`（含空行库）或 `external_provider_ledger.json` 存在时强制进入所有权核验，识别顶级 `versions.source.creator_receipt_ref`（`crcpt:`）与 `ExternalProviderLedger.invocations[].provider_receipt_id`（`prov_rcpt_`），且不再轻信未匹配本地候选集合的伪造 `v02v03:` 前缀；
   - **随身手机后台并发读取覆写竞态修复**：`web/js/pocket-phone.js` 与 `web/js/v02-honest-ux.js` 新增请求序号与动作纪元门禁（`fetchReqSeq`、`huxActionEpoch`、`stateReqSeq`、`getCachedState()`），防止 `fetchBookData("memories")` 在慢接口返回后用旧快照回盖用户刚提交成功的范围澄清状态。

---

## 10. Round-2 深度契约收口与全栈对抗终验（2026-11-18 晚间–11-19 零时）

本轮针对代码/测试深度排查揭示的真契约缺口与边界误判完成二次系统补链（详见 [`tests/test_v02_gpt_audit_remediation.py`](file:///D:/nyairo-rc1-body-backend/tests/test_v02_gpt_audit_remediation.py) `test_08`）：

1. **后端治理账本故障闭环与两阶段崩溃重提**：
   - `components/life/scripts/v02_representation_lease_forget.py` 新增 `_read_json_with_health` 与 `_sanitize_dict_list_fields`：遇到损坏 JSON、符号链接或字段类型篡改时写路径一律 `503 CORRUPT_GOVERNANCE_AUTHORITY_BLOCKED` 故障闭环（绝不覆盖损坏磁盘文件），读投影将受损域同步标为 `epistemic_status="UNKNOWN"` + `provenance.read_degraded=True`；
   - `explicit_import_representation` 强制校验绑定 `workspace_root` 一致性（`403 WORKSPACE_ROOT_MISMATCH`）、<= 2 MiB（`413`）与合法 UTF-8（`422 INVALID_UTF8_MARKDOWN`），并先执行 `_check_idempotent_op` 再核对崩溃重试匹配，搭配 `MAX_OPERATION_LEDGER=512` 与 `committed_op_ring` 兜底 `540`-op 淘汰后幂等重放（`200`）与篡改拦截（`409 OPERATION_ID_PAYLOAD_CONFLICT`）；
   - `components/phone/v02_honest_ux_view.py` 对伴侣遗忘暂缓加严 `require_candidates=True`（空共享会话返 `409 FORGET_NO_ELIGIBLE_CANDIDATES`），`resolve_hold_case` 追踪重叠暂缓并精准清理范围外 `hold_active`；`confirm_rescaled_copractice` 对 `pause_without_debt` 写入 `0.0` 频次；`chiyo_bundle/phone_tool.py` 对高敏运行时迁移动作与外层/内层 `operation_key` 冲突加门禁（守住 `LIFE_UPDATE_SURFACES == 37`、`_PHONE_AND_WORLD_GUIDANCE == 1199`、`MAX_LIVED_CONTEXT_CHARS == 1800`）。
2. **启动期 WAL 自动对账、工作台真提交与迁移语义降级**：
   - `components/life/scripts/v02_crash_and_partial_reconciler.py` 删除 `tx_t1_commit_adoption_activity_action` 重复定义阴影；`life_runtime_production.py` 与 `autonomous_life_resident.py` 接入 `reconcile_canonical_wal_on_startup()`，仅在同 `state_root` 已存在 `canonical_life.sqlite3` 时恢复未完结出箱/动作（空根绝不建库）；
   - `v02_v03_daily_loop_bridge.py` `_settle_v02_advance_workbench` 在工作台绑定活跃 `ManagedArtifact` 时真实提交新版本并更新托盘 `baseline_version_id`；`_verify_authoritative_owner_receipt_zero_write` 按 `(artifact_id, version_id, action_id, owner)` 严格交叉核验；
   - `v02_runtime_governance_migration.py` `_apply_import_suitcase` 将旧语义投递记录显式降级为 `downgraded_status="UNKNOWN"` / `treated_as_read=False` 并入 `migration_suitcase.migrated_delivery_records` 持久化。
3. **Experiment B 240-run 真实工作区重制与 90 天诚实壁钟门禁**：
   - `v02_lived_slices_and_vd_protocols.py` 升级 `Experiment B` 为默认 `20` 任务 × `3` 种子 × `4` 格 = `240` 独立隔离 `ManagedArtifact` 分支工作区（`60` runs/格），每次成功续写均经 `_rlf.commit_artifact_version` 落盘并由独立只读核验器 `_evaluate_experiment_b_cell_from_owner_state` 验收：`R0N0=0.25`、`R0N1=0.25`、`R1N0=1.0`、`R1N1=1.0`、`delta=0.75`、`cluster_ci_95=[0.55, 0.95]`、`owner_committed_runs=150`、`safety_violations=0`、`llm_calls=0`；
   - 新增 `verify_wall_clock_soak_gate(..., min_wall_hours=24.0)`：明确拒绝 `simulated=True` / `SIMULATED_90D` 冒充连续 `>=24h` 真实挂钟运行证明（返回 `SIMULATED_EVIDENCE_CANNOT_SATISFY_WALL_CLOCK_GATE`）；
   - 同步补强 `test_v02_wave2_end_to_end_closure.py`（`test_06`）、`test_v02_ch13_twenty_four_slices_capstone.py`（`slice_01`）、`test_v02_twelve_mandatory_scenarios_and_vd_ablation.py`（场景 1/3/10 真实 owner 验证）与 `test_v02_ch03_f001_f193_matrix_audit.py`（直接解析 `03-功能与设计资产清单.md` 全部 193 条对照表及 `IN_SCOPE_BEHAVIORAL_TEST` `146` + `STAGE_RESERVE_CONTRACT_ONLY` `47` 处置表，补全 Gap 15..20 行为断言）。
4. **桌面网关深层脱敏与 Headless Chrome 实测捕获真错位修复（`D:\个人\nyairo-desktop`）**：
   - 修复 `v02HonestUXWriteResult` 将上游非 JSON `403`/`409` POST 响应误吞成 `200 UPSTREAM_READ_FAILED`（「状态读取未完成」）的真实网关缺陷（现透传 `HTTP_403` / `HTTP_409` 并于 DOM `#toastPill` 渲染对应中文原因）；收敛 `scrubV02HonestUXValue` / `scrubPartnerSubjectPrivacyDeepDepth` 递归深度 >64 故障闭环；补强 `web/js/v02-honest-ux.js`、`web/js/room-stage.js` 顶栏轮询刷新与 `web/index.html` `jpost` 错误结构透传。
5. **Round-2 最终全量核验事实（以本轮改动后最后一次实测结果为准）**：
   - **后端 WSL `/root/nyairo-v051`**：
     - 全量 `23` 组 `tests/test_v02_*.py`：`386/386 PASS`（`47.003s`，其中 `tests/test_v02_gpt_audit_remediation.py` `8/8 PASS`）；
     - `components/supply/lifesupply/tests`：`142/142 PASS`（`48.837s`）；
     - `test_v08_ch46_seven_day_verification.py`、`test_v08_v02_v03_design_debt_closure.py`、`test_v03_full_live_protocol_soak.py`、`test_phone_tool.py`：`52/52 PASS`（`16.411s`）；另 `test_hermes_phone_brain_wiring.py` 76 项中功能/契约全过，仅 `test_01_frozen_life_anchors_unchanged` 保留历史旧锚点 `5f51c9...`（本轮五份封存基线保持现行封存哈希 `f14fe629...` 零改动）；
   - **桌面端 `D:\个人\nyairo-desktop`**：`node --check`（3 份 JS 全过）、`go vet ./...` PASS、`go test -count=1 ./...` 连续两次全量 PASS（`16.443s` / `16.064s`，含 Chrome 实 click 与跨栈活连接）；三处 GUI `nyairo.exe`（`D:\个人\nyairo-desktop\nyairo.exe`、`dist\nyairo.exe`、`%LOCALAPPDATA%\Programs\nyairo\nyairo.exe`）一致为 `17,785,344` 字节、`PE_Subsystem == 2`、SHA256 `2bc53b740bda1bb96f7c95775baf4b79a0aaf1f62f10ce79eb3228f4374e3e64`；
   - **WSL `:3831` 实机重启复核（`/root/.nyairo-day`，`PID 25426`）**：重启后连续两轮扫过全部 `19` 路 GET 入口均返回 `200 OK`，`GET /api/phone/v02/honest-ux` 实返 `chatless_traces.objects == 21`、`epistemic_status == "VERIFIED"`、`perspective_redacted == True`、`llm_calls == 0`，且两轮读取前后 `supply/data`、`memory`、`phone` 目录 `mtime_ns` 与整棵目录 `-wal`/`-shm`/`-journal` 辅文件列表均为零变化；
   - **诚实清单边界**：`MANIFEST.json` 清单内条目经 `verify_manifest.py` 校验在 Windows 与 WSL 均满足 `changed_or_missing == []`，但本地开发工作区保留了清单外 `.agents/` 与 `dist/` 目录，因此严格全目录 `unexpected_files` 非空属于本地开发态工作目录现状，未虚构“无任何清单外文件”。
