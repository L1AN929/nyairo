# Agent Note: v0.4 旧作重访 · 原 owner 历史读协议与有来源新作

Status: implemented

## Problem

主体「能找回自己的旧稿」这件事在 0.4 之前是断的，断点不在存储而在**读协议**：

- Supply 的 `artifact_versions` 一直是不可变行（`lifesupply/db.py:343-357` 的 BEFORE UPDATE/DELETE
  触发器），`ManagedArtifactStore.read_version(version_id)` 也早就在（`artifact/store.py:274-280`）；
- 但 socket 服务对外只有 `list_artifacts / read_artifact / read_head / lookup_operation /
  read_effect_receipt`（`life_supply.py:223-228` 改前），**没有**"列出这件作品的版本"和"按名读某一版"
  两个 op；
- 于是窄端口 `LifeArtifactPort` 只能保证 head，`PrivateProjectService` 只能诚实地说
  `complete=False / PORT_LACKS_LIST_VERSIONS`（2026-10-27 私人项目笔记把这条记成未闭合项）。

同时"另开一件有来源的新作"没有落地形状：新建 artifact 的嵌套字段只有
`{subject_id, workspace_id, title, content}`，没有 provenance 字段，直接加列就是 schema 迁移。

## Decision

只在本阶段写集内改，先扩**原 owner 的读协议**，再往上一层层接：

1. **owner 两条纯读**：`list_versions(subject, artifact_id, limit?, before_version_number?)` 回
   元数据（版本号/父版本/存库 `content_sha256`/字符数/时间/`operation_key`）+ `total_versions` +
   `next_before_version_number`；`read_version(subject, artifact_id, version_id, max_chars?)` 回正文
   与**全量** `content_sha256`（被切时 `content_truncated=true`、`content_chars`/`returned_chars` 分别给）。
   `store.list_versions()` 的 `None` 只表示读不到，永远不等于"没有版本"；`limit>50`、`before<1`、
   `max_chars` 越界都在 owner 侧拒（`LIMIT_OUT_OF_RANGE` / `CURSOR_INVALID` /
   `MAX_CHARS_OUT_OF_RANGE`）。
2. **历史读不豁免当前状态**：`_history_subject_artifact()` 先做 subject 归属，再扣留 tombstone
   （`ARTIFACT_TOMBSTONED`，历史一并不可读），`ARCHIVED` 仍可读并把 `lifecycle` 回传给调用方；
   具名版本必须属于该 artifact，否则 `VERSION_NOT_IN_ARTIFACT`（store 那条 `read_version` 只按
   version_id 取，绑定必须在服务层判，这一条是本次补的）。
3. **端口/项目服务**：`LifeArtifactPort.list_versions/read_version` 归一化并给出
   `complete/has_more/reason_code`；`complete=True` 只在"这一页就是全部历史"时成立，带游标的页是
   `HISTORY_PAGED`。`PrivateProjectService` 新增 `read_project_version / compare_project_versions /
   read_project_provenance / create_derived_project`，`_history` 兼容 dict 与旧的纯序列两种答案。
4. **比较以 owner hash 为准**：两侧都有 `content_sha256` 时 `content_changed = from_hash != to_hash`，
   同正文不同版本判 `machine_head_only=True`，不把 NO_CHANGE / 机器头说成一次修改；无 hash 且有界切片
   被切则 `content_changed=None` + `COMPARISON_INCOMPLETE`。
5. **有来源新作 = 新 artifact 单 parent + 首版结构化来源行**：复用仓库已有的"首行
   `<!-- name:v1 {json} -->`"约定（`research_workspace_service.py:86-96`），新标记
   `nyairo-work-source:v1`，内容为 `source_artifact_id / source_version_id / source_version_number /
   source_content_sha256 / purpose(quote|adapt|reuse) / note / taken_at`。写入前**当场重读**来源，
   不可读/被 tombstone/被截断/版本不属于该 artifact → `SOURCE_NOT_READABLE` + `source_reason_code`。
   来源永远从第 1 版读回（不可变），所以后续续写洗不掉它；原件 head/revision/版本数不变。
6. **手机**：新增 4 条 GET（版本页 / 具名版本 / 与 head 的比较 / 来源）与 1 条 POST
   `/api/phone/projects/derived`；全部沿用既有 `_guard_common`/`_guard_mutation`（Host / Origin /
   Sec-Fetch / CSRF）、`_read_body` 白名单、`record_operation` 归因。GET 零写。

