# v0.4 EP-16 私人相册：真实本地导入与同 owner 保存（施工结果）

日期：2026-10-22。分支 `work/rc1-phone-world-body`。本单未提交、未推送、未部署。

## 交付形状

- 用户在本机选择 PNG/JPEG → 浏览器真实解码预览 → 填写说明（≤200 字）→ 保存 → 双列网格 → 详情大图/来源 → 刷新与同根重启后仍在。
- 每张图片是一件普通 Supply `ManagedArtifact`：首版是确定性 JSON 清单（`album.image.v1`，不含字节），字节在 owner root 的 `media/<sha256>.png|jpg`。
- 只有 PNG/JPEG，解码后 ≤5 MiB。检查 base64 严格性、签名与必要结构；不手写完整解码器，不声称内容净化或 EXIF 擦除。
- 图像字节仍受 subject/生命周期检查；`ARCHIVED`/`TOMBSTONED` 后说明、字节与列表扣留，文件不物理删除。
- 相册清单从私人项目列表/详情/历史/比较/来源/提交/关闭与 `latest_resumable` 中窄只读排除，不会被续写成文字作品。

## 改动文件

新增：

- `components/supply/lifesupply/artifact/album.py`
- `components/phone/album.py`
- `components/supply/lifesupply/tests/test_album_images.py`
- `tests/test_v04_album_images.py`
- `tests/test_phone_album_ui.mjs`
- `scripts/phone_album_owner_fixture.py`
- `.agents/notes/implemented/feature/2026-10-22-v04-private-album-import.md`（由原 proposed 同位流转，原路径已退出）
- 本文件

修改：

- `components/supply/lifesupply/artifact/store.py`（可选 `media_root`，窄 `put_media`/`get_media`）
- `components/supply/lifesupply/service/life_supply.py`（四个相册 op；8 MiB 帧只服务相册导入，旧 op 仍 1 MB）
- `components/phone/service.py`（相册适配、真实字节校验、类型排除）
- `components/phone/server.py`（相册 GET/POST、精确图片字节响应、8 MiB 仅相册 body）
- `components/phone/web/app.js`、`components/phone/web/style.css`（入口、双列、详情、导入与预览）
- `components/life/scripts/private_project_service.py`、`components/life/scripts/life_artifact_port.py`（相册清单不可续写）

未改：`db.py`、`contract.py`、OWNERSHIP_REGISTRY、MANIFEST、world_move 修复脚本、AGENTS/CLAUDE/NOW/PROJECT_INDEX/RUNBOOK、dispatch、检查点/launch、其他测试与既有 dirty。

## 实跑命令与结果（WSL，`/tmp/rc104-venv`）

1. Supply 相册新用例：

   ```bash
   cd components/supply
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
     /tmp/rc104-venv/bin/python -B -m unittest -v lifesupply.tests.test_album_images
   ```

   结果：`Ran 15 tests ... OK`，exit 0。含真实 socket、>1 MiB 且 <5 MiB 图片走完整帧、旧 op 1 MB 上限、
   同 key 重放只一版、异意图冲突且不写第二个文件、不同 key 同图复用字节、跨 subject、
   字段注入、tombstone 扣留且文件仍在、同根完全停启后读回。

2. Supply 既有 owner 套件（原 discover 命令）：

   ```bash
   cd components/supply
   PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. \
     /tmp/rc104-venv/bin/python -B -m unittest discover -s lifesupply/tests -q
   ```

   结果：`Ran 137 tests ... OK`，exit 0。

3. 手机端到端：

   ```bash
   cd /mnt/d/nyairo-rc1-body-backend
   PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_v04_album_images
   ```

   结果：`Ran 7 tests ... OK`，exit 0。真实 Supply owner + `PhoneService` + loopback HTTP；
   CSRF/字段注入拒绝且不写；列表/详情/真实字节与 `image/png` + 四个响应头；>1 MiB 图片完整往返；
   同 key 重放与异意图冲突；tombstone 后 404 且文件保留；相册不进入项目/`latest_resumable`/项目提交；
   同根完全停启后读回字节、说明与“本机导入（自述）”来源，未重 seed。

4. 相关回归：

   ```bash
   PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -q \
     tests.test_phone_api tests.test_phone_projects_api \
     tests.test_v04_work_revisit_history tests.test_v04_work_revisit_writer_chain
   ```

   结果：`Ran 41 tests ... OK`，exit 0。

