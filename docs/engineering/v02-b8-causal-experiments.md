# v0.2 B8-5：三组因果实验（E12.4）

本批只做 B8-5。分支 `v0.2-autonomous-life`，施工前 HEAD `b253960`。

实验要回答的问题不是"她感觉不同了吗"，而是三个可以分开验证的层次：

1. **信息可达**：真实的 owner 状态或合法经历，确实进入了下一轮。
2. **行为准备变化**：候选、决定依据或具体执行目标因此不同。
3. **实际后果变化**：真实 Action 对正确 owner 产生了不同的、可验证的结果。

第一层不能自动当成第三层。下面每一节都写明观察停在哪一层。

---

## 1. 实验变量与预先确定的通过标准

| 实验 | 唯一变量 | 通过标准（事先定下） | 不构成通过 |
|---|---|---|---|
| A | 两份**真实作品状态**（head 版本、正文、待处理位置） | 每组的准备步骤（目标作品、`expected_head`、要写入的正文）正确反映**本组自己的**作品；两轮之间随本组自己的新版本推进；另一组的 artifact id / 版本 id / 正文绝不出现在本组记录里 | 模型选择不同；文本不同 |
| B | 同一作品、同一 head、同一候选集、同一预算下，**本轮可见经历**（有 / 无） | 送入 AG-1 的请求里，A 组带真实已准入经历及其正文、B 组不带；其余条件逐项相同；经历真实存在于 M0 且由唯一合法写入者写入 | 决定或文本必须不同 |
| C | 空检查窗口 | 空窗口只产生沉默，不产生模型调用、动作、作品版本或编造经历 | —— |

**不允许为了得到想要的结果反复改提示词或筛样本。** 本轮 A/B 两组都用同一个受控认知替身，替身只会返回 `START` 并选中候选集里的第一个候选；这样做是因为施工单明确说"不能强制要求 START/CONTINUE 必须不同"，而实验 A 的通过条件本身是关于**准备步骤**的。

## 2. 测试 profile 与模型配置

* 隔离环境：WSL Ubuntu 的 `/srv/nya02`，两个真实非 root 身份 `nyaop`(1001，只签发授权) 与 `nyasvc`(1002，服务主体)；真实 Life Supply owner 跑在 `/srv/nya02/sock/life-supply.sock`。
* 每个组一个独立 subject + workspace + `COMMIT_MANAGED_ARTIFACT` 授权，作品差异全部由**合法 owner 操作**（`create_artifact` / `commit_version`）建立，没有直接改数据库。
* 每组独立的 Life state root（`/srv/nya02/b8-causal-ca/*`、`/srv/nya02/b8-causal-cb/*`）；每个场景的记忆库（`evidence.db` / `controls.db`）也在各自 root 内。
* 模型 profile：`/root/.nyairo/config.yaml`，`provider: custom`、`default: gemini-3.8-flash-high`、`base_url: https://gw.929711.xyz/v1`，密钥由 root 解析后只经进程环境交给服务身份（不落盘、不打印）。

**本轮该网关从隔离环境不可达**，所以 B2 的真实模型半边标记 `NOT_RUN`：

```
getent hosts gw.929711.xyz   -> 只有 IPv6 (2606:4700:3036::ac43:d1e1 / 2606:4700:3031::6815:5d7c)
ip -6 route show default     -> 无 IPv6 默认路由
TCP 443 -> 104.21.93.124     -> TCP_TIMEOUT      (对照 api.github.com -> 200)
TCP 443 -> 140.82.121.4      -> TCP_OK
```

第一次运行（修复前）确实发起了 6 次调用尝试，全部在传输层失败，AG-1 记为 `DECISION_ATTEMPT_FAILED:MODEL_CONNECTION`（见 `S1-rm-A/agency_decision_journal.jsonl`）。**没有任何一次请求到达 provider，因此没有消耗任何付费额度。** 修好确定性半边后，B 的真实模型半边以 `--max-calls 0` 重跑，不再触碰 provider。

## 3. 每组真实 Artifact / Memory 来源

* **实验 A 的作品**：三个 trial，每个 trial 两组。A 组是有更深历史的同类型作品（head V2 / V3 / V4，正文结尾是需要处理的位置），B 组是 head V1、待处理位置在别处的同类作品。标题与措辞在三个 trial 之间都不同，避免一个固定样例碰巧通过。
* **实验 B 的作品与经历**：三个场景各一个 subject 与一件真实作品；经历来自**真实执行结果**：