## Alternatives considered

- **在 `life_artifact_port` 里用 `read_head` 反复倒推历史**：最强立论是不用改 owner 协议、当晚就能有
  "2 个版本"，但 `read_head` 只能拿当前 head，`artifact_versions` 没有任何"父版本反查"读口，倒推就是
  让调用方猜造历史；而且 0.2 起就写明"不发明版本"。否决。
- **给 Life 侧开一条直读 `life_supply.sqlite` 的只读连接**（最快拿到全部版本与正文）：直接违反
  "不绕 owner 直读数据库"，也让 SO_PEERCRED 身份、Governance 与单写者围栏全部失效。否决。
- **在 `managed_artifacts` 加 provenance 列 / 新建 provenance 表**：语义最干净，但要动 canonical
  schema（迁移、兼容、原实例升级风险），而设计只要求"沿用已有结构化标记或 provenance 契约的**最小**
  方案"。规模与风险都不成比例，否决。
- **把"另开一件"做成 fork/多 parent 合并**：能表达"从第 2 版分叉"，但 17 §5.2 的版本身份是单 parent、
  不可变头；多 parent 会把 CAS 与 head 语义整个改写。否决，保留"新作品 + 首版来源行"。
- **历史读时把 `ARCHIVED` 也拒绝**（更保守）：会让"她关掉的作品"永远读不回来，而 archive 本来就是
  "关闭不是删除"。改为可读 + `continuable=False`，比"全域更严"更符合各自现有契约，也不偷偷放宽
  任何别的服务。

## Consequences

- 收益：旧稿第一次**能从原 owner 读回**：版本清单有界可分页、具名版本可读、与当前 head 的比较能区分
  真实正文差异与纯机器头；她能以"引用/改编/直接复用"三种用途另开一件有来源的新作，原件一动不动；
  手机 GET 纯读、POST 带 CSRF/白名单/CAS/记账；同实例新 runtime 读回同样的版本与来源，不重放。
- 代价 / 已知限制：
  - 新增标记 `nyairo-work-source:v1` 是**本阶段新增**的标记名，只有 `PrivateProjectService` 认它；
    其它读面（研究/实践/资料）把它当普通首行，不解析也不改写。
  - 后续由别的通道（如 Life 写作动作直接 `commit_version`）续写时不会重复来源行；来源仍从第 1 版读，
    因此不丢，但 head 上不再有那一行——这是有意为之的"单 parent + 不可变首版"取舍。
  - 手机界面与真实浏览器点击、正常 Life 循环里旧正文进入实际写作上下文，本段**未做**；见
    `docs/engineering/v04-work-revisit-result.md` §7。
  - 本段真实产品模型 calls = 0，不构成任何"偏好/成长/学习"证据。
- 不变量：无新表/新库/新 owner；不直读 SQLite；历史读不写 head/operation/Activity、不恢复旧版本；
  删除面没有变化（archive 仍是关闭，tombstone 仍扣留内容）。

## Review round (bounded, A–F)

- 派生文本面新增 `_source_chain`/`_source_gate`：从不可变首版读来源、逐跳用 owner 判当前可读、深度 3、
  环即拒、首版读不到 fail-closed；`read_project`/`read_project_version`/`compare_project_versions`/
  `read_project_provenance` 在源不可读时只留元数据（id/版本号/字符数/hash），正文、比较差异与来源 note 全部扣留。
- `create_derived_project` 的默认 key 改成完整意图（source id/version、title、body、purpose、note）；
  `taken_at` 用源版本的 owner 记录时间而非墙上时钟；显式 key 先 `lookup_operation` 对账，异意图 →
  `OPERATION_KEY_REUSED_WITH_DIFFERENT_INTENT`；写完再验一次源（owner 没有读+写复合边界，这是补丁不是复合事务）。
- 比较先剥本模块标记：`body_changed` / `hash_changed` / `headers_differ` 分开，`machine_head_only`
  只在 hash 与正文都没变时为真。
- 读权限如实：读 op 只用 host-operator/service-principal + subject 归属 + lifecycle，**不查 grant**；
  新增两条读口与既有读口同权，报告不再声称“已验当前 grant”。
- 来源标记是自述（`verified=False`），不授权读、不驱动自动复用；`source_id`+hash 单独换不到访问权。
- 仍未做：手机界面屏、真实 Edge/CDP 点击、正常 Life 循环写作上下文、Life runtime 重启、owner 读+写复合边界。

