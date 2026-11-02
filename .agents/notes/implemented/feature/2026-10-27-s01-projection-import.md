# Agent Note: S01 作品 Markdown 投影、外部编辑观察与显式导入（F-011/F-012/F-014）

Status: implemented

## Problem

设计 VIII.3 要求 S01 持有 canonical identity/version，文件系统只是 representation 与外部证据，
且 `file exists`、`same hash`、`authored by Chiyo`、`used by Activity` 是四种不同事实。
树上的 `ManagedArtifactStore` 已经有投影写入、`check_projection`、`rebuild_projection` 和
COMMIT_MARKDOWN 的 CAS，但缺的正是「外部编辑之后」这一段：

1. `check_projection` 只返回 `OK`/`CONFLICT`，把「正文读不出」与「投影文件不一致」混成一个答案，
   也没有 `MISSING`/`UNBOUND` 的区分；正文健康与投影健康无法各报各的。
2. 外部改过的 `.md` 只能靠人工 `rebuild_projection` 盖掉；没有 Observation、没有候选、没有
   「作者未知」的诚实标注，也没有显式确认入口。
3. 更严重的是：canonical 每次提交都会自动重写投影。若外部编辑尚未处理，这次写入会**静默覆盖**
   用户改动，正好违反「不静默覆盖外部证据」。
4. 导入若直接复用普通 `commit_markdown`，版本行没有 schema 位置记录 `EXPLICIT_IMPORT` 来历；
   加表会动冻结的 `db.py`/`contract.py`。

## Decision

**1. 观察是纯读，两个健康域分开（`store.observe_projection`）。**
一次真实读取绑定路径与文件，返回 `content_health`（`AVAILABLE`/`UNAVAILABLE`/`CORRUPT`）与
`representation_health`（`CURRENT`/`MISSING`/`DIVERGED`/`UNBOUND`）以及候选/文件事实，
不写行、不落 Observation 事件、不修投影。`MISSING`（文件没了）与 `UNBOUND`（没有绑定或绑定
路径不可用）分开；正文读不出时即便文件在，也不叫作品成功。

**2. 候选的基线来自 owner 自己的 `projection_sha256`（`candidate.base_projection_version`）。**
文件名/hash/目录都不能证明作者，候选固定 `authority=EXTERNAL_UNKNOWN`。基线不是「当前 head」：
若 head 在外部编辑待处理期间前进，绑定里记录的仍是上次投影写入的版本，导入会答 `HEAD_MOVED`，
不会把候选悄悄重绑到新 head。

**3. 显式导入走原 owner 写口（`import_projection`）。**
必须 `confirm=true`、候选 id、候选自己的基线版本和已有 `COMMIT_MANAGED_ARTIFACT` grant；
`commit_markdown` 的 CAS 前置仍是 `expected_head`。同 operation key 同意图返回原版本（replayed），
异意图 `CONFLICT`。新版本照常是不可变版本行；来历写进该操作自己的 `result_json`
（`source_kind=EXPLICIT_IMPORT`、`external_contributor=UNKNOWN`、基线、候选与投影 hash、
`imported_at`），`read_version` 从 owner 操作行读出，普通提交没有来历就报 `ORDINARY_COMMIT`。
不加表、不改 `db.py`/`contract.py`，也不伪造 creator receipt。

**4. 自动投影写入保护外部编辑（`_update_projection`）。**
自动路径先比较文件与绑定记录的 `projection_sha256`；不一致就保留文件并把 outbox/binding 标
DIRTY，等显式导入或对 `MISSING`/`UNBOUND` 的显式 rebuild。显式导入可以覆盖，但只在文件仍等于
用户确认的那个 hash 时；观察后又变过的文件不会借旧候选被导入。

**5. 显式 rebuild 只修 `MISSING`/`UNBOUND`。**
服务层 `rebuild_projection` 遇到 `DIVERGED` 直接拒绝；低层 `store.rebuild_projection` 保留原来的
强制语义（已有测试/显式维护使用），不把它暴露成普通修复。

**6. 文件安全边界。**
绑定路径必须相对且无 `..`；逐段 `lstat` 拒绝 symlink；解析后必须在 projection root 内；
最终必须是普通文件；读取有 256 KiB 上限、严格 UTF-8；写投影前拒绝 symlink 目录。任一失败
给出具名 reason（`UNSAFE_BINDING_PATH`/`SYMLINK_REFUSED`/`PROJECTION_TOO_LARGE`/…），
不读取受管范围外目标，也不扫描用户磁盘。

用户的入口是 `scripts/s01_projection_cli.py`（status/observe/import/rebuild/versions/provenance），
手机透传读面本次没有接，因为 CLI 已能完成整条路径。

## Alternatives considered

- **给 projection/candidate 新建表记录候选与来历。** 最强理由：候选天然可持久、可查询。
  否决：schema 属于冻结的 `db.py`；候选完全可由「绑定 + head + 文件 hash」确定性重算，
  重启后同一候选 id 仍然成立，不需要第二份真相。
- **把导入做成普通 `commit_markdown`，用调用方自称的 author 字段。** 最强理由：零新操作。
  否决：自称作者不能当已验证事实；而普通提交没有位置诚实地标 `EXPLICIT_IMPORT`。
  把来历写进 owner 自己的不可变操作行，既不改契约也不丢事实。
- **head 前进时自动 rebase 候选（三方合并或重放 diff）。** 最强理由：用户少一次手工操作。
  否决：设计明确 Stage 1 不做自动三方合并；重放外部 diff 会把「外部贡献」变成未经确认的
  自动改写。正确行为是 `HEAD_MOVED` 冲突。
- **提交时照旧无条件重写投影。** 最强理由：投影永远跟 head 一致，演示最简单。否决：这会
  静默覆盖用户刚改的文件，直接违背外部编辑作为证据的要求。
- **让 rebuild 成为「一键恢复一致」并允许覆盖 DIVERGED。** 否决：默认覆盖会丢掉证据；
  DIVERGED 只能经显式导入处理。`MISSING` 才是可安全重建的。

## Consequences

- `scripts/s01_projection_demo.py` 用真实 owner、真实受管 Markdown、真实 CAS 和同根重启跑完整条
  路径，打印 CHECK 并以 `S01_PROJECTION_DEMO_OK` 结束；0 产品模型。
- 自动提交遇到未处理的外部编辑时不再覆盖文件，而是保留并标 DIRTY；head 仍成功前进，
  之后该编辑只能经 `HEAD_MOVED` 冲突或显式导入处理，不会被误认为基于新 head。
- 版本操作结果多了一个 `provenance` 字段；表结构、owner 分工、`db.py`/`contract.py` 与既有
  `commit_markdown` 语义（幂等、CAS、NO_CHANGE、receipt）都没有改。
- 已知边界：候选不持久成独立记录（按上述事实可重算）；没有可信作者身份协议，因此外部贡献
  永远只标 `EXTERNAL_UNKNOWN`；手机未接读面；这不是 G1、不是 24h，也不证明真实模型会自然
  编辑文件。
