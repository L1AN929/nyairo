# 0.7 本地发布准备（2026-11-15 · 收尾代理 R1 施工单执行记录）

- 本文件是发布就绪记录，不是发布完成声明：commit/tag/打包/上传/服务重启均未做（见"发布步骤"，全部留给父级/协调者）。
- 现场：HEAD `8dbe8ef`（"chore(release): 生成 v0.6.0 归档树匹配清单"，2026-11-08）、分支 `work/rc1-phone-world-body`、git status 展开 178 个已跟踪修改 + 186 个未跟踪文件（未提交，本单严禁 commit）。仓库：Windows `D:/nyairo-rc1-body-backend` ＝ WSL `/root/nyairo-v051`，本单全部产物双侧同步、逐文件 md5 零差异（见终验小节）。
- 版本口径：**v0.7.0 收敛版**，含 2026-11-14/15 全部修复与新机制；**v0.8.0 版本号保留未用**（会话中途按补丁单曾标 0.8.0，2026-11-15 用户裁定改道 v0.7.0，各处随之回落）。四处对齐完成：`MANIFEST.json` → `0.7.0`、`scripts/release.json` → `v0.7.0`；`scripts/bootstrap.sh:6/:23` 与 `website/install.sh:6/:23` 的安装器版本串（下载发行版号 + 帮助文本）已升到 `v0.7.0`（各文件内 `uv >= 0.6` 是 uv 工具最低版本提示，非产品版本串，不动；v0.5.1 先例是四处对齐，见 release-readiness-20261101.md 第 12 行）。
- 本单改动文件仅限：`MANIFEST.json`、`scripts/release.json`、本文件、`docs/engineering/20261114-design-debt-registration.md`（追加 §五 发布登记）。**未改任何代码文件；未动 NOW.md**（理由见"NOW.md 处置"）。

## 一、变更清单（2026-11-14 四批次 + X 波总账，登记文件 §一～§四 为权威细节）

- **T1（正式决策请求接attention）**：attention_view 接进正式决策请求（30 章 A 批）；`TRIGGER_EXPLICIT_REEVALUATION` 诚实改名；处境指纹纳入身体档；世界侧业务日界统一 +08:00（新模块 `components/world/scripts/business_day_key.py`；骨架世界 `world_living_skeleton.py` 有意保留 Asia/Tokyo，登记文件 §二"暂缓裁定"表）；已购抑制同款候选（ALREADY_OWNED_SUPPRESSED）；in-flight PAUSE 拒绝与 small_treat 超额拒付负控。
- **T2（反骚扰配额对齐 10 章设计值）**：周 3／慢信月 1／48h 最小间隔／连发两周第三周归零／存在感表演话术拦截（登记文件 §二"已对齐"表逐行有值与 reason_code）；慢信 72h 冷静期（`drafted_at`）；摩擦淡去默认 48h；话题悬挂默认 20min。
- **T3-wave1（慢变量孵化内核）**：六相/因果边/显形队列/冷却族 120d·90d/熵预算月 2/平淡期 21 天/感知分层，sha256 确定性零 RNG，v04 POI 一次性物理状态；S07 最小 Affect owner（`components/life/scripts/s07_affect_owner.py`，登记文件 §一.1）。
- **T3-wave2（WakeTicket / 亲密接线 / T1 尾巴）**：WakeTicket 持久票（ticket_id/coalesce_key/folded/est_tokens/lease + 对账投影，合并窗仍 300s 未动）；LifeRequestBudget 日 token 预算维度；成年亲密接共在 owner（PRIVATE_INTIMATE 模式/活动断点/86400s 跨日重确认）；`ROUTE_PROBE_FRESHNESS_SEC` 既成缺陷修复（.pyc 取证复原，登记文件 §四.5）；T1 尾巴清理（别名删除、body 进指纹哈希）。
- **X1（预算生产接线）**：est→预算生产接线 + 50k 日 token 预算启用（旋钮 `CHIYO_AUTONOMOUS_DAILY_TOKEN_BUDGET`）；`v03_ecology_view` 旋钮白名单两键放行；冻结锚点重钉 18 处（`autonomous_life_assembly.py` 76b4c7cb… → 5f51c9e8…，登记文件 §四.6 有完整清单）。
- **X2（因果边动力学）**：rule 带/witnessed 脉冲/inferred 零动力学；多 POI binds 分流；POI 营业感知 hold；接触精化波动收窄。
- **设计文档**：v3.0 原稿 W1–W10 回写（备份 `D:/CHIYO-总体设计-v3.0-回写备份-20261114/`，不在本仓库内）；登记文件 `docs/engineering/20261114-design-debt-registration.md` 落地。