| 场景 | 真实执行结果 | AG-2 handoff kind | 谁发出的 handoff |
|---|---|---|---|
| S1 | owner 确认提交了新版本（`APPLIED`，`version_created=True`） | `SIGNIFICANT_PROGRESS` | 生命周期自身（`_emit_progress_experience`） |
| S2 | 另一个合法写入者先落地，owner 以过期 head 拒绝（`CONFLICT`） | `CONFLICT` | AG-2 公开 `emit_experience_handoff`，作用在该轮真实产生的 integration 上（生命周期本身没有 CONFLICT 出口） |
| S3 | 对真实前台 Activity 的真实 `ABANDON` | `ABANDONED` | AG-2 自己（`adopt_decision` 的 abandon 分支） |

三者的经历文本都只由事件的结构化字段拼成（`life_experience_memory_consumer._text`），没有一句是手写的。

## 4. 对照执行命令

```bash
# 隔离环境先起好（真实 owner + 两个身份 + 基础授权），然后：
NYA02_ROOT=/srv/nya02 bash components/life/tests/v02/run_b8_causal.sh a            # 实验 A（0 次模型调用）
NYA02_ROOT=/srv/nya02 bash components/life/tests/v02/run_b8_causal.sh b            # 实验 B（确定性 + 真实模型，≤6 次）
NYA02_MAX_MODEL_CALLS=0 bash components/life/tests/v02/run_b8_causal.sh b          # 只跑确定性半边

# 实验 C 复用既有 B8 检查：
cd components && PYTHONPATH="life/scripts:native:native/src:native/memory_runtime_v1:.:../chiyo_bundle" python3 -B check_life_b8.py
```

驱动脚本会为每个组签发独立 subject + workspace + 授权（每次运行换一次 subject 与 operation id，因为 owner 对 operation id 去重，复用会重放上一次的授权）。

## 5. 每组结果表

### 实验 A：作品状态影响后续行为（3 组对照）

`START` / 候选 `CONSIDER_PERSONAL_OPPORTUNITY` / `step_path=AR0` / Activity `ACTIVE` / LR-5 `progress_state=ADVANCED`。

| 组 | 初始 head | 第一轮 `expected_head` | 第一轮真实新版本 | 第二轮 `expected_head` | LR-5 checkpoint | 决策 |
|---|---|---|---|---|---|---|
| T1-A | V2 (`…c007108e…`) | 本组 V2 | V3 | 本组 V3 (`…7a5ed6d0…`) | `artifact:ec9fe84c…`（本组作品） | START |
| T1-B | V1 (`…993a6d06…`) | 本组 V1 | V2 | 本组 V2 (`…613927d3…`) | `artifact:0a51c690…`（本组作品） | START |
| T2-A | V3 (`…151d2556…`) | 本组 V3 | V4 | 本组 V4 (`…6ba83c4c…`) | `artifact:9edd975f…` | START |
| T2-B | V1 (`…17d06025…`) | 本组 V1 | V2 | 本组 V2 (`…deda7f7b…`) | `artifact:3e1f877c…` | START |
| T3-A | V4 (`…1100c900…`) | 本组 V4 | V5 | 本组 V5 (`…f7524fd9…`) | `artifact:49b6da8c…` | START |
| T3-B | V1 (`…bc5e3ad6…`) | 本组 V1 | V2 | 本组 V2 (`…a6f0d507…`) | `artifact:23c570e1…` | START |

逐项通过的断言（每组 × 三个 trial，共 90 项检查，全部 ok）：

* 作品真的处于声明的 head（A 组 V2/V3/V4，B 组 V1），标记语真的在该组自己的正文里；
* 第一轮的准备步骤对本组自己的 head（`expected_head`、写作者拿到的 `head_version_id`、写作者拿到的正文尾部标记）；
* owner 回执里的 `source_hash` 等于**准备写入的正文**的 SHA-256（准备的东西就是提交的东西）；
* 第一轮真的在 owner 处产生了新版本，且那个版本就是作品的下一轮 head；
* **第二轮**（换了新的 Life state root、面对第一轮刚改过的作品）的准备步骤对的是第一轮产生的那个版本，不是过期的初始 head；
* 两轮都作用在本组自己的 artifact 上；
* 本组记录里**不出现**另一组的 artifact id、任何版本 id、或另一组的正文标记。

`EXPERIMENT_A: PASS`。

### 实验 B：真实经历影响后续决策（3 个合法经历场景）

**B1 —— 经历真的形成并正式准入（M0）**

