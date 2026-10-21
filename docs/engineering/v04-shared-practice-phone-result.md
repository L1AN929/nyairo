# v0.4 阶段 C｜手机共同实践（phone 唯一 writer）交付与精确接口

Status: implemented
Scope: `components/phone/practice_shared.py`、`components/phone/service.py`（共同实践段）、
`components/phone/server.py`（共同实践路由与守卫）、`components/phone/web/app.js`（共同实践两屏）、
`tests/test_v04_shared_practice_phone.py`、`tests/test_v04_shared_practice_phone_ui.mjs`。
本阶段**只**写手机这一半：A 的 `shared_practice_service.py`、B 的 normal Life / local_contact 一律**没动**。

## 1. 现在的真实主屏路径（不是纯函数）

主屏 → 「共同实践」行内按钮（`renderHome` 的 `home-shortcuts`）→ 共同实践屏：

1. `GET /api/phone/shares/sources`（B 的 `LifeSharingService.sources()`）列出**owner 现在真的读得到**的共享来源
   （含 `head_version_id`）；读不到的那一条带原因、**不可选**，界面上不出现私人材料全集。
2. 点一份来源 → 带上 `source_artifact_id` + `source_version_id`（就是那一版的 head）。
3. 写标题 + 第一轮 → `POST /api/phone/shared-practice`。
4. 进入「这一次」屏：材料引用/版本、双方的来回（谁写的分开显示）、共同作品正文、
   owner 自己的版本历史（编号 + 字数，**不带正文**）、权限边界。
5. 「提交我这一句」/「改我这一段（新的一轮）」/「暂停这次共同实践」/「显式结束」四种用户意图。
6. 每次写完只做一次重读（`refreshAfter('shared-practice…')`）；没有定时器、没有轮询。
7. 重载：导航位置（屏幕 + 这一次的 id）存在 `sessionStorage` 的 nav 里，重进这一屏**重读 owner**；
   「已暂停 / 已结束 / 双方贡献 / 作品版本」都是服务的事实，不是界面记忆。

## 2. 精确接口（A 的 owner，caller = `user`）

手机只按**已发布的方法名 + 具名关键字**调用，调用前用 `inspect.signature` 核一遍；
缺 `required=True` 的那一项就报 `SHARED_PRACTICE_INTERFACE_MISMATCH`（附缺口清单），**核不过就不调用**。

| 手机这一层 | A 的方法（真实签名） | 传的关键字 |
|---|---|---|
| `open_practice` | `open_practice(*, title, opening_text, material_refs, operation_key=None, basis=None)` | `title`、`opening_text`、`material_refs=[source_artifact_id]`、`operation_key` |
| `summary` | `list_practices(*, include_ended=True, limit=None)` | `include_ended=True`、`limit=50` |
| `work` | `read_practice(session_id, *, include_history=False, include_material_excerpt=False, entry_limit=12, excerpt_chars=240)` | `session_id`、`include_history=True`、`include_material_excerpt=False`、`entry_limit=60` |
| `submit_user` / `modify` | `submit_user_contribution(session_id, *, text, expected_head, commitment_state='NONE', operation_key=None)` | `session_id`、`text`、`expected_head`、`operation_key` |
| `pause` | `pause_practice(session_id, *, note='', expected_head, basis=None, operation_key=None)` | `session_id`、`note`、`expected_head`、`operation_key` |
| `end` | `end_practice(session_id, *, reason='', expected_head, basis=None, operation_key=None)` | `session_id`、`reason`、`expected_head`、`operation_key` |
| （未使用，可选） | `context_view(session_id, *, include_material_excerpt=False, entry_limit=12, excerpt_chars=240)` | 缺了只是功能降级 |

**手机绝不调用**（A 的 `ACTOR_WRITES` 只给 Life 侧，或需要经核对的来源）：
`submit_subject_response`、`submit_subject_contribution`、`commit_work_text`、`resume_practice`、
`reopen_practice`、`reconfirm_materials`。`PhoneSharedPractice` 上根本没有这些方法（测试断言
`hasattr` 为 False）。

## 3. 契约与三条不许糊弄的地方

