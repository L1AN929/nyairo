# 安装与开始使用

当前发行是 `v0.2.0-rc3` 公开测试版：[GitHub 仓库](https://github.com/L1AN929/nyairo)、[预发布下载](https://github.com/L1AN929/nyairo/releases/tag/v0.2.0-rc3)。不是稳定版；旧 rc9 资产保留。

## 一条命令引导安装

Windows 用户先打开 WSL 2 / Ubuntu；Linux 用户打开自己的终端。使用普通账号运行：

```bash
curl -fsSL https://nyairo.com/install.sh | bash
```

自动准备 uv 和 Python 3.13，下载并校验固定 v0.2.0-rc3、安装依赖、创建独立个人配置，再打开 Hermes 模型设置向导并启动聊天。需要自己选择模型、填写密钥或服务地址。Ubuntu / Debian 如果没有 curl，先运行 `sudo apt update && sudo apt install -y curl`。

安装目录：`~/.local/share/nyairo/releases/v0.2.0-rc3`；个人数据：`~/.nyairo`；命令入口：`~/.local/bin/nyairo`。以后打开新终端运行 `nyairo`；模型设置用 `nyairo setup model`，平台设置用 `nyairo gateway setup`，网关用 `nyairo gateway run`。修改人设编辑 `~/.nyairo/SOUL.md`。

重复运行会核对程序、保留个人数据并再次打开模型向导，不自动升级到其他发行版本。旧手动安装不会自动搬家；要使用已有个人目录，可明确传入 `--profile`，继续使用前仍按隐私升级说明检查旧授权。

服务器上只安装、不打开向导或聊天：

```bash
curl -fsSL https://nyairo.com/install.sh | bash -s -- --no-setup --no-launch
```

自定义目录或只设置不启动聊天：

```bash
curl -fsSL https://nyairo.com/install.sh | bash -s -- --prefix "$HOME/apps/nyairo" --profile "$HOME/.nyairo" --no-launch
```

程序与个人目录必须分开。基础安装授权当前本地账号，默认启用记忆与生活状态；其他服务仍需单独配置。引导脚本位于 [scripts/bootstrap.sh](scripts/bootstrap.sh)，网站 `/install.sh` 发布同一份内容。RC1 两份脚本逐字节一致；公开资产校验完成后才更新官网副本，不改写旧标签或 ZIP。

## 手动 Git / ZIP 路线

按 [官网新手路线](https://nyairo.com/#quickstart) 或 [详细中文教程](docs/USER_GUIDE.zh-CN.md) 中的手动章节操作。原来的 `bash scripts/install.sh` 仅安装源码目录内的依赖，需要已有 uv，随后手动建立个人配置并设置模型。不要将它和官网引导安装命令混用。
教程依次说明下载、建立个人数据目录、选择模型、开始聊天、Telegram、各附加模块、更新、备份与排错。基础配置先开启记忆与生活状态；认知、World/Body 和资源文档按需要单独设置。资源服务的当前装配限制见 [功能与已知问题](https://nyairo.com/#status-matrix)。

已记录的普通账号安装复核覆盖 Linux / Windows WSL。原生 Windows、macOS、官方完整 Docker 方案和全模块一键配置仍没有完成相同范围的交付验收。

## 首次安装与旧配置升级

首次安装使用 v0.2.0-rc3；旧 ZIP 和标签不改写。创建 profile 时，只有加上 `--allow-local-owner` 才授权当前 Linux 账号的本地入口；Telegram 等入口仍需自己的明确 DM 绑定。记忆模式默认拦截模型工具，用户命令与聊天仍可用；主动放开工具会削弱遗忘后的读取限制。

已有 profile 不要重跑创建脚本，也不要删数据。先备份 `HERMES_HOME`，更新程序和个人插件副本，再按 [隐私修复与升级](PRIVACY_REVIEW.md) 检查自己的授权设置。你写好的 SOUL.md 和个人记录会保留。

内置 Hermes 已应用 44 个文件补丁，并非纯上游原样。`patches/rollback.sh` 是开发者在独立上游副本中复核补丁的工具；默认拒绝撤掉内置宿主接线。

RC1 引导安装会在 Linux / WSL 建立 `~/.local/bin/nyairo` 并记住个人目录。手动 Git / ZIP 路线在创建个人目录后，按教程运行一次 `bash scripts/update.sh --adopt` 登记入口。以后通过固定入口聊天、运行网关和整包更新。已有 rc5 安装请使用 [一次性迁移步骤](UPDATE.md)，保留原来的个人目录。

## v0.2.0-rc3 预发布安装

`v0.2.0-rc3` 是公开测试版，不是稳定版；发行提交以标签为准，来自 `v0.2-world-integration`，未合入 `main`。
安装器随本包指向 RC1，官网入口只在 Release 资产可下载后更新。也可按下面的手动路线安装。

需要的东西：Linux 或 Windows WSL / Ubuntu、Python 3.13、`curl`、`sha256sum`、`unzip` 或 Python 的
`zipfile`。全程用普通账号，不要用 root。

```bash
# 1) 下载预发布源码包和它的校验值
curl -fL -o nyairo-v0.2.0-rc3.zip \
  https://github.com/L1AN929/nyairo/releases/download/v0.2.0-rc3/nyairo-v0.2.0-rc3.zip
curl -fL -o nyairo-v0.2.0-rc3.zip.sha256 \
  https://github.com/L1AN929/nyairo/releases/download/v0.2.0-rc3/nyairo-v0.2.0-rc3.zip.sha256

# 2) 校验（不通过就停在这里）
sha256sum -c nyairo-v0.2.0-rc3.zip.sha256

# 3) 解压到一个独立目录
mkdir -p "$HOME/.local/share/nyairo/releases/v0.2.0-rc3"
python3 -m zipfile -e nyairo-v0.2.0-rc3.zip "$HOME/.local/share/nyairo/releases/v0.2.0-rc3"

# 4) 先核完整性，再装依赖（源码包不含 Git 元数据）
python3 "$HOME/.local/share/nyairo/releases/v0.2.0-rc3/scripts/verify_manifest.py"
cd "$HOME/.local/share/nyairo/releases/v0.2.0-rc3"
uv sync --project vendor/hermes --frozen --extra messaging --extra web

# 5) 个人目录：新装
vendor/hermes/.venv/bin/python scripts/setup_profile.py \
  --home "$HOME/.nyairo" --owner local-owner --memory --life --allow-local-owner

# 新装完成后登记固定入口
HERMES_HOME="$HOME/.nyairo" bash scripts/update.sh --adopt \
  --releases-dir "$HOME/.local/share/nyairo/releases"
# 已有 rc9 profile：先备份个人目录，再按 UPDATE.md 的整包升级路径迁移。
# 不要重建或删除 profile；本版兼容验收覆盖旧聊天、作品、记忆及治理记录。
```

`scripts/verify_manifest.py` 必须打印 `"valid": true`；它只核对**发行内容的完整性**（文件集合与逐文件
SHA256），**不证明发布者身份**，也不证明凭据状态。解压出来的树是 LF 行尾，这是被校验的那一份；不要
拿一份被 Windows 编辑器改成 CRLF 的副本去核对，那样哈希必然不匹配。

模型与平台仍要自己配：`nyairo setup model` / `nyairo gateway setup`。自主生活默认关闭，需要显式打开
（见下一节）。

## v0.2 自主生活（预发布候选）

自主生活基线为 `35df37f`，生成世界与常驻进程的同实例融合已通过隔离 owner 链验收。
发行标签为 `v0.2.0-rc3`；未合入 `main`，未切生产。下面保留基线隔离安装的真实历史操作。
自主生活依然需要独立服务、真实授权和显式 enable，不是基础安装后自动产生动作。

下面的命令与输出，是在隔离测试环境里**实际执行过**的一次完整安装走通（全新个人目录 → 初始化 → 配置供应商 → 资源服务工作区与授权 → 打开自主生活 → 一次合法作品活动 → 回读真实结果）。隔离环境不等于生产交付，逐能力判定见 [验收与复测](TESTING.md) 的 E13 门禁。

先认识四个词，够用即可：

| 词 | 一句话 | 你要知道的 |
| --- | --- | --- |
| state root | 这套生活的状态根目录（默认 `<安装根>/state`） | 开关、每日预算、唤醒与步骤日志、Activity 快照都在这里；**一个 state root 只允许一个写入进程** |
| subject | 主体身份 | 授权、工作区、作品都挂在它上面；换 subject 就是另一份生活 |
| Supply socket | 本机连接资源服务的 Unix socket 文件 | 不是网络端口，默认 0600，只允许它的所有者账号连接 |
| grant | 管理身份签发的一张"权限凭据" | 写自己的作品需要一份 `COMMIT_MANAGED_ARTIFACT`（限定到你的工作区）；授权编号必须由服务真实签发 |

完整走通（隔离环境，两个真实非 root 身份 `nyaop` / `nyasvc`，真实 Life Supply owner，真实模型 `gemini-3.8-flash-high`）：

```bash
# 0) 全新的一套目录，不碰任何现有实例
rm -rf /srv/nya02/inst && mkdir -p /srv/nya02/inst/profile
chown nyasvc:nyasvc /srv/nya02/inst/profile && chmod 700 /srv/nya02/inst/profile

# 1) 把已经配置好的模型供应商设置放进这套个人目录（真实安装里由 nyairo setup model 写入）
install -m 600 /root/.nyairo/config.yaml /srv/nya02/inst/profile/config.yaml
install -m 600 /root/.nyairo/.env /srv/nya02/inst/profile/.env
chown nyasvc:nyasvc /srv/nya02/inst/profile/config.yaml /srv/nya02/inst/profile/.env

# 2) 初始化个人目录：安装器用的同一个入口
runuser -u nyasvc -- python3 scripts/setup_profile.py \
  --home /srv/nya02/inst/profile --owner chiyo-install --memory --life --allow-local-owner
```

真实输出（截断）：

```text
Profile ready. Set HERMES_HOME to this directory, then use the normal Hermes CLI / gateway setup.
```

确认供应商设置真的可解析（密钥值不打印）：

```bash
python3 - /srv/nya02/inst/profile <<'PY'
import sys
sys.path.insert(0, "components/life/scripts")
from profile_model_config import describe_model_config, load_configured_model_config
print(describe_model_config(load_configured_model_config(hermes_home=sys.argv[1])))
PY
```

```text
{'provider': 'custom', 'model': 'gemini-3.8-flash-high', 'base_url': 'https://gw.929711.xyz/v1', 'key_env': None, 'api_key_present': True, 'hermes_home': '/srv/nya02/inst/profile', 'resolved_by': 'profile_shape_resolver', 'usable': True}
```

```bash
# 3) 实例自己的环境文件（隔离环境沿用 $ROOT/env.sh 的约定）
cat > /srv/nya02/inst/env.sh <<'EOF'
export LIFE_SUPPLY_SOCKET=/srv/nya02/sock/life-supply.sock
export LIFE_SUPPLY_OPERATOR_UIDS=1001
export LIFE_SUPPLY_SERVICE_UIDS=1002
export NYA02_SUBJECT=chiyo-install
EOF
. /srv/nya02/inst/env.sh

# 4) 管理身份签发工作区授权，服务身份开通工作区，再签发作品授权；然后由合法 owner 操作建立第一版作品
export NYA02_ROOT=/srv/nya02/inst
export NYA02_RESIDENT_DIR=/srv/nya02/inst/resident
export NYA02_RESIDENT_SUBJECT=chiyo-install
export NYA02_WORLD_ENV_FILE=/srv/nya02/inst/no-world-env   # 这套 profile 没有 World 服务，观测记录 UNAVAILABLE
bash components/life/tests/v02/run_resident_soak.sh prepare
```

```text
== operator issues the workspace grant
== service principal provisions the workspace
== operator issues the artifact grant for workspace:workspace:9ba2cd4c8b164d028abe38efcd990132
workspace=workspace:9ba2cd4c8b164d028abe38efcd990132 grant=grant:2fce125a99c540f6948f21ed0e51af78
== the first real work, created by a legal owner operation
memory ready: /srv/nya02/inst/resident/memory/evidence.db (controls .../controls.db)
created: artifact:be778d6d32e94796abffdbf392f7a54c status=OK reason=
head=artifact-version:d5904244225447b3b5e8190bd01ef6b9 v1
```

```bash
# 5) 默认是关的：新 state root 里没有开关文件
runuser -u nyasvc -- env PYTHONPATH="$PWD" python3 -m chiyo_bundle life \
  --state /srv/nya02/inst/resident --action status | head -20
```

```text
# 真实输出的关键行（budget_today 三项此时全为 null，中间字段省略）
 "autonomous_switch": {"enabled": null, "changed_at": null, "changed_by": null},
 "last_wake": null, "recent_wakes": [], "recent_steps": [], "pending_unknown_actions": []
```

缺东西时不会假装成功，而是给出具名拒绝（两次都是 `rc=3`，且都**没有任何模型调用**）：

```text
# 缺作品授权（真实输出，仅压缩空白）
{"started": {"detail": "no autonomous execution is possible without the Supply socket, the subject and a real artifact grant", "missing": ["CHIYO_LIFE_SUPPLY_ARTIFACT_GRANT"], "ok": false, "reason": "SUPPLY_CONFIGURATION_MISSING"}}
# 有授权，但这套 profile 没有可用供应商
{"started": {"detail": "the profile has no usable provider for the content writer", "missing": null, "ok": false, "reason": "NO_CONTENT_WRITER"}}
```

```bash
# 6) 打开自主生活，然后只跑一个真实考虑窗口（一次 tick）
runuser -u nyasvc -- env PYTHONPATH="$PWD" python3 -m chiyo_bundle life \
  --state /srv/nya02/inst/resident --action enable
export NYA02_PROFILE_HOME=/srv/nya02/inst/profile
export CHIYO_AUTONOMOUS_MAX_COGNITION_CALLS_PER_DAY=2
export CHIYO_AUTONOMOUS_MAX_ACTIONS_PER_DAY=1
bash components/life/tests/v02/run_resident.sh foreground --once
```

```text
{"recorded": true, "changed_at": "2026-10-11T02:04:47.601761+00:00", "changed_by": "operator"}
# 真实输出的关键字段（其余字段省略）
 "autonomous_enabled": true,
 "feeds": ["resume_eligibility", "artifact_head"],
 "model": {"cognition": true, "probe": {"host": "gw.929711.xyz", "port": 443, "status": "REACHABLE"}},
 "world": {"status": "UNAVAILABLE", "reason": "WORLD_NOT_CONFIGURED"},
 "counters": {"wakes": 2, "outcomes": {"ACTED": 1}, "owner_confirmed_versions": 1, "actions": 1,
              "model_calls": 1, "pending_unknown_last": 0, "pending_unknown_max": 0},
 "budget": {"cognition_used_today": 1, "max_cognition_calls_per_day": 2,
            "actions_used_today": 1, "max_actions_per_day": 1}
```

```bash
# 7) 回读真实结果：唤醒日志、步骤日志、以及直接问资源服务
cat /srv/nya02/inst/resident/state/autonomous_wakes.jsonl | tail -1
```

```text
{"kind": "CHECK_WINDOW", "status": "ACTED", "status_class": "acted", "outcome": "ACTED",
 "decision": "START", "llm_calls": 1, "action_id": "actn:ba85fba2b0ee8de6",
 "action_status": "SUCCEEDED", "activity_ref": "actv_d19a84f0063e250b",
 "detail": "owner committed artifact-version:7da6005463dd4e3d87e5ea20bdf8c3dd"}
```

```text
# 步骤日志（owner 自己的回执）
{"effect_state": "APPLIED", "status": "SUCCEEDED", "reason": "OK",
 "expected_head": "artifact-version:d5904244225447b3b5e8190bd01ef6b9",
 "version_id": "artifact-version:7da6005463dd4e3d87e5ea20bdf8c3dd", "version_number": 2,
 "operation_key": "life:2fe3393e392267d4489827e6d96dde9c"}

# 直接问资源服务（窄端口 read_head）
head    : {"version_id": "artifact-version:7da6005463dd4e3d87e5ea20bdf8c3dd", "version_number": 2,
           "parent_version_id": "artifact-version:d5904244225447b3b5e8190bd01ef6b9", ...}
content : "# 长测的手记\n\n第一段：先写一句，看看她会不会自己接着写。\n\n\n其实没有谁在真正注视，屏幕另一端只有光标在规律地闪动。隔壁工位的咖啡机响了第三回，今天的天气比预想中更早凉下来。\n"
```

这一轮**没有任何用户消息**：考虑由到期窗口触发，决策来自真实模型，写入由资源服务确认，新正文是模型写的散文而不是模板。这一次共 **2 次**真实模型调用（1 次决策 + 1 次内容生成）。

日常使用、能力边界与不得声称的部分，见 [详细中文教程](docs/USER_GUIDE.zh-CN.md) 的「v0.2 自主生活（v0.2.0-rc3 公开测试版）」一节。
