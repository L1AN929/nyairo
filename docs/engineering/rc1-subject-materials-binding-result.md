# rc1 主体私人材料与生活续接 —— P 面交付结果（手机实例 → 现有 Life runtime 的无界面绑定）

> 表述纪律：本层只证明**绑定/预检/诚实拒绝/CLI 契约**。它**没有调用过真实模型**，也**没有让主体常驻**。
> 任何"她自己读了一天"的说法本轮不成立。

- 仓库：`D:\nyairo-rc1-body-backend`（分支 `work/rc1-phone-world-body`，HEAD `41e818f`，dirty 成果保留）
- 新增：`components/phone/life.py`、`tests/test_phone_subject_life.py`、本文、`rc1-subject-materials-runbook.md`
- 修改：`scripts/phone.py`（只加两个 verb + usage 行）、`components/life/scripts/one_day_life.py`（只加/删三处 argparse 声明）
- 未触碰：C 的任何文件、`components/phone/{service,server,demo}.py`、`web/**`、`components/supply/**`、`components/world/**`、`port`/`Body`/`World`/`demo`/`tool`

---

## 1. 公开函数（精确签名）

模块：`components.phone.life`

```python
class PhoneLifeError(RuntimeError)

# 常量
SCHEMA_PREFLIGHT = "phone.life.preflight.v1";  SCHEMA_RUN = "phone.life.run.v1"
EXIT_OK = 0; EXIT_REFUSED = 1; EXIT_USAGE = 2; EXIT_BLOCKED = 3; EXIT_LEASE_HELD = 4
DEFAULT_RUN_DURATION_SECONDS = 1800
STATE_ROOT_MODE = 0o700
PHONE_LIVE_FIELDS: dict[str, tuple[str, ...]]     # phone 字段 -> Life 环境名
OWNED_PREFIXES = ("CHIYO_AUTONOMOUS_",)

# 实例事实（只读；从不回写）
phone_root(root: Path | str) -> Path
load_phone_instance(root: Path | str) -> dict[str, Any]                    # 抛 PhoneLifeError
nested_life_dir(root: Path | str) -> Path                                  # <root>/one-day
read_nested_life(root: Path | str) -> Optional[dict[str, Any]]             # 缺失 -> None；非法 -> 抛
life_install_root(root: Path | str, nested: Mapping | None = None) -> Path  # <root>/one-day/life
life_state_root(root: Path | str, nested: Mapping | None = None) -> Path    # …/life/state
serve_marker(root: Path | str) -> Optional[dict[str, Any]]                 # run/phone.serve.json

# 映射（provenance 全在返回值里）
build_binding(phone: Mapping[str, Any], nested: Optional[Mapping[str, Any]], *,
              base_env: Optional[Mapping[str, str]] = None) -> dict[str, Any]
    # -> {"environment": dict[str,str], "sources": dict[str,str], "overridden": list[str],
    #     "conflicts": list[str], "missing": list[str], "unknown": list[str]}
map_life_environment(phone, nested, *, base_env=None) -> dict[str, str]

# 单项检查（都只读、都不连接）
check_model(environment: Mapping[str, str]) -> dict[str, Any]
check_supply_binding(phone: Mapping[str, Any], *, environment: Optional[Mapping[str, str]] = None) -> dict
check_world_binding(phone: Mapping[str, Any]) -> dict[str, Any]
check_permissions(root: Path | str, phone: Mapping[str, Any]) -> dict[str, Any]
check_single_lease(state_root: Path | str, *, probe: Optional[Callable[..., Any]] = None) -> dict
check_switch(root: Path | str, nested: Optional[Mapping[str, Any]] = None) -> dict[str, Any]
check_privacy(environment: Mapping[str, str]) -> dict[str, Any]

# 两个入口
life_preflight(root: Path | str, *, environment: Optional[Mapping[str, str]] = None) -> dict[str, Any]
life_run(root: Path | str, *, once: bool = False, max_ticks: Optional[int] = None,
         duration_seconds: int = 1800, poll_seconds: Optional[int] = None, act: bool = True,
         environment: Optional[Mapping[str, str]] = None,
         resident_factory: Optional[Callable[..., Any]] = None,
         verify_owners: bool = True) -> dict[str, Any]
```