- **`resume` / `reopen` 是她那一半**。`PHONE_ACTIONS = ('submit','modify','pause','end')`；
  `POST .../actions` 收到 `resume`/`reopen` → `400 SHARED_PRACTICE_ACTION_UNSUPPORTED`（不转发给 owner），
  界面上没有「重新取用」按钮，只有一句「由她自己决定」。
- **A 没有「原地改用户贡献」这条路**。`modify` = 用户的下一次发言（`edit_kind="new_turn"`、
  `history_kept=true`），旧的那一条留在 A 的不可变历史里；界面上的备注照实这么写。
- **请求 ≠ 排队 ≠ 决定 ≠ 回应**。`queued` 恒为 False（A 没有排队）；`accepted` 只**照抄** A 的结论
  （A 只在存在经核对来源的主体回应时说 true），`accepted_written_by_phone=false`、
  `subject_contribution_written=false`、`starts_life_daemon=false` 每次都出现。
  `submitted` 在失败信封里也出现（=False），缺字段不会被读成「也许落了一半」。
- **原键对账**：A 只在写入答复里给 `operation_key` / `retryable`，**没有**按原键只读对账的面。
  所以 `GET /shared-practice/operations/<key>` 照实回 `503 SHARED_PRACTICE_OPERATION_UNSUPPORTED`
  （`recorded=false`），这一层不自己造账本。
- **GET 纯读**：`GET /shared-practice` 与 `GET .../works/<id>` 不写任何文件（测试用整棵 state root 的
  size+mtime 快照比对）；列表不投影任何正文（`private_text_included=false`）。

## 4. P 自己的 HTTP 面（`/api/phone`，唯一前缀）

| 方法 | 路径 | 请求体 | 说明 |
|---|---|---|---|
| GET | `/shared-practice` | — | 列表 + owner 接口状态；无正文 |
| GET | `/shared-practice/works/<practice_id>` | — | 材料/双方来回/作品版本/权限 |
| GET | `/shared-practice/operations/<key>` | — | 具名 `SHARED_PRACTICE_OPERATION_UNSUPPORTED` |
| POST | `/shared-practice` | `{source_artifact_id, source_version_id, title, text, operation_key}` | 开一次 |
| POST | `/shared-practice/works/<id>/actions` | `{action, expected_head, operation_key, text?, reason?}` | 四种用户意图 |

守卫（全部已测）：
- Host 白名单、Origin 白名单、`Sec-Fetch-Site`、`X-CSRF-Token` 四道闸门；
  缺 CSRF `403 CSRF_TOKEN_MISSING`、外站 Origin `403 ORIGIN_NOT_ALLOWED`、跨站 `403 CROSS_SITE_REFUSED`。
- 客户端身份/渠道/路径字段（`subject`、`contributor`、`accepted`、`state`、`home`、`path`、`url`、
  `channel`、`uid`、`owner`、`workspace_id`…）**按名拒绝 409**，绝不静默丢弃。
- 未知字段 `400 UNKNOWN_FIELD:<name>`；`submit` 带 `reason` 或 `pause` 带 `text` 也是 400。
- `practice_id` 只来自路径，`([A-Za-z0-9._:\-]{1,200})`；超长直接在路由层 404，不进 owner。
- 实例的 `subject` / `home` / `state_root` 只在 `PhoneService` 构造时绑定一次，客户端传的一律拒绝。

## 5. 交付前真实跑过的验证（本机 Windows，py 3.13 / node 22）

```
py -3.13 -X utf8 -m unittest tests.test_v04_shared_practice_phone          → Ran 25 tests, OK
py -3.13 -X utf8 -m unittest tests.test_v04_shared_practice_service       → Ran 44 tests, OK（A 的回归）
py -3.13 -X utf8 -m unittest tests.test_phone_practice_api                → Ran 23 tests, OK
py -3.13 -X utf8 -m unittest tests.test_v04_phone_contact_service         → Ran 36 tests, OK
py -3.13 -X utf8 -m unittest tests.test_v04_phone_contact_http            → Ran 33 tests, OK
py -3.13 -X utf8 -m unittest tests.test_phone_projects_api                → Ran  7 tests, OK
py -3.13 -X utf8 -m unittest tests.test_phone_research_api                → Ran  9 tests, OK
node --test tests/test_v04_shared_practice_phone_ui.mjs                   → 11 pass / 0 fail
node --test tests/test_v04_phone_contact_ui.mjs                           →  9 pass / 0 fail
node --test tests/test_phone_ui.mjs                                       → 34 pass / 0 fail
node --test tests/test_phone_practice_ui.mjs                              → 15 pass / 0 fail
node --test tests/test_phone_research_ui.mjs                              →  9 pass / 0 fail
node --test tests/test_phone_projects_ui.mjs                              →  6 pass / 0 fail
node --test tests/test_phone_reading_ui.mjs                               → 25 pass / 0 fail
node --test tests/test_phone_world_ui.mjs                                 → 25 pass / 0 fail
node --test tests/test_v04_phone_sharing_body_entry.mjs                   →  6 pass / 0 fail
node --check components/phone/web/app.js                                  → 0
```