5. 前端：

   ```bash
   node --test tests/test_phone_album_ui.mjs
   node --test tests/test_phone_ui.mjs tests/test_phone_projects_ui.mjs \
     tests/test_phone_reading_ui.mjs tests/test_phone_research_ui.mjs \
     tests/test_phone_practice_ui.mjs tests/test_phone_world_ui.mjs
   ```

   结果：相册 10 pass / 0 fail；既有 121 pass / 0 fail。

6. 预览夹具：

   ```bash
   /tmp/rc104-venv/bin/python -B scripts/phone_album_owner_fixture.py \
     --root /tmp/album-fixture-check-20261022a --port 0 --check
   ```

   新根与同根重启两次均输出 `album_count=0`、`check=OK`，exit 0；已有根未覆盖、未删除、未重 seed。

7. 笔记门禁：

   ```bash
   node --experimental-strip-types C:/Users/28556/.claude/skills/write-notes-like-deepseek/scripts/verify-agent-note-tree.ts
   node --experimental-strip-types C:/Users/28556/.claude/skills/write-notes-like-deepseek/scripts/verify-agent-note-format.ts
   node --experimental-strip-types C:/Users/28556/.claude/skills/write-notes-like-deepseek/scripts/verify-archived-agent-notes.ts
   ```

   结果：tree 82 篇 OK、format 82 篇 OK、archive 0 篇 OK；均 exit 0。

## 未验证 / 限制

- **真实浏览器未验**：任务书把 `preview_start`、Edge 实际文件选择、预览、保存、刷新留给父级；
  本单只跑了 HTTP/Node/夹具冒烟，不自报浏览器通过。
- 媒体文件与 SQLite 没有跨介质原子事务：先文件后引用，数据库失败可能留孤儿文件；不做 GC，
  不承诺跨备份防复活，EP-09 的既有恢复限制不因此消失。
- 签名/结构检查不是完整解码或内容净化，不擦除 EXIF/位置/拍摄时间；图片内容不算亲历事实，
  不自动写 Memory/经历，不外发。
- 已知红未触碰、未复跑：`tests.test_life_v1_restart` 整模块历史红；
  `tests.test_v04_private_projects_stage.LifeOwnerAdapterTest.test_gateway_creates_one_project_through_the_owner`
  的裸 id 断言问题。两者均非本单引入，也未夹带修改。
- 全量 `scripts/test.py` 未跑，不称全绿。

## 2026-10-22 独立验收返修

审查指出五项缺口，本段记录返修的失败、修复与复跑。承重墙、DDL、`db.py`/`contract.py` 未动。

### 修复前实跑红

先按具名场景写用例再改代码，修复前结果：

- Supply：`test_foreign_subject_cannot_serve_another_import_by_copying_its_manifest`、
  `test_same_subject_cannot_revive_a_tombstoned_picture_by_copying_its_manifest` 读到 `OK` 与真实字节；
  `test_replay_from_another_subject_is_refused_and_serves_no_bytes`、
  `test_replay_after_grant_revocation_writes_nothing` 得到 `NO_CHANGE`。
- HTTP：`test_album_reads_pass_the_same_host_origin_and_fetch_guard` 的坏 `Origin`
  对 `/api/phone/album` 返回 200 与列表正文。
- （tombstone 重放在修复前已因 `read_head` 过滤 tombstone 而回 `CONFLICT`，仍保留为回归。）

### 修复

1. **GET 闸门**：`components/phone/server.py` 的相册列表/详情/图片三条 GET 统一先走原
   `_guard_common`（Host/`Origin`/`Sec-Fetch-Site`），拒绝时只发 JSON 信封；其他路由未改。
2. **取值绑定真实导入**：`life_supply.py` 新增 `_album_binding_digest` /
   `_album_import_binding`。读取要求 artifact 的 `CREATE` 操作键在 Governance 有一条
   `CONSUMED` reservation，其 `action_digest` 与“该 subject/workspace/说明/媒体摘要与字节数”的
   导入 payload 一致，且当前 head 是这次导入的首版。`store.py` 只新增两个只读查询
   （`read_create_operation`、`read_reservations_for_action`），未新增表、owner 或密钥。
3. **重放条件**：`_op_import_album_image` 的 existing-create 分支先核同一 subject、`ACTIVE`、
   head 仍是同一清单、绑定仍在，再对请求携带的 grant 做 `validate_grant`；任一不满足即拒绝，
   **且在任何 `put_media` 之前**。tombstone 不会恢复文件，撤权不会伪成功；旧 `create_artifact`
   语义未改。