| 场景 | M0 event id | handoff 来源 ref | 准入计数 | 读回来的经历文本（截断） |
|---|---|---|---|---|
| S1 | `01a1266e-7b69-7990-8f29-eef7ff2c0723` | `lexp:1b65f966aa0db626` | accepted=1, dup=0, defer=0 | 完成了一次真实修改，作品所有者确认了新版本；作品 artifact:…；所有者回执 rset:986e3e14… |
| S2 | `01a1266e-7d22-75df-bc3f-26203e464df3` | `lexp:a63f57aaf76cf339` | accepted=1, dup=0, defer=0 | 一次改动被拒了：读到的版本已经过期，没有覆盖别人的修改；所有者回执 rset:49ae5581… |
| S3 | `01a1266e-7f1b-76db-a80a-62e046c19f7a` | `lexp:fb0dd1149d2a31fb` | accepted=1, dup=0, defer=0 | 放下了这件事（作品仍然留着） |

三条都通过了 `LifeExperiencePort.append_outcome`（唯一合法写入者 `EvidenceStore.append`）落库，并通过边界端口（带治理投影）读回。

**B2 —— 同一 head，只改可见性**

| 场景 | head（A/B 前后一致） | 候选集相同 | legal enums / capacity 相同 | A 组请求里的经历 | B 组请求里的经历 | A 决策 | B 决策 |
|---|---|---|---|---|---|---|---|
| S1 | `artifact-version:502989ce…` | `CONSIDER_PERSONAL_OPPORTUNITY` | 相同 | `01a1266e-7b69-…` + 真实正文 | `[]` | START | START |
| S2 | `artifact-version:1a9525c4…` | `CONSIDER_PERSONAL_OPPORTUNITY` | 相同 | `01a1266e-7d22-…` + 真实正文 | `[]` | START | START |
| S3 | `artifact-version:cb3f694b…` | `CONSIDER_PERSONAL_OPPORTUNITY` | 相同 | `01a1266e-7f1b-…` + 真实正文 | `[]` | START | START |

确定性半边 `EXPERIMENT_B: PASS`，`model calls used: 0/0`。

**真实模型半边**：`NOT_RUN`（网关从隔离环境不可达，见第 2 节）。

### 实验 C：定时器是否伪造主动性（复核）

复用 `components/check_life_b8.py`，本轮在**当前集成代码**上重跑，`B8_CHECK: PASS`，其中第 `[5]` 节：

```
[5] experiment C: empty check windows fabricate nothing
  ok   four windows all report silence
  ok   no model call was made or charged
  ok   no action was spent
  ok   no artifact version and no step were produced
  ok   no fabricated experience was recorded
  ok   the earlier window's budget was not disturbed
```

四个空检查窗口全部 `IDLE_NO_CANDIDATE`，零模型调用、零动作、无新作品版本、无虚构经历、预算未被扰动。**没有做过 24 小时或 7 天的真实观察，本批也不宣称做过。**

## 6. 模型调用次数

| 部分 | 调用尝试 | 真正到达 provider |
|---|---|---|
| 实验 A（3 组对照 × 2 轮 × 2 组） | 0 | 0 |
| 实验 B 确定性半边 | 0 | 0 |
| 实验 B 真实模型半边 | 6（首次运行，传输层全部失败） | **0** |
| 实验 B 真实模型半边（修复后重跑） | 0（`--max-calls 0`） | 0 |
| 实验 C | 0 | 0 |

**本批实际付费调用：0 次。** 施工单允许的 6 次没有用掉，因为网关不可达，不是因为省着用。

## 7. 观察到的差异与未观察到的差异

**观察到的（第一层，信息可达）**

* 实验 A：两组的准备步骤逐项不同，且每一组都只反映自己的作品——`expected_head`、写作者拿到的 head 与正文、owner 回执的 `source_hash`、产生的版本、LR-5 checkpoint 全部是本组自己的；另一组的任何 id 与正文一次都没有出现。
* 实验 A：第二轮的准备步骤跟着本组自己的新版本走（A 组 V2→V3→V4…，B 组 V1→V2→V2），不会停在过期的初始 head。
* 实验 B：A 组的决策请求里带着真实已准入经历的 id 与正文，B 组不带；两组的 head、候选集、legal enums、capacity 逐项相同。**唯一变量确实只是经历可见性。**

**未观察到的（第二、三层）**

* 实验 B 没有观察到决定差异：A/B 两组都是 `START`、同一个候选、同样的 `reason_codes`。受控替身只会返回 `START`，所以这**不能**说明真实模型也不会不同；真实模型半边 `NOT_RUN`，所以也没有证据说它会相同。
* 实验 B 没有观察到"实际后果变化"：两半都用不改动作品的写作者，owner 都答 `NO_CHANGE`，head 前后一致。这是刻意的控制（要在同一 head 上比较），也意味着本轮**不主张**经历改变了真实后果。
* 实验 A 没有观察到模型选择差异（替身固定返回 `START`）；本轮**不主张**作品状态改变了她的选择倾向。

## 8. 哪些结论可成立，哪些仍属长期观察问题

**可以成立**