## 二、MANIFEST.json 刷新

- **算法来源（权威）**：`scripts/make_manifest.py`（生成）＋ `scripts/verify_manifest.py`（校验）＋ MANIFEST 顶层 `tree_recipe` 自述。要点：`files` = 逐文件 sha256、POSIX 相对路径、排序、排除 `MANIFEST.json` 与 `.git` 自身；`file_count` = 条目数；`tree_sha256` = 对排序后的 `"<sha256>  <路径>\n"` 行拼接再取 sha256；其余顶层键（schema/hermes/project_name/release_gate/…）从既有清单承接。排除目录：`.git`、任何 `__pycache__`、`vendor/hermes/.venv|venv`、`vendor/hermes/*.egg-info`。
- **生成方法**：工作树 ≠ 可直接跑 `make_manifest.py` 的树（盘上还有 8651 个永不入包的未跟踪/忽略文件，裸跑会把 worker-runs、dist、.memsearch 全吸进清单）。本单按仓库既有语义（清单描述"将提交的归档树"）在 WSL 侧组装 staging 目录 `/tmp/nyairo-stage-v070`：文件集合 = 旧清单 13458 条 ∪ 本次纳入的 172 个未跟踪源文件（171 个变更引入 + 本就绪文档自身；见小节"纳入/排除"），逐文件复制后用权威脚本 `--version 0.7.0 --revision V07_0_REDACTED_SOURCE_TARGETED_CHECKS --status SOURCE_REVIEWED_TARGETED_TESTS_PASSED --release-gate "…" --write` 重生成，再对该 staging 目录跑 `verify_manifest.py` 得 `valid=true`（零 unexpected_files——这是工作树永远给不出的绿，只有归档语义的 stage 能给）。
- **行尾核验**：.gitattributes 为 `* text=auto eol=lf`；对全部 355 个变更文件扫描 CRLF：0 个 CRLF 文本文件（1 个 .md 只有孤立 CR 字符，git 不归一化；21 个是二进制 png/webp/mp4/exe，`text=auto` 不触碰二进制）。故"盘上字节＝提交字节"，本清单哈希与未来 commit 归档字节一致。
- **数字**：刷新前 version 0.6.0 / file_count 13458 / tree_sha256 `a99f92be0416a0a482edb453da0e5a1fe9a16924641dae3f95a75d8fa3fdce16`；刷新后 version 0.7.0 / file_count 13630 / tree_sha256 见 MANIFEST.json（写入后以文件为准，本单不在此复制以免两处失同步）。
- **顺带修正的两类既成账**（非本单引入，刷新后自动闭合）：
  1. v0.6.0 清单里 11 条哈希与 HEAD 自身字节不符（NOW.md、tests/test_room_living_ui.mjs、9 篇 .agents/notes —— 疑为清单生成后、提交前文件又被父级改写所致）。这 11 个文件两侧字节本就一致（md5 实核），刷新即修复。
  2. Windows/WSL 双侧 6 个 website 文件预存漂移（app.js/index.html/style.css/docs-data.js/USER_GUIDE.zh-CN.md/assets/og-cover.png；Windows 侧 11-13 22:4x 更新、WSL 侧停在 11-13 20:3x）。本单按 Windows（较新）→ WSL 同步，同步后 6 文件 md5 双侧一致。
  3. git status 的 178 个"M"里有 53 个是纯 mode 位噪音（内容哈希与旧清单相同），不影响清单。
- **verify_manifest.py 对工作树的预期**：manifest 校验的正式跑法只在打包 staging（`make_release.sh` 内建）里做；直接对工作树跑仍会报 ~8000+ unexpected_files（worker-runs/.memsearch/dist 等盘上存在但永不入包的文件），这是语义内行为，不是清单缺陷。

### 纳入/排除判定（186 个未跟踪文件 → 171 纳入 / 15 排除）

排除 15（全部有先例或引用证据）：