没有跳过的用例（skip 0）。`tests/test_phone_api.py` 那 13 条本来就 skip（需要外部实例环境），本轮没碰。

## 6. 本轮顺手修掉的半成品破损（都在手机这一半里）

1. **`components/phone/web/app.js` 整份不解析**：`sharedPracticeListView` / `sharedPracticeWorkView`
   被错误地塞进了 `const pureApi = {…}` 对象字面量中间（`SyntaxError: Unexpected identifier`），
   `sharedPracticeOutcomeLines` / `operationView` / 两个 payload builder / 三个路径函数也被劈成两半；
   `validateSharedPracticeDraft` 少一个 `}` 且 `action` 未定义。已按原意图拼回。
2. **`app.js` 缺 `submitSharedPracticeOpen` / `submitSharedPracticeAction`**：按钮上写着 `onClick`，
   函数却不存在（点下去 `ReferenceError`）。已补齐（原键账本 `practice_op:<uuid>`、写完只重读一次）。
3. **`SCREENS` 里没有 `shared-practice` / `shared-practice-work`**，主屏也没有入口 → 两个屏根本进不去。
   已注册并加主屏行内按钮。
4. **`DETAIL_FIELDS` / `go()` / `goBack()` / `restoreNav()` / `persistNav()` 都没有 `sharedPracticeDetailId`**
   → 打开「这一次」变成「没有选中任何一次」、重载丢位置。已补。
5. **`PhoneService.__init__` 里 `self._reading = reading`，但签名里没有 `reading`** → 构造 `PhoneService(...)`
   直接 `NameError`（整个手机面都起不来）。已补关键字参数。
6. **`PhoneService._practice_call` 定义了两次**（共同实践 + B 的个人实践工作台），后者覆盖前者，
   共同实践请求全被送进 B 的服务（回 `PRACTICE_UNAVAILABLE / LifeArtifactError`）。共同实践那个改名为
   `_shared_practice_call`。
7. **`server.py` 里 `_practice_id` 定义了两次**（B 的工作台 / 共同实践），互相覆盖且错误码串了。
   共同实践那个改名为 `_shared_practice_id`。
8. **共同实践开一次时缺 `title` / `text`**：A 的 `open_practice` 要标题与第一轮，手机原来只发材料 id 与版本，
   现在两屏都补齐（选材 → 标题 → 第一轮），并且 `source_version_id` 与 owner 记下的版本不同时
   照实标 `source_version_moved=true`，不假装没发生。

## 7. 限制与没做的事（照实写）

- **A 没有按原键只读对账面**：`operations/<key>` 只能回「不支持」，UI 显示「未知」而不是伪造结论。
- **A 不改写已记录的贡献**：`modify` 是新的一轮；要真正原地改写需要 A 先发布用户侧编辑动作（本轮不碰 A）。
- **版本历史没有正文**：只显示编号/时间/字数（A 的 `read_practice(include_history=True)` 就这样），
  正文只在「共同作品」一处。
- **没有真实 Life 进程参与本轮验证**：测试里的主体回应由 Life 角色 + `FakeSourceVerifier` 写入（A 自己的
  契约替身），本轮**没有**跑真实模型、没有连原实例、没有起 Life daemon；`starts_life_daemon=false` 是代码事实。
- **列表的「你 / 她」计数**：A 的 `list_practices` 只给总数与回应数，没有按人拆分；列表就写「来回 N 条 +
  她回应 M 次」，详情里才给精确的双方计数（不编数字）。
