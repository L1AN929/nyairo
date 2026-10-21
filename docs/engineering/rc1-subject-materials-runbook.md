# rc1 主体私人材料与生活续接 —— 手机实例上的 Life 绑定运行手册（P 面）

本轮把"她自己用自己材料"的**无界面**入口接到手机上：手机实例（`components/phone/demo.py` 的
`instance.json`）是唯一的 live 绑定来源，Life runtime 用**现有**装配/常驻类跑，不新建 store、不新开
额度窗口、不起第二个 Supply/World，也不需要用户打开网页。

- 代码：`components/phone/life.py`（新）、`scripts/phone.py`（两个 verb）
- 契约/结果：`docs/engineering/rc1-subject-materials-binding-result.md`
- 核心侧（C 的处境/阅读循环）见 `docs/engineering/rc1-subject-materials-contract.md`

---

## 0. 一句话分工

| 面 | 谁在做 | 这次有没有变 |
|---|---|---|
| 手机 UI / HTTP（`components/phone/web/**`、`service.py`、`server.py`） | U/P 之前那轮 | **没变**。她的手机她自己有，这轮不加网页功能 |
| 手机 CLI（`scripts/phone.py`） | P（本文件） | 只**新增** `life-preflight` / `life-run`，旧四个 verb 一行没动 |
| Life runtime / cycle / resident | C | **没变**（本层只调用，不修改） |
| `one_day_life.py` CLI parser | P | 只修三个确切缺陷（见 §5），业务一行没动 |

---

## 1. 为什么不能直接用 `one-day/instance.json` 起主体

手机和 Life 是**两份文件、字段同源、不共写**：

- `demo.serve_instance()` 每一代 `serve` 都会把**新的** `supply_socket` / `supply_generation` 写回
  手机实例（`demo.py:751-757`）；
- `one_day_life.prepare()` 写进 Life 实例的 `environment` 的那份 socket 还是**做准备那一代**；
- 所以主体如果照 Life 清单起，就会连到一个已经没人监听的旧 socket（"陈旧 socket"）。

结论：**显式用手机实例的 live 绑定**，并且**只在内存里覆盖**，绝不回写任何 `instance.json`
（移动/替换旧清单会破坏既有根，也违反"不覆盖 nested 业务清单"）。

---

## 2. 绑定映射（phone live → Life 环境名）

`life.build_binding(phone, nested)` 的顺序就是设计本身：

1. 先取 `os.environ`，整段剥掉 `one_day_life.OWNED_ENV_NAMES` **和所有 `CHIYO_AUTONOMOUS_*`**
   （后者是为了让 `CHIYO_AUTONOMOUS_NEW_RUN_WINDOW` 不可能从操作者的 shell 漏进来）；
2. 拷入 nested Life 实例自己记录的 `environment`（**只读**，空值当"没绑定"，不冒充来源）；
3. 手机实例的 live 值覆盖同名项；每个名字的 `sources` 都记在报告里
   （`phone_live` / `phone_features` / `nested_life` / `nested_model_env` / `life_root` / `absent`）。

| 手机实例字段 | Life 环境名 |
|---|---|
| `supply_socket` | `LIFE_SUPPLY_SOCKET`、`CHIYO_NATIVE_LIFE_SUPPLY_SOCKET` |
| `subject` | `NYA02_SUBJECT`、`CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT` |
| `artifact_grant_id` | `NYA02_ARTIFACT_GRANT`、`CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT` |
| `workspace_id` | `CHIYO_LIFE_SUPPLY_WORKSPACE_ID` |
| `work_artifact_id` | `CHIYO_LIFE_WORK_ARTIFACT_ID` |
| `reading_artifact_id`（可为空） | `CHIYO_LIFE_READING_ARTIFACT_ID`（空 = **不绑**，不拿别的材料顶） |
| `private_items_artifact_id` | `CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID` |
| `world_home` | `CHIYO_WORLD_HOME`、`HERMES_HOME` |
| `world_id` / `world_package` | `CHIYO_WORLD_ID` / `CHIYO_WORLD_PACKAGE` |
| `world_body_socket` | `WORLD_BODY_SOCKET` + `WORLD_BODY_INTEGRATION_MODE=canonical` |
| `features.*` | 同名 `CHIYO_WORLD_*`（`CHIYO_WORLD_DAILY_RHYTHM_ENABLED` 等） |