`resident_factory` / `verify_owners` / `environment` 是**只在 Python API 上的测试注入口**；
`scripts/phone.py` 两个 verb 一个都不传（`test_cli_never_passes_an_injected_factory` 钉住），
所以不存在"CLI 任意 payload 选择执行"的路。

### 为什么没有 cognition / writer 注入
`AutonomousLifeResident.__init__`（`autonomous_life_resident.py:214-216`）只接受
`state_root / environment / poll_seconds / act / clock / sleeper`；它内部自己调
`assembly.model_cognition(...)`（`:411-412`），没有 cognition/writer 的 DI 缝。按施工单要求，
**只报告、不擅改 C 的文件**。因此测试注入的是"resident 形状的替身"（`resident_factory`），
验证的是**绑定喂对了什么**，不是"用假模型跑通自治"。

---

## 2. CLI（`scripts/phone.py`，`python -m components.phone` 等价）

```text
usage: phone.py {prepare-demo,serve,owner-supply,owner-world,life-preflight,life-run} ...

phone life-preflight --instance ROOT
    → 纯读报告（JSON 一行），退出码 0=可装配 / 1=有 BLOCKED / 2=用法错
    → 内部异常：{"ok": false, "reason_code": "LIFE_PREFLIGHT_FAILED", "error_type": ...} + 1

phone life-run --instance ROOT [--once] [--max-ticks N] [--duration-seconds N]
                              [--poll-seconds N] [--no-act]
    → 0 跑完并停了自己 / 1 清单或身份拒绝 / 2 用法错 /
      3 诚实 BLOCKED（未启动任何东西）/ 4 同根已有 writer
```

**没有**的开关（argparse 直接拒绝，`test_cli_exposes_no_injection_or_window_flags`）：
`--new-run-window`、`--enable-autonomy`、`--resident-factory`、`--cognition`、`--writer`、`--force`。

`life-run` 的默认是**有限**的：不给 `--once` 也不给 `--max-ticks` 时，`--duration-seconds`
默认 1800 秒（`DEFAULT_RUN_DURATION_SECONDS`）。常驻必须由调用者显式给出更大的界，本层不提供无限模式。

---

## 3. 环境映射（phone live → Life）

顺序：`os.environ` → 剥掉 `one_day_life.OWNED_ENV_NAMES` 与所有 `CHIYO_AUTONOMOUS_*` →
nested `environment`（只读，空值跳过）→ 手机 live 覆盖 → 手机 `features` → Life root 默认 →
`<root>/one-day/model_env`（只补缺）→ **强制** `pop(CHIYO_AUTONOMOUS_NEW_RUN_WINDOW)`。

| 手机字段 | Life 环境名 | 期望来源 |
|---|---|---|
| `supply_socket` | `LIFE_SUPPLY_SOCKET`、`CHIYO_NATIVE_LIFE_SUPPLY_SOCKET` | `phone_live`（会出现在 `overridden`） |
| `subject` | `NYA02_SUBJECT`、`CHIYO_NATIVE_LIFE_SUPPLY_SUBJECT` | `phone_live` |
| `artifact_grant_id` | `NYA02_ARTIFACT_GRANT`、`CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT` | `phone_live` |
| `workspace_id` | `CHIYO_LIFE_SUPPLY_WORKSPACE_ID` | `phone_live` |
| `work_artifact_id` | `CHIYO_LIFE_WORK_ARTIFACT_ID` | `phone_live` |
| `reading_artifact_id`（空则缺省） | `CHIYO_LIFE_READING_ARTIFACT_ID` | `phone_live` / `absent` |
| `private_items_artifact_id` | `CHIYO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID` | `phone_live` |
| `world_home` | `CHIYO_WORLD_HOME`、`HERMES_HOME` | `phone_live` |
| `world_id` / `world_package` | `CHIYO_WORLD_ID` / `CHIYO_WORLD_PACKAGE` | `phone_live` |
| `world_body_socket` | `WORLD_BODY_SOCKET`、`WORLD_BODY_INTEGRATION_MODE=canonical` | `phone_live` |
| `features` | 同名 `CHIYO_WORLD_*` | `phone_features` |
| — | `CHIYO_AUTONOMOUS_RUN_ID`、`..._PER_UTC_DAY`、`..._PER_RUN_WINDOW` | `nested_life`（**沿用**） |
| — | `CHIYO_LIFE_INSTALL_ROOT`、`NYA02_RESIDENT_STATE` | `life_root` |
| — | `NYA02_MODEL_*` | `nested_model_env`（只补缺） |