4. **夹具 socket**：`scripts/phone_album_owner_fixture.py` 每次运行绑定新的
   `supply-album-<pid>-<ns>.sock`，停止不再 unlink；同根重启只更新 `instance.json` 的
   `supply_socket`，不重 seed；既有目录没有有效 `instance.json` 时 exit 2 拒绝；stdout 改为
   实测 `album_count=<n>`，不再假设“空”。旧 socket 文件保留在盘上（实跑两代并存）。
5. **UI 文案**：相册列表页脚与导入页不再向用户暴露 “Supply / ManagedArtifact owner / 清单”，
   改为“图片保存在这个实例里；说明是你自己填的”等；来源自述与“不自动写进记忆”的边界保留。

### 修复后实跑（WSL，`/tmp/rc104-venv`）

- Supply 相册：`Ran 20 tests ... OK`，exit 0。
- 完整 Supply discover：`Ran 142 tests ... OK`，exit 0。
- 手机端到端：`Ran 8 tests ... OK`，exit 0。
- 手机相关回归（含上述两套）：`Ran 49 tests ... OK`，exit 0。
- Node：相册 10 pass / 0 fail；既有六套 121 pass / 0 fail。
- 夹具：新根、同根重启 `--check` 均 `album_count=0`/`check=OK`/exit 0；两代 socket 文件并存；
  未知目录 exit 2 且未写入任何文件。
- `git diff --check` 对写集 exit 0；改动 Python 文件 `ast.parse` 全部通过。

### 未验证 / 边界

- 真实浏览器点验仍由父级执行；本单只报 HTTP/Node/夹具实跑。
- 绑定依赖共享单库中的 `permission_reservations` 只读事实，与 Workspace owner 既有读取同类；
  不承诺跨备份防复活，也不把该读取扩成新的授权域。

## 2026-10-22 浏览器验收返修：img URL 缺 API 前缀

父级用 Edge 155 在隔离根 `/tmp/nya-album-parent-20261022-1525` 真实导入 PNG/JPEG 两张，
预览与保存成功，但 `<img src>` 实际是 `/album/artifact:.../image`；服务只挂
`/api/phone/album/artifact:.../image`，network 404，界面显示“这张图片现在读不到”。
旧 Node 测试把错误地址当成期望值，所以首次浏览器验收才抓出来，不能把旧测试绿写成浏览器通过。

根因：`albumImagePath()` 返回给 `request()` 的相对路径，`request()` 统一补 `API_BASE`；
但 `albumPicture()` 把同一个相对路径直接写进 `<img src>`，浏览器不会经过 `request()`。

修复（仅相册前端）：

1. 新增 `albumImageSrc(artifactId)`：`API_BASE + albumImagePath(id)`，空路径仍返回空。
2. `albumPicture()` 的 `src` 改为 `albumImageSrc(artifactId)`；列表缩略图与详情大图共用这一处，
   因此两屏同时修复。`request()` 与 `albumDetailPath()` 未改，metadata 不会双加前缀。
   id 校验（`albumIdFor` / `ALBUM_ID_SAFE`）、禁止外部 URL/blob、来源与生命周期扣留均保持原样。

测试：`tests/test_phone_album_ui.mjs` 新增两条断言，一条查地址构造与源码接线，
一条用仓库既有的 stub DOM 真实 boot、点主屏“相册”、渲染列表与详情，读取真实 `img` 节点。
负控时把 `src` 临时还原为 `albumImagePath`，stub DOM 断言实跑红：
`actual '/album/artifact:abc/image'`、`expected '/api/phone/album/artifact:abc/image'`；
恢复后绿。

实跑：

- `node --test tests/test_phone_album_ui.mjs`：12 pass / 0 fail，exit 0。
- Python `subprocess` 展开全部 `tests/test_phone*.mjs`（7 个文件）给 `node --test`：
  133 pass / 0 fail，exit 0。
- notes 门禁（`npx tsx`）：tree 82 篇 OK、format 82 篇 OK、archive 0 篇 OK，均 exit 0。
- 本写集 `git diff --check`：exit 0。

未验证 / 边界：stub DOM 不加载图片、不做布局，不能替代浏览器；真实 Edge 里两张原图的缩略图与
大图请求、来源与生命周期扣留由父级在同一隔离根复验，本单不启动/停止服务或浏览器。
本次未改服务端契约、未重跑 Python/HTTP 套件，因此不回写旧 HTTP 结论。
- 相册列表每行多做两次窄查询以核绑定，受既有 1000 行扫描上限约束，未做新的索引或缓存。