**沿用、不重算**（来自 nested Life 清单）：

| Life 环境名 | 含义 |
|---|---|
| `CHIYO_AUTONOMOUS_RUN_ID` | 同一个 run 窗口身份；重启不换窗口 |
| `CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY` / `..._PER_RUN_WINDOW` | 硬请求上限 |
| `CHIYO_LIFE_INSTALL_ROOT` | `one-day/life` |
| `NYA02_RESIDENT_STATE` | `one-day/life/state`（**现有**的真实 Life 根） |

其它：`<root>/one-day/model_env`（`NYA02_MODEL_*`，0600，只在内存里读，已存在的变量优先）与
nested `environment` 里的 `NYA02_MEMORY_*` 原样带过；没有就**没有**（continuity 报
`PAST_SOURCE_NOT_WIRED`，不编一段历史）。

---

## 3. `life-preflight`：纯读、零写、零模型、不连 socket

```bash
python scripts/phone.py life-preflight --instance /abs/phone-root
# 等价：python -m components.phone life-preflight --instance /abs/phone-root
```

读什么（只有读）：

| 检查 | 读什么 | 能不能"假装通过" |
|---|---|---|
| `nested_life` | `<root>/one-day/instance.json` 存在且合法 | 不存在 → `UNAVAILABLE` + `unknown: nested_life_instance` |
| `supply` | `lstat` 手机 live socket：在不在、是不是 socket、代次与 `run/phone.serve.json` 一不一致 | 不连、不探活；缺失 → `UNAVAILABLE`，不是 socket / 代次不符 → `BLOCKED` |
| `world` | `world_body_socket` 的 `lstat` + bridge 目录在不在 | 不连 |
| `permissions` | 手机根 / Life state root 的 mode 与 owner、`local_owner_uid` vs 当前 euid | uid 不符 → `BLOCKED`；其它只报 |
| `lease` | `<state>/run/life_activity_authority.lock` 的**只读** `flock` + marker | 没有 `fcntl`（Windows）→ `UNKNOWN`，**不报 FREE** |
| `switch` | 现有 `autonomous_switch.json` / 当日预算 | 没开 → `BLOCKED`（本命令不会去打开） |
| `model` | 配置（`autonomous_life_assembly._model_config` 同一套解析） | 只报 `configured`，`request_sent: false`，永不打印 key |
| `privacy` | profile `chiyo/config.json` 的 `phone.private_artifacts` permit | 只读；`private_content_read: false`（从不读笔记正文） |

报告里有三件必须分开读的事：

- `assembly.feasible` —— 现有 runtime **能不能在这个绑定上装起来**；
- `assembly.*`（`model_configured` / `switch_on` / `single_writer_lease` / `supply_socket_present` /
  `request_ceiling_set`）与 `run_ready` —— **现在能不能真跑一步**；
- `model_called: false` + `verified_claims.verified_model_response: false` —— **没有调用过模型**。

退出码：`0` 可装配 · `1` 有 BLOCKED（未就绪） · `2` 用法错。

---

## 4. `life-run`：只复用现有 resident，默认有限

```bash
# 一步（start → tick → stop），最常用
python scripts/phone.py life-run --instance /abs/phone-root --once

# 有界多 tick / 有界时长（默认 1800 秒，永远是有限的）
python scripts/phone.py life-run --instance /abs/phone-root --max-ticks 3
python scripts/phone.py life-run --instance /abs/phone-root --duration-seconds 600 --poll-seconds 60

# 只考虑不执行
python scripts/phone.py life-run --instance /abs/phone-root --once --no-act
```

它做的事：用 §2 的环境构造**现有的** `autonomous_life_resident.AutonomousLifeResident`
（同一个类、同一个 `state_root`、同一把 lease），`start()` → `tick()`/`run()` → 一定 `stop()`
（`stop()` 会释放 lease）。退出码与 `one_day_life` 同一套：