| 文件 | 理由 |
|---|---|
| `docs/engineering/20261101-release-offline-final-guard-result.md` / `.json` | .gitattributes :101-102 明文 export-ignore（"误落 guard 结果"） |
| `tests/fixtures/frozen_clock.py`、`tests/fixtures/frozen_site/sitecustomize.py`、`tests/fixtures/direct_world_bridge/journey_submit_port.py` | .gitattributes :103-105 明文 export-ignore（2026-11-01 具名否决的旁路/全局时钟夹具） |
| `.agents/release-id.txt` | .gitattributes :93 export-ignore |
| `.agents/progress/current.json` | worker 连续性快照（同 worker-runs/continuity 类，20261101 先例不入包） |
| `20261106-combined-a-prepare.sh`、`20261106-combined-a-run.sh` | 根目录一次性手工脚本（内嵌 `/tmp/rc104-venv` 本机路径），全库 grep 零引用，20261101 先例排除同类 |
| `scripts/_probe_stage9.py`、`scripts/_probe_stage9_chain.py` | 一次性探针，全库零引用（20261101 先例排除"事故探针"） |
| `website/downloads/nyairo-0.5.1-setup.exe`(+.sha256)、`nyairo-0.6.0-setup.exe`(+.sha256) | 二进制发行物不入源码清单（先例：manifest 0 个 exe） |

纳入 171：.agents/notes 46 篇、chiyo_bundle 2、components 43（含 `business_day_key.py`、`s07_affect_owner.py`、`v04_neighborhood_living.py`、`v03_clock_evaporation.py` 等）、docs/engineering 23、scripts 14（各 slice fixture 与 v07 live/demo 入口）、tests 60、website 资产 12（`friend-l1an9.webp`、8 张新截图 webp、2 段 demo mp4——`index.html`/`app.js` 实引用 mp4；.gitattributes 对上述全部 `export-ignore: unset`、`git check-attr` 实核）。

## 三、scripts/release.json

- 结构未动（schema 1 / hermes_version / hermes_commit / profile_format / update_protocol 全保留），仅 `version: "v0.6.0"` → `"v0.7.0"`（会话中途曾按补丁单标 `v0.8.0`，2026-11-15 用户裁定改道后回落 `v0.7.0`）。
- 字段含义依据：`verify_manifest.py` 以 `release.json.version == 'v'+MANIFEST.version` 做一致性门（tests/test_release_metadata.py 钉住）；`test_update.py` 以 release.json 为安装注册/升级准入元数据。双侧同步改。

## 四、测试证据（2026-11-15 实测，WSL `/root/nyairo-v051`，`vendor/hermes/.venv` python）

| 套件 | 实测 | 结果 |
|---|---|---|
| `tests.test_v07_release_complete_flows`（主套件） | Ran 10 | OK |
| `tests.test_v07_daily_life` | Ran 74（215.8s） | OK |
| `tests.test_v04_neighborhood_living`（锚点） | Ran 28 | OK |
| `tests.test_v03_clock_evaporation_stance_world`（锚点） | Ran 25 | OK |
| `tests.test_v02_v03_causal_counterfactual_soak`（锚点） | Ran 22 | OK |
| 昨日批次 10 个新测试模块合计（business_day_key 10 / attention_request_integration 5 / situation_body_parts 6 / v03_economy_illness_reality_bridge 34 / v03_slow_variable_incubation 12 / s07_affect_owner 10 / autonomous_life_budget_wiring 11 / v03_ecology_view_knob_whitelist 9 / v03_causal_edge_dynamics 8 / v02_wake_tickets_budget 13） | Ran 118 | OK |
| 成年亲密三件（view 42 / fault_matrix 43 / co_presence_wiring 6）+ test_release_metadata + test_update + test_privacy_security + test_security_hardening_v052 | Ran 123 | OK |
| staging 树 `verify_manifest.py`（打包语义） | 13630 文件零差异零多余 | valid=true |

- 数字对不上总账口径的两处，如实记录：
  1. 总账称"合并锚点套件 132/132"；本单对施工单指定的三个锚点套件实测 28+25+22=**75**，未能在仓库内复原出 132 的组合口径（.agents/worker-runs/ 无 20261114 记录文件）。逐套件实测数字如上，以实测为准。
  2. 总账称"新增测试 92 个（分批 32+5+22+19+20+8，合计 106，口径本身不一致）"；本单对 12 个昨日批次新测试文件 grep `def test_` 实测合计 **167**（上表 118 + 亲密 fault_matrix 43 + co_presence_wiring 6）。批次↔文件的精确归属无法从仓库单方复原，本表逐文件数字可复核。
- 终验（本单最后一步）复跑以上全部套件 + `tests.test_release_metadata`，数字见文末"终验"小节。

## 五、已知遗留