`build_binding()["conflicts"]` 只对**业务身份**（grant / workspace / subject）报交叉绑定冲突；
socket 代次不同是**预期**，记在 `overridden`，不当冲突。`life_run` 遇 `conflicts` 直接
`CROSS_BINDING_CONFLICT`（exit 3），不合并。

---

## 4. 预检语义（每条都可在报告里核对）

`life_preflight` 返回：`schema / instance / read_only: true / model_called: false /
socket_created: false / processes_started: 0 / subject / binding{...} / bindings{...} /
checks{nested_life,supply,world,permissions,lease,switch,model,privacy} / blocked[] /
assembly{feasible,model_configured,switch_on,single_writer_lease,supply_socket_present,
request_ceiling_set,detail} / run_ready / verified_claims{read_only,not_performed,
verified_model_response:false} / ok / status / exit_code`。

| 检查 | 状态语汇 | 关键字段 |
|---|---|---|
| `nested_life` | `READY` / `UNAVAILABLE` / `BLOCKED` | `life_state_root`、`life_install_root`、`detail=NESTED_LIFE_INSTANCE_MISSING` |
| `supply` | `READY` / `UNAVAILABLE` / `BLOCKED` | `present`、`is_socket`、`mode`、`owner_uid`、`stale`、`serve_marker_generation`、`environment_binding_matches`、`probe="not_performed_read_only"` |
| `world` | `READY` / `UNKNOWN` / `BLOCKED` | `present`、`bridge_dir_exists` |
| `permissions` | `READY` / `UNKNOWN` / `BLOCKED` | `uid_matches`、`phone_root_mode`、`state_root_mode`(如 `"0700"`)、`state_root_secure` |
| `lease` | `FREE` / `HELD` / `UNKNOWN` | `held_by_another_process`、`marker{instance_id,pid,acquired_at}`、`reason=LEASE_PROBE_UNAVAILABLE` |
| `switch` | `READY` / `BLOCKED` / `UNKNOWN` | `recorded`、`autonomous_switch`、`budget_today` |
| `model` | `READY` / `BLOCKED` | `configured`、`request_sent: false`、`probe: "not_performed"`、`description`（无 `api_key`） |
| `privacy` | `READY` / `UNKNOWN` | `private_artifacts_permit`、`private_content_read: false` |

`blocked[]` 只收 `status == BLOCKED` 的检查加 `binding.missing`。**缺失的 socket 不阻塞"可装配"**
（装配只需要三个配置字符串），它由 `assembly.supply_socket_present` 与 `run_ready: false` 表达。

### 为什么没用 `one_day_life.preflight`
它做三件本层明令禁止的事：① `_preflight_supply()` 真的 `connect` 两次并 `get_grant`
（`:671-692`）；② `_preflight_model()` 做一次 TCP reachability 探针（`:715`）；③ `_probe_writer()`
用 `ActivityAuthorityLease.acquire()/release()` 探测，而 `acquire()` 会 `mkdir run/`、创建锁文件、
写并删除 `life_authority.json`（`activity_continuity.py:644-693`）——**不是零写**。本层因此自己实现
只读探针（打开既有锁文件做 `flock(LOCK_EX|LOCK_NB)`，无文件产生），并在没有 `fcntl` 时诚实报
`UNKNOWN` 而不是 `FREE`。