| 退出码 | 含义 | 典型原因码 |
|---|---|---|
| `0` | 跑完并停了自己 | — |
| `1` | 拒绝（清单/身份问题） | `INSTANCE_*`、`NESTED_LIFE_INSTANCE_INVALID`、`LOCAL_OWNER_UID_MISMATCH` |
| `2` | 用法错（argparse） | 未知参数 |
| `3` | 诚实 BLOCKED（什么也没启动） | `NO_MODEL_PROVIDER`、`AUTONOMOUS_SWITCH_OFF`、`REQUEST_CEILING_UNSET`、`SUPPLY_OWNER_UNREACHABLE`、`SUPPLY_BINDING_STALE`、`CROSS_BINDING_CONFLICT`、`RUN_FAILED` |
| `4` | 同根已有 writer | `LEASE_HELD`、resident 自己报 lock/lease |

**它不会做的**（写死在实现里，CLI 也没有对应开关）：

- 不打开长期自主开关（`autonomous_switch.json` 只读；关了就是 `AUTONOMOUS_SWITCH_OFF`）；
- 不新开请求窗口（`CHIYO_AUTONOMOUS_NEW_RUN_WINDOW` 会被主动剥掉；没有 `--new-run-window`）；
- 不起 Supply / World 子进程、不建第二个 socket（需要手机 `serve` 已经在跑；socket 不通就
  `SUPPLY_OWNER_UNREACHABLE`，而不是自己去起一个）；
- 不塞 fake 生产替身：没有真实 provider 配置就 BLOCKED，不装 deterministic writer / stub cognition；
- 不打印/落盘任何凭据（`describe_model_config` 只暴露 `api_key_present`）。

**跑之前请确认**（否则会按上表 BLOCKED，这是设计）：

1. 手机 `serve` 在跑（live socket 在、代次一致）；
2. 该实例的 `one-day/life/state/autonomous_switch.json` 是 `enabled: true`（由既有的
   `one_day_life prepare`/操作者动作写入，本层不改）；
3. nested Life 清单里有显式请求上限；
4. profile 里有可用的 `NYA02_MODEL_*`（或 `config.yaml`）—— 但"配置齐全"≠"模型能用"。

---

## 5. 顺带修掉的三处 `one_day_life` CLI 缺陷（先复现、再窄修）

| # | 现象（复现方式） | 原因 | 修法 |
|---|---|---|---|
| 1 | `python components/life/scripts/one_day_life.py --help` 直接 `argparse.ArgumentError: conflicting option string: --create-private-items`，**任何** verb 都起不来 | `--create-private-items` 被声明两次（原 1474-1475 与 1510-1511） | 只留第一处；第二处留一行注释 |
| 2 | `prepare` 分支用了 `args.supply_socket` 但 parser 从没声明 → `AttributeError` 被兜底吞成 `FAILED/3` | 缺 `--supply-socket` | 补声明（`--governance-socket` 旁） |
| 3 | `read --page N` 用了 `args.unclear`，但 parser 没有 `--unclear`（`life-v1-runbook.md` 却写了这个参数） | 缺 `--unclear` | 补声明（`--page` 旁），语义仍是 `read_page(unclear=...)` 的"没看懂"标记 |

只动 `main()` 的 argparse；`read_page` / `prepare` 的语义与实现一行没动。

---

## 6. 三层结论（写验收时照抄，不要升格）

1. **机制已验证**：映射、preflight 的零写/零模型、六种诚实 BLOCKED、CLI 契约、parser 三处修复，
   都在 `tests/test_phone_subject_life.py` 里（Windows 32 例通过；WSL/Linux 再跑含 POSIX 用例的
   同一份）。见 `rc1-subject-materials-binding-result.md` 的"验证"节。
2. **真实模型未调用**：本层任何路径都不发模型请求；`model_called/request_sent` 恒 false。
3. **尚未启用常驻**：没有后台循环、没有调度器；`life-run` 是有界的、跑完就停自己的 resident。
   "她自己过了一天"这句话本轮**不成立**。

---

## 7. 回滚

本层是**新增**代码（`components/phone/life.py`、两个 verb、三个 parser 声明），回滚只需要：

```bash
git checkout -- scripts/phone.py components/life/scripts/one_day_life.py
rm components/phone/life.py            # 仅在你确实要撤掉时；正常流程不删
```

它不写任何实例数据，所以**没有数据侧回滚**：手机实例与 Life state root 与执行前逐字节一致
（`tests/test_phone_subject_life.py` 用 mode/size/mtime 快照钉住这一点）。