- **登记文件**：`docs/engineering/20261114-design-debt-registration.md` §二"暂缓裁定"表 6 项（急性期进入/退出阈值、合并冷却窗 300s、quiet hours 23:30、骨架世界 Tokyo 日界、SlotQuota 四时段槽位未实现）随 v0.7.0 带入；§五为本单追加的发布登记。**登记不等于追认。**
- ~~**安装器版本串两处未升**（不在本单权限）：`scripts/bootstrap.sh:6`、`website/install.sh:6` 仍 `v0.6.0`~~ → **已于 2026-11-15 收尾补丁单关闭**：按协调者明示指令两文件四处版本串升起，并随用户裁定改道定版为 `v0.7.0`；随补丁重建 MANIFEST 并复验（先例见 release-readiness-20261101.md）。
- `make_release.sh` 的正式打包必须从**提交后的 commit** 归档——本单未 commit，ZIP 尚不存在；清单已按"将提交字节"预生成，父级提交时 `text=auto eol=lf` 不会改写任何纳入文件（0 个 CRLF 文本文件，见 §二）。
- 敏感审阅（有限扫描，非零泄露保证）：对 171 个纳入未跟踪文件做内部地址/凭证模式正则普查，29 处命中全部为合成测试域与标识符（`*.invalid`、`*.local`、`sk-presence`、测试用 `Bearer` 常量），无真实内网主机/凭证；未穷尽人工审阅正文，与 20261101 先例同口径。

## 六、NOW.md 处置（不动，理由存档）

- NOW.md 文件头（2026-11-04 加）明确声明："**本文件的记录停在 2026-11-03 22:04 / rev884（fastday-n）**……2026-11-04 全天不在本文件里——见 `.memsearch/PROJECT.md` 的 Recent Progress；机器可读断点存 `.agents/continuity/current.json`。只读本文件不等于读到最新状态。"
- 即该文件的现行惯例是**冻结的历史日志**，持续记录已迁移他处；v0.6.0 发版（11-08）也未在 NOW.md 追加条目；release-readiness-20261101.md 且注明 "NOW.md 由父级独占"。
- 结论：向一个自我声明"停在 11-03"的文件追加 2026-11-15 条目违背其自身声明，**本单不动 NOW.md**。本次发布记录载体 = 本文件 + 登记文件 §五。

## 七、回滚路径

- **既有 tar**：`/root/nyairo-v051-code-backup-20261112.tar.gz`（10,959,186 bytes，2026-11-12 21:05；结构实核：前缀 `nyairo-v051/`、1580 文件 + 115 目录，含 vendor 之外的全部代码/文档/notes，排除 vendor、__pycache__、.venv、.memsearch、.claude、dist、data、worker-runs/continuity/progress/release-id、website/downloads、根目录日志与锁文件）。本单未改动它。
- **新打 tar**：`/root/nyairo-v051-code-backup-20261115.tar.gz` —— 按 1212 同配方在收尾时打的发布前快照（含本单全部产物；大小与条目数记入收尾报告，不写入本文件，避免"文档进 tar、tar 大小进文档"的自引用循环）。
- git 底线：Windows 侧 HEAD 始终 `8dbe8ef`，本单零 commit；任何工作树层面的不满意都可 `git checkout -- <path>` 恢复到 v0.6.0 提交态（本单新文件为 untracked，删除即退；但"严禁删除文件"对本单成立，实际回滚由父级决定）。
- 旧 MANIFEST 回滚：1212 tar 内的 MANIFEST.json 是 v0.6.0/13458 版；它只在与"回滚到 8dbe8ef 代码树"配套时使用。

## 八、发布步骤（本单一步未执行，全部留给父级/协调者）

1. （建议先做）确认 `/root/nyairo-v051-code-backup-20261115.tar.gz` 在位（本单已打好）。
2. 父级按白名单 `git add` + commit（含 MANIFEST.json、release.json、本文件、登记文件与 171 个纳入文件；15 个排除文件不 add）。commit 后**必须**在新 commit 上重跑 `make_manifest.py`+`verify_manifest.py` 复核清单仍与归档树一致（历史上父级 commit 晚于清单生成曾造成 11 条陈旧哈希，见 §二）。
3. 打包验证：`bash scripts/make_release.sh <新commit>`（会在私有 staging 里跑打包内 `verify_manifest.py` 门，门不过不落盘）；产出 `dist/nyairo-v0.7.0.zip` + `.sha256`。
4. tag/push/upload 按 2026-11-02 固定流程：先提交 → LF 树重生成 MANIFEST → 用该 commit 打包 → 验包 → 打 tag → push → 建 release/上传；旧 tag/release/main 一律不动。
5. 服务重启（协调者终局统一执行，本单未做）：当前现场 3831 **无监听**（WSL `ss` 与 Windows `netstat` 双侧实核，无 nyairo_day 进程）。协调者按既有入口操作：
   `python3 scripts/nyairo_day.py status --root /root/.nyairo-day`（纯读）→ 若在跑先 `stop` → `python3 scripts/nyairo_day.py start --root /root/.nyairo-day`（默认 3831；0.7 起日常入口默认 canonical gate）。