---

## 5. `life_run` 的 gate 顺序（每一步都在启动前）

1. 手机 `instance.json` 可读（`phone_service.load_instance`，含 schema/kind/必需字段/root 一致）；
2. nested `one-day/instance.json` 存在且合法；
3. `build_binding` 无 `conflicts`；
4. `check_permissions.uid_matches is not False`（POSIX；Windows 上为 `None` → 不拦）；
5. `check_supply_binding.status != BLOCKED`（不是 socket / 代次陈旧 → 拒绝）；
6. `check_model.configured` 为真（否则 `NO_MODEL_PROVIDER`，**绝不**塞 fake writer/cognition）；
7. `check_switch.recorded is True`（否则 `AUTONOMOUS_SWITCH_OFF`；本层永不写开关）；
8. 显式请求上限存在（否则 `REQUEST_CEILING_UNSET`）；
9. `check_single_lease.status != "HELD"`（`HELD` → `LEASE_HELD` / exit 4）；
10. `verify_owners=True`（CLI 默认）时对手机 live socket 做一次 `health` 读（只读 op）——
    不通即 `SUPPLY_OWNER_UNREACHABLE`，**不自己起服务**；
11. 构造 `resident_factory(...)`（默认 `AutonomousLifeResident`）→ `start()` → `tick()`/`run()` →
    `finally: stop()`。`start()` 的拒绝按 detail 里的 lock/lease/held 映射到 exit 4，否则 exit 3。

报告里的 `model_calls` / `model_called` 取 resident 自己的 `counters["model_calls"]`；
本层自己从不发起请求。

---

## 6. 验证（真实命令与真实结果）

环境：Windows 11 + Python 3.13（`os.name == "nt"`）；WSL Ubuntu + Python 3.12.3（POSIX）。

```text
# Windows（POSIX-only 用例自动 skip）
$env:PYTHONDONTWRITEBYTECODE="1"; $env:NYAIRO_RC1_LIFE_ROOT=$env:PI_SCRATCH_DIR
python -m unittest tests.test_phone_subject_life
→ Ran 32 tests … OK (skipped=5)   exit 0

# WSL/Linux（含 lease/uid/AF_UNIX 用例）
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 NYAIRO_RC1_LIFE_ROOT=/tmp/nyairo-rc1-life-fixtures \
  python3 -m unittest tests.test_phone_subject_life
→ Ran 32 tests … OK              exit 0

# 两处 help 活着
python components/life/scripts/one_day_life.py --help      → exit 0
python scripts/phone.py --help                              → usage 含 life-preflight,life-run，exit 0
```

5 个 Windows 上被 skip 的用例（都在 WSL 上通过）：
`test_run_refuses_a_foreign_uid`、`test_run_fails_closed_on_a_second_writer_for_the_same_root`、
`test_the_lease_probe_leaves_no_files_behind`、`test_preflight_never_connects_to_the_supply_socket`、
`test_preflight_reports_a_non_socket_path_loudly`（整个 `NoProbeTests` 类因 AF_UNIX 跳过，其中第二个
用例实际不需要 AF_UNIX，但归在同类里）。