- 没有 commit、没有 deploy、没有 reset/clean；所有 dirty 原样保留，夹具根建在会话 scratch 下且不删。

## 8. 轮二（必做）：正式装配 + 用户输入走传输

**界面与 HTTP 请求形状没变**；变的是「谁写、写给谁」。第 7 节里「A 没有按原键对账面」那一条
已被第 8.2 节取代（现在走 B 的 inbox 对账）。

### 8.1 装配（`serve` 不再 UNBOUND）

`components/phone/service.py::_shared_practice_owner_from_instance`：

```python
sps.shared_practice_service_from_env(port=<实例端口>, env=<实例绑定>, actor=sps.CALLER_USER)
# env 只用 private_project_service 已发布的名字，值全部来自实例 payload：
#   ENV_WORKSPACE_NAMES[0]=instance["workspace_id"]   ENV_GRANT_NAMES[0]=instance["artifact_grant_id"]
#   ENV_SUBJECT_NAMES[0]=instance["subject"]          ENV_SOCKET_NAMES[0]=instance["supply_socket"]
#   ENV_EXCLUDED_NAMES[0]=default_excluded_artifact_ids(reading, private_items)
```

`PhoneService(instance, project_port=None)`：`project_port` 是**同一位 owner 已打开的端口对象**
（POSIX 真 Supply / 父级 / 测试），不是替身服务；A 的 `SharedPracticeService` 永远由 A 的 factory 建。
半接好的实例 → `SHARED_PRACTICE_UNBOUND` + `SHARED_PRACTICE_BINDINGS_MISSING`。`demo.py` 无需改动。

### 8.2 写语义（用户那一半进她的来信箱）

| 手机动作 | 现在做什么 | 不再做什么 |
|---|---|---|
| 开一次 | `CO_PRESENCE_INVITATION`（`scope`=标题、`text`=第一轮、`material_refs`=[材料]） | 不调 A 的 `open_practice`（作品由正常 Life 在 ACCEPT 后开） |
| 提交 / 修改 | `LOCAL_MESSAGE`（`text` + 原键） | 不写 A 的用户贡献（Life 消费事件时用 user 角色写） |
| 结束 | `CO_PRESENCE_END` + `explicit_end=true` + `invitation_id`（从 inbox 里那条邀请的 `result.practice_session_ref` 反查，最多 8 条） | 不直接 `end_practice`（否则 joint 还占前台） |
| 暂停 | A 的 `pause_practice`（user 侧状态动作） | 不动她的 Activity |
| 原键对账 | `GET .../operations/<key>` → `LocalContactInbox.operation`（`absent`/`applied`/`unknown`） | 不再回「不支持」，不自造账本 |

每次答复都带：`queued`（=PENDING）、`submitted`（只表示传输记下了）、`accepted=false`、
`accepted_written_by_phone=false`、`subject_contribution_written=false`、
`user_contribution_written=false`、`phone_wrote_practice=false`、`starts_life_daemon=false`。
邀请找不到时 `end` 回 `409 SHARED_PRACTICE_INVITATION_UNKNOWN`（不写事件、不改作品）。

### 8.3 给父级的可运行 fixture

```python
from components.phone import fixture
h = fixture.start_fixture(project_port=port)   # port 可省：省了按实例 supply socket 建
h["url"]; h["csrf_token"]; h["root"]; h["service"]; h["instance"]
h["close"]()                                   # 关 HTTP + 线程；根与账本保留
```

根默认在 `$PI_SCRATCH_DIR/nyairo-phone-fixture/run-<pid>-<ns>`（或 `NYAIRO_PHONE_FIXTURE_ROOT`），**不删**。

### 8.4 本轮实际 exit / skips