6. 重启后健康检查：`status`/`phone` 读面、冻结锚点三套件、`test_v07_release_complete_flows`。

## 九、终验（2026-11-15 实测收尾）

- **清单终值口径**：MANIFEST 在本会话内共重建四遍，每遍均对 staging 树跑 `verify_manifest.py` 得 `valid=true`（0 changed / 0 unexpected）并将 staging 产物原样部署双侧、md5 实核一致：①首次 13629 文件（v0.8.0 期，tree_sha256 `3741b1c3…`）；②补入本就绪文档后 13630 文件（`2d075d7e…`）；③收尾补丁单四文件（安装器×2 + 两文档）后仍 13630（`365db5c0…`）；④**2026-11-15 用户裁定版本改道 v0.7.0 后终建**——终值以 `MANIFEST.json` 文件本身为准（顶层 version 0.7.0 / revision V07_0_REDACTED_SOURCE_TARGETED_CHECKS / file_count 13630）。历史 tree_sha256（`3741b1c3…`/`2d075d7e…`/`365db5c0…`）均为 v0.8.0 期中间态，不对应任何发布物。
- **终验实测**（部署 v0.7.0 清单与 release.json 之后，WSL `/root/nyairo-v051`）：
  - 21 模块合并跑：`test_v07_release_complete_flows`(10) + 三锚点套件(28+25+22) + 昨日批次 10 模块(118) + 亲密三件与 release_metadata/update/privacy/hardening(123) = **Ran 326, OK**（与 §四逐套件口径逐项一致；含 `test_release_metadata` 对 `release.json v0.7.0 == 'v'+MANIFEST.version 0.7.0` 的一致性门）。
  - `tests.test_v07_daily_life`：**Ran 74, OK**（201.3s）。
  - 合计 400 例全绿。
- **双侧同步**：本单四个改动文件（MANIFEST.json、scripts/release.json、本文件、登记文件）＋7 个预存漂移 website 文件（6 个内容更新 + 1 个 WSL 缺失新增 `website/assets/screenshots/desktop-editor.webp`）全部 md5 双侧一致；全树逐文件 md5 终检见收尾报告。
- **tar 快照**：`/root/nyairo-v051-code-backup-20261115.tar.gz` 已按 §七配方打制（1212 同配方；结构实核与 1212 同族：vendor/__pycache__/.memsearch/dist/data/worker-runs 等排除，含本单全部产物）。

## 十一、三轮修复与全量复扫收官（2026-11-15）

- **三轮修复**（commit `4526e47`，6 文件，+52/-6）：全量 321 模块扫描（发布树 `4227639`）发现 6 失败，三根因——①身体档两种"没读"拼法轮番漂移处境指纹（BODY_NOT_READ / BODY_SITUATION_NOT_READ），"同一处境只考虑一次"的有界沉默失效；②停点"最新"按 set_at 排序在冻结/混合时钟下失真，且 plan_page_read 的 head_number 参数实际无人传（版本戳恒 1）；③test_world_runner / test_world_move_pause_race 兄弟导入缺 tests 目录 bootstrap。修复：body_parts 无真实 owner 事实统一收敛 BODY_NOT_READ；位置行盖 written_in_revision（文档自身单调版本号）、元数据投影白名单放行、_bookmark 与 read_reading_situation 改版本号优先排序；两测试文件补 tests 目录 sys.path。
- **全量复扫**：对最终提交树 `git archive` 干净拉取，并行（10 路）扫 tests/ 全部 321 个测试模块 + worldgen discover：**322/322 全部 PASS，0 失败 0 超时**（最慢三件：test_one_day_life_w05r 449s、test_life_v1_integration 406s、test_v07_daily_life 161s）。
- **清单第五遍**：修复匹配清单（commit `7214bab`，tree_sha256 `498696e6…`，staging verify `valid=true`）；本节收录后按治理再重算一遍，终值以 `MANIFEST.json` 文件本身为准。