1. 下一轮的准备步骤由**真实 owner 状态**决定：作品、head 版本、正文、checkpoint 都取自本组自己的 owner 读取，不误用另一组的任何东西（实验 A，90 项断言）。
2. 一次真实执行结果可以形成**合法经历**并正式进入 M0：三个不同性质的真实结果（成功提交 / 冲突拒绝 / 放弃）都拿到真实 event id、真实 handoff 来源、`accepted=1 / duplicate=0 / defer=0`，且能从边界端口按治理规则读回（实验 B1）。
3. 经历可见性可以做到**单变量控制**：同一 head、同一候选、同一容量下，只让一侧的请求带上已准入经历，另一侧完全不带（不删存储），并且这一点由请求本身证明（实验 B2 确定性半边）。
4. 定时器不会伪造主动性：空窗口只产生沉默（实验 C，`B8_CHECK: PASS`）。

**仍属长期观察问题（本轮不下结论）**

1. 真实模型在有无经历时是否会作出不同选择——本批无法测（网关不可达），需要一次可达 provider 的运行。
2. 一次文本不同是否意味着形成长期人格——不成立，单次文本差异不是人格证据。
3. 经历是否改变了长期行为分布——需要多轮、跨天、跨重启的观察，本批没有做。
4. 24 小时 / 7 天级别的空转与自发行为——本批没有做，也不宣称做过。

**必须一起读的说明**

* 确定性测试不能证明真实模型必然作出不同选择；它只证明接线与约束成立。
* 单次文本不同不证明形成长期人格。
* 实际 owner 状态差异（实验 A）与 Memory 经历影响（实验 B）是**分别归因**的，不能互相代替。
* `NO_ACTION` 也是合法结果；本轮没有为了让实验"成功"而强行执行任何动作。

## 9. 测量中发现、本轮未修的报告缺口

这两条都是**报告层**的缺口，不是正确性缺口；harness 改从真正的 owner 读取权威值，没有绕过任何判定。

1. `autonomous_life_cycle` 在 ABANDON 轮里于读取 adoption / integration 记录**之前**就返回，所以该轮报告里没有 `experience_handoff` 字段——而 AG-2 其实已经发出并投递了 `ABANDONED` handoff（实验 B 的 S3 由 M0 写入者侧证实）。
2. `autonomous_life_cycle._record_artifact_progress` 去读 LR-5 结果里的 `checkpoint_update` 键，而 LR-5 实际把 `checkpoint_ref` 放在结果顶层，所以该轮报告里的 `checkpoint_ref` 是 `null`，而 LR-5 里的 checkpoint 是真实推进的（实验 A 由 `activity_read` / `progress_read` 读到）。

## 10. 产品级缺口（如实记录，未绕过）

* **`NO_CHANGE` 无法成为经历**：AG-2 的 handoff 词表只有 `COMPLETED` / `ABANDONED` / `SIGNIFICANT_PROGRESS` / `CONFLICT` 四个，没有一句能如实描述"owner 说没什么可写"。实验 B 的 S3 第一轮就是这种情况：真实 `NO_CHANGE`、真实 `OUTCOME_ALREADY_DONE`、**零 handoff**。把这条硬塞进 `CONFLICT` 会写出假文本，所以本轮没有这么做。
* **M0 的 memory owner 是实例主体**：`MemoryAdmissionExperienceConsumer` 用事件自己的 `subject_id`（AG-2 handoff 不带这个字段）回落到 `chiyo`。本批为隔离而给每组不同的 Supply subject，M0 侧仍是同一个 memory owner；这在只有一个主体的真实产品里是正确的，只是隔离实验必须显式知道这一点（harness 已按此断言）。

## 11. B8-6 启动前的剩余阻塞

1. **常驻驱动未定**：`runner_tick()` 目前只由 CLI / 宿主驱动，没有后台线程；插件 `pre_llm_call` 钩子尚未挂 `runner_tick()`。这是产品语义决定，需要主人定。
2. **网关从隔离环境不可达**（IPv6-only 解析 + 无 IPv6 路由，IPv4 亦超时）：任何真实模型实验都需要一条可达路径，否则只能 `NOT_RUN`。
3. **B8-4 遗留未动**：T20（缺 v0.1 形态 profile fixture）、T12（原生生活经历 CORRECT 入口未实现，不得伪造用户消息）、T06/T07（有前台 Activity 时 AG-1 规则先解析，PAUSE/ABANDON 不经模型）。
4. **GitHub 推送仍失败**（连接被重置）：本批提交只在本地，离线 bundle 兜底。
5. **宿主机 C: 盘空间耗尽（0 字节可用）**：本批的临时脚本因此改放仓库内（与 B8-2/3/4 的 `run_*.sh` 一致），不影响交付，但后续批次需要先腾出空间。