覆盖到的断言（32 例）：live socket 覆盖旧代次并出现在 `overridden`；run_id/预算/install root 沿用；
shell 里塞 `CHIYO_AUTONOMOUS_NEW_RUN_WINDOW=1` 与伪 run_id 都会被剥掉/还原；preflight 前后整棵树
mode/size/mtime 完全一致（含 state root 与 `run/`）；model 行恒 `request_sent:false` 且 description
无 `api_key`；reading 绑定缺失只报 `absent` 不顶替；陈旧 socket 代次 → `SUPPLY_BINDING_STALE`；
nested 缺失 → `UNAVAILABLE`/`NESTED_LIFE_INSTANCE_MISSING`；cross-binding → `CROSS_BINDING_CONFLICT`；
无 provider / 开关关 / 无预算 / supply 不通 → 四条诚实 BLOCKED 且 `resident_factory` 一次都没被调用；
开关关时 `autonomous_switch.json` 字节不变；注入 factory 收到精确的 `state_root`（=`<root>/one-day/life/state`）、
映射后的 `environment`、`poll_seconds`、`act`；`once` 只 tick 一次并一定 `stop()`；第二 writer 同根
→ exit 4；CLI 转发与拒绝开关；`one_day_life` 三个 parser 缺陷的正向断言。

> 没有跑旧全库、没有跑 24 小时、没有起真实 provider。C 侧的 cycle/source/PDI 用例属于 C 的交付面。

---

## 7. 限制与偏差（照实写）

1. **没有 cognition/writer 注入**：C 的 `AutonomousLifeResident` 无该 DI 缝，按要求不擅改；
   因此"脚本化决策驱动真实 owner 读页"只能在 C 侧或父级验收里做，本层不冒充。
2. **`life_run` 没有在真实 provider 上跑过**：本轮不接/不改当前实例、不起真实 provider；
   `NO_MODEL_PROVIDER` 与其余五道 gate 的**拒绝路径**都被验证，**放行后的真实请求**没有被触发。
3. **lease 探针在 Windows 上只能是 `UNKNOWN`**（无 `fcntl`）；因此 Windows 上
   `assembly.single_writer_lease` 不是 `FREE`，`life_run` 仍依赖 resident 自己在 `start()` 里
   `acquire()` 的真实结果来 fail closed（`RUNTIME_REFUSED` + exit 4 用例覆盖了这条映射）。
4. **`state_root_secure` 只报不拦**：state root 不存在时是 `None`；存在但 0755 时是 `false`。
   运行期 `AutonomousLifeResident.start()` 自己会 `chmod 0700`（`autonomous_life_resident.py:362`），
   所以本层不去改权限（"不动别人的根"优先），只把它写进报告。
5. **手机根本身通常不是 0700**：`demo.prepare_demo()` 用默认 umask 建（实测 `0755`）。
   本层只报 `phone_root_mode`，不 chmod。
6. **`check_model` 复用了私有 helper** `autonomous_life_assembly._model_config`（附公共
   `profile_model_config` 兜底）。这样"配置齐全"与 resident 实际会用的解析一致；helper 改名时
   会走兜底分支，不会静默报"没有 provider"。
7. **`life_preflight` 的服务端探活是"没做"而不是"通过"**：missing socket 是 `UNAVAILABLE`，
   与"在但没有监听"在本层不可区分（不做 connect）。要真正确认，用 `life-run` 的那一次 `health` 读。
8. **socket 代次比对依赖 `run/phone.serve.json`**：该 marker 由 `demo.serve_instance` 写
   （`demo.py:830-838`）。没有 marker（例如只在 `prepare` 之后）时不做代次判断，不误报陈旧。
9. **`one_day_life` 只动了 argparse**：三处修复不改变 `prepare`/`read_page` 的语义；`read`
   仍然只读一页并落"取得+位置"，`understanding` 恒 `UNKNOWN`。

---

## 8. 三层结论

| 层 | 状态 |
|---|---|
| 机制已验证 | 绑定映射、preflight 零写零模型零连接、六条诚实 BLOCKED、同根 lease fail-closed、CLI 契约、三处 parser 修复 —— 32 例（Windows + WSL） |
| 真实模型未调用 | 本层没有任何请求路径；报告恒 `model_called: false` / `request_sent: false` / `verified_model_response: false` |
| 尚未启用常驻 | 无后台循环、无调度器；`life-run` 有界、跑完停自己的 resident；没有替操作者打开开关，也没有新开请求窗口 |
