# Agent Note: rc1 手机实例是主体 Life 的唯一 live 绑定来源（无界面 preflight/run）

Status: implemented

## Problem

rc1 之前，手机上能读自己材料的路只有两条，都不是"主体自己在过":(a) 聊天模型通过 `chiyo_phone`
工具打 HTTP 到 `serve`;(b) 操作者手工跑 `one_day_life.py read`。**没有任何入口把"主体 runtime"起在
手机实例上**,而手机实例与 Life 实例是两份文件:

* `demo.serve_instance()` 每一代 `serve` 都会把**新的** `supply_socket`/`supply_generation` 写回手机
  实例(`components/phone/demo.py:751-757`);
* `one_day_life.prepare()` 写进 `<root>/one-day/instance.json` 的 `environment` 仍是**做准备那一代**
  的 socket。

于是"照 Life 清单起主体"会连到一个已无人监听的旧 socket;而"照手机清单起"又缺 Life 自己的
run 窗口、请求预算与 state root。另外 `one_day_life.py` 的 CLI 有三个确切缺陷,其中
`--create-private-items` 被声明两次会让 `argparse` 在**构造 parser 时**抛
`ArgumentError: conflicting option string`,即 `one_day_life.py --help` 与所有 verb 都起不来;
`--supply-socket` 与 `--unclear` 被使用却从未声明。

## Decision

新增 `components/phone/life.py`(P 独占写集),把**手机实例当作 live 绑定的唯一来源**,在内存里映射成
现有 Life runtime 的环境名,并只经由 `scripts/phone.py` 的两个新 verb 暴露:

* `life-preflight --instance ROOT`:**纯读**。只 `lstat` socket 路径(不 connect)、只读
  `run/phone.serve.json` 比对代次、只读 `flock` 既有生命锁文件、只读开关/预算/profile 的
  `phone.private_artifacts` permit、只读 provider 配置。报告把 `assembly.feasible`(能装起来)、
  `run_ready`(socket 在 + 开关开 + 预算设了)、`model_called: false` 三件事分开写。
* `life-run --instance ROOT [--once]`:复用**现有**
  `autonomous_life_resident.AutonomousLifeResident`(同一个类、同一个 `state_root`
  =`<root>/one-day/life/state`、同一把 lease),默认有限(不给界时 `--duration-seconds` 默认 1800),
  一定有 `finally: stop()`。

映射顺序写死在 `build_binding()`:`os.environ` → 剥 `one_day_life.OWNED_ENV_NAMES` 与全部
`CHIYO_AUTONOMOUS_*` → nested `environment`(只读拷入,空值跳过) → 手机 live 覆盖 → 手机 `features` →
Life root 默认 → `<root>/one-day/model_env`(只补缺) → 强制 `pop(CHIYO_AUTONOMOUS_NEW_RUN_WINDOW)`。
`sources`/`overridden`/`conflicts`/`missing`/`unknown` 全部随报告返回;nested 清单**一行不写**。

`life_run` 在构造 resident 之前过十一道具名 gate(清单可读、nested 存在、无 cross-binding 冲突、uid
匹配、socket 是 socket 且代次不陈旧、provider `configured`、操作者开关为 `True`、显式请求上限存在、
生命锁不是 `HELD`、Supply 答一次 `health`);任一不过就带 reason + exit code 返回,`resident_factory`
一次都不构造。exit code 沿用 `one_day_life` 的 `0/1/2/3/4` 语汇。

`components/life/scripts/one_day_life.py` 只修 argparse 三处:删掉重复的 `--create-private-items`、
补 `--supply-socket`、补 `--unclear`;`prepare`/`read_page` 的语义与实现未动。

## Alternatives considered

- **直接照 `<root>/one-day/instance.json` 起主体(不映射)** —— 最强理由是"只读既有清单、零新增代码,
  也没有第二份映射规则会漂"。否决原因:socket 代次必然过期(见 Problem 的 `demo.py:751-757`),
  主体会一直连一个没人监听的路径,把"她自己读不了"伪装成"owner 故障";而且它把 Life 清单当成 live
  真相,等于在手机 `serve` 每一代都制造一次静默失配。
- **复用 `one_day_life.preflight()` 当手机 preflight** —— 最强理由是"一套预检语义、少写代码"。否决原因:
  它**不是零写**也不是零探测:`_preflight_supply()` 真 `connect` 并 `get_grant`;
  `_preflight_model()` 做 TCP 探针;`_probe_writer()` 用 `ActivityAuthorityLease.acquire()` 探测,
  会 `mkdir run/`、建锁文件、写并删 `life_authority.json`。施工单要求手机 preflight 纯读零写零模型,
  所以改为只读 `flock` 既有锁文件(没有 `fcntl` 就报 `UNKNOWN`,不报 `FREE`)。