```
py -3.13 -X utf8 -m unittest tests.test_v04_shared_practice_phone            → Ran 16 tests, OK
py -3.13 -X utf8 -m unittest tests.test_v04_shared_practice_phone_transport  → Ran 12 tests, OK
py -3.13 -X utf8 -m unittest tests.test_v04_shared_practice_service          → Ran 59 tests, OK（A 回归）
py -3.13 -X utf8 -m unittest tests.test_v04_phone_contact_service            → Ran 36 tests, OK
py -3.13 -X utf8 -m unittest tests.test_v04_phone_contact_http               → Ran 33 tests, OK
py -3.13 -X utf8 -m unittest tests.test_phone_practice_api                   → Ran 23 tests, OK
py -3.13 -X utf8 -m unittest tests.test_phone_projects_api                   → Ran  7 tests, OK
node --test tests/test_v04_shared_practice_phone_ui.mjs                      → 11 pass / 0 fail
node --test tests/test_v04_phone_contact_ui.mjs                              →  9 pass / 0 fail
node --test tests/test_phone_practice_ui.mjs                                 → 15 pass / 0 fail
node --test tests/test_phone_ui.mjs                                          → 34 pass / 0 fail
node --check components/phone/web/app.js                                     → 0
```

skip 0。新的 12 条：装配 2、传输 5、HTTP 3、fixture 1、守卫 1。

### 8.5 这一轮**没有**证明的事（别当整链完成）

* 没有跑**正常 Life runner**：三轮输入真的被她处理、她真的写回应、作品版本真的推进——那要在父级的
  真装配里看；这里只证明「事件进了来信箱、A 没有被手机写」。
* 没有真 POSIX Supply / 真模型：`project_port` 用的是 A 契约用例同一份内存 owner 端口（端口，不是替身
  服务）；真实模型 0 次调用。
* END 的完整收口（joint COMPLETED + LR-4 资格 + `end_practice`）由 B 在处理 END 事件时完成；这里只证明
  手机发得出正确的 END 事件、发不出时具名拒绝。

## 9. 父级最终集成验证（2026-10-21，覆盖 §8 的未验证部分）

父级真实链发现并修复两个未被内存端口测试发现的连接缺口：

- `_shared_practice_owner_from_instance` 原来排除了 reading artifact，来源重读被
  `PROJECT_ARTIFACT_EXCLUDED` 拒绝，治理逻辑因此将全部回应扣空。共同实践 factory 现在只排除
  private-items 台账，明确选定的阅读 source 仍由 owner 每次核验；不取消归档/撤权/换版治理。
- END 原来查邀请结果里的 `practice_session_ref`，但结果早于开作品、根本没有这个字段。
  现在纯读协议当前 `practice_session_ref/practice_invitation_event_ref/invitation_id`，再向 inbox
  核验对应原邀请确实 ACCEPTED；缺失/另一作品/未接纳均拒绝，手机仍不结束 Activity 或直接改作品。
  回归不再 override `_invitation_for`，而是实际写入协议绑定后重读并验证零写。

父级脚本 `shared_practice_parent_smoke.py`：真 Supply/World/正常 runner + 正式 PhoneService factory，
11 checks / exit0，实际三轮回应、END、原 Activity page2、重启同内容无重放；root
`/tmp/day-life-parent-j-184578`。Edge 从主屏实际点击 12 checks / exit0，作品版本 1→5→9→13，
随后 ENDED，原 Activity page2；重载归属/正文保留、storage 无业务正文、390px 无溢出、0页面异常。
证据 `shared-practice-browser-1792542498657.json` / 同名 PNG，夹具
`/tmp/day-life-parent-u-115454`；server关闭回执 resources_closed=true，PID979不存在。

这两条链的模型选择/内容为严格原始 JSON 替身，不是自然模型行为。早期父级 root/home适配及按钮等待
错误的失败证据保留；同一浏览器夹具邀请与第一轮没有重复投递，后续按 owner 回应数续接。

最终 Windows contact+phone 67 tests / exit0（phone两模块29）；WSL life seam+真Supply治理43 / exit0；
Node UI11 / exit0；79notes tree/format与diff-check均0。中途一次安全守卫请求 WinError10053保留，
同项定点重测0、最终67模块组合重测0。来源归档/换版/派生文本扣留与纯读对账仍通过。

独立真实模型仅1 attempt，HTTP200/stop但AG1 INVALID_SCHEMA、没有committed Decision，未进入内容链；
不是自然拒绝/接纳，预算已永久关闭。详见[父级结果](v04-shared-practice-parent-result.md)。
原客户端 `source_version_id` 仍只表达选材时所见版本，邀请携带的是artifact引用，接受时重新读当前版本；
不声称跨排队时间锁定了客户端所见版本。正式LifeArtifactPort历史仍head-only，未虚构逐版全文。