- **改 C 的 `AutonomousLifeResident` 加 cognition/writer 注入缝,好在测试里跑"脚本化决策 + 真实 owner"**
  —— 最强理由是这能把"读到第 2 页"从 C 侧一路验到手机实例上。否决原因:本轮 C 是唯一 Life 核心写者,
  P 不得改 C 的文件(施工单把 `autonomous_life_resident.py` 明确划给 C);resident 只接受
  `state_root/environment/poll_seconds/act`,所以只在 Python API 上留 `resident_factory`(resident 形状的
  替身)来验证"绑定喂对了什么",**不**在 CLI 暴露任何注入口。该缺口写进交付结果而不是私自补。
- **`life-run` 顺手把 `autonomous_switch.json` 打开(或提供 `--enable`)** —— 最强理由是"操作者一条命令就能
  让她开始过日子,体验更好"。否决原因:长期自主开关是操作者的决定,而且它跨重启生效;替操作者打开
  等于把"她自己在过"变成"CLI 帮她打开"。现在开关关着就是 `AUTONOMOUS_SWITCH_OFF`(exit 3),
  并且"开关字节不变"有测试钉住。同理不提供 `--new-run-window`:请求窗口只沿用 nested 清单里的
  run_id/上限,连 `CHIYO_AUTONOMOUS_NEW_RUN_WINDOW` 都从环境里主动剥掉。

## Consequences

- **收益**:手机实例一旦 `serve` 在跑,主体就能在**同一个 socket/subject/workspace/grant/artifact** 上被
  有限地跑一步,而且失败原因永远是具名的(`NO_MODEL_PROVIDER`/`AUTONOMOUS_SWITCH_OFF`/
  `REQUEST_CEILING_UNSET`/`SUPPLY_OWNER_UNREACHABLE`/`SUPPLY_BINDING_STALE`/
  `CROSS_BINDING_CONFLICT`/`LEASE_HELD`);preflight 对实例目录零写入(mode/size/mtime 快照相等),
  没有第二条正文路、没有第二个 Supply/World、没有新 store。
- **代价与已知上限**:① 没有 cognition/writer 注入,所以"真模型选的"这条证明仍留给 C 侧/父级验收;
  ② 本轮没有在真实 provider 上放行过(只验证了六条拒绝路径);③ Windows 上 lease 与 uid 只能
  `UNKNOWN`/不拦,`fcntl`/AF_UNIX 用例在 WSL 才跑;④ preflight 不做 connect,所以"在但没人监听"与
  "不在"对它不可区分 —— 要确认只有 `life-run` 那一次 `health` 读;⑤ 手机根本身通常 0755
  (`demo.prepare_demo` 用默认 umask),本层只报不 chmod。若将来 C 给 resident 加了安全的
  cognition/writer 注入缝,应重访本决定并删掉 `resident_factory` 这条测试缝。

## Verification

- `python -m unittest tests.test_phone_subject_life`(Windows 11 / Python 3.13)→ `Ran 32 tests … OK
  (skipped=5)`,exit 0;同一份在 WSL(Ubuntu / Python 3.12.3)跑 → `Ran 32 tests … OK`,exit 0
  (5 个 POSIX 用例在 Windows 上 skip、在 WSL 上通过:foreign uid、第二 writer 同根、只读锁探针不建目录、
  AF_UNIX 无连接、非 socket 路径)。
- `python components/life/scripts/one_day_life.py --help` → exit 0(修好前是 `ArgumentError`);
  `python scripts/phone.py --help` → usage 含 `life-preflight,life-run`,exit 0。
- 交接契约与运行手册:`docs/engineering/rc1-subject-materials-runbook.md`;签名/映射/退出码/限制:
  `docs/engineering/rc1-subject-materials-binding-result.md`。核心侧处境与阅读循环见
  `docs/engineering/rc1-subject-materials-contract.md`(C 侧)。
- 相邻既有决定:[手机本地 owner 面](./2026-10-17-v04-phone-api-local-owner.md)、
  [阅读停点与私人笔记契约](./2026-10-22-rc1-reading-owner-phone-reading.md)、
  [单一写者隔离](../../implemented/process/2026-10-16-single-writer-isolation.md)。
