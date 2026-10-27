# 一条命令，开始她的日常

只在 **WSL / Linux** 里运行。先进入源码目录：

```bash
cd /path/to/nyairo-rc1-body-backend
```

实例目录必须是不存在的新目录（例如 `$HOME/nyairo-day`）；已有数据不会被覆盖，不是这个入口创建的已有实例也不会被接管。

## 1. 准备模型配置（可选；不配也能启动，只是她不会自主活动）

```bash
cat > "$HOME/.nyairo-model.env" <<'EOF'
NYA02_MODEL_PROVIDER=custom
NYA02_MODEL_BASE_URL=https://你的网关地址/v1
NYA02_MODEL_NAME=你的模型名
NYA02_MODEL_API_KEY=你的密钥
EOF
chmod 600 "$HOME/.nyairo-model.env"
export NYA02_MODEL_ENV_FILE="$HOME/.nyairo-model.env"   # 在同一个终端里执行
```

密钥不要写进命令行、不要提交到 git。

## 2. 启动（一条命令）

```bash
python3 scripts/nyairo_day.py start --root "$HOME/nyairo-day"
```

目录不存在时，这一条会先建实例（真实 Supply/World、这个实例自己的 Memory），再启动服务。

- Memory 删除底账默认在 `$HOME/nyairo-day-memory-authority/authority.db`，在实例目录之外；要换位置用 `--memory-authority /绝对/路径`。
- 看到 `服务就绪，尚未开始自主活动。` 就表示手机、Supply、World 都起来了；**默认没有发任何模型请求，不花额度**。
- 服务默认在后台运行；想前台看日志加 `--foreground`，用 Ctrl-C 停止。

### 新街区 + 可选虚拟身体（都是显式选择）

```bash
python3 scripts/nyairo_day.py start --root "$HOME/nyairo-day" \
  --world-package daily_neighborhood_v1 \
  --lived-needs \
  --room-living --world-actions
```

- `--world-package daily_neighborhood_v1`：使用仓库里的日常街区包（家、门前街道、街口、
  小公园、咖啡店、书店阅读角、街区家居店）。包会复制进新实例的 `world-package/`，
  源包不会被改写；不写这个参数仍是原来生成的默认世界。
- `--lived-needs`：给这个新实例启用虚拟饿/渴/如厕档案，初值默认 0（中性）。
  需要合成测试值时显式写 `--lived-needs-thirst 0.72` 等；初值只由操作者给，
  不从过去经历推。家里随实例准备声明了水瓶、饭团和洗手间，走既有
  `state/world_object_capabilities.json` 接口；杯子不是水，拿杯子不缓解口渴。
- `--room-living`：让同一个 World 进程和手机面看到相同的营业/店面/配送/布置状态。
- `--world-actions`：只对**这次启动的进程树**显式打开本地世界动作（临时 canonical）。
  默认写门仍是关闭的；不写这个参数就不会改任何全局权限。
- 街区包和虚拟身体档案是**新实例的选择**：已有实例不接受换包或改初值，
  会明确要求换一个新目录；`--room-living` / `--world-actions` 是运行开关，
  同根重开会沿用上次选择。

## 3. 看她在做什么 / 给她一份材料

```bash
python3 scripts/nyairo_day.py status --root "$HOME/nyairo-day"
python3 scripts/nyairo_day.py phone  --root "$HOME/nyairo-day"
python3 scripts/nyairo_day.py import --root "$HOME/nyairo-day" --file ./演示材料.txt
```

- `status`：中文状态、她此刻在哪、今天做了什么、今天的安排、作品、阅读、Memory、持久预算。
- `phone`：AI 手机的只读入口，同时打印并读取：手机页面 `http://127.0.0.1:3831/`、今天 `/api/phone/today`、安排 `/api/phone/today/arrangements`、作品 `/api/phone/projects`。
- `import`：把 UTF-8 文本导入成她的阅读材料，由真实 owner 建件并回读。**给材料不等于强迫她读**；服务正在运行时，新绑定下次 `start` 后生效。
- 刚建好、还没跑过一天时，「今天」「安排」会明确返回 `CANONICAL_STATE_ABSENT`（读不到就说读不到，不假装成空白的一天）；「作品」与阅读材料读口正常。

## 她的日记

她想写的时候，会通过 `chiyo_phone` 自己写一条：`diary_write`（正文 + 一个 `operation_key`），
`diary` 按时间列目录，`diary_read` 读回某一条正文。日期由手机自己的业务时间记录，不让她或
operator 手填；不会每天自动生成、不补写、不进 Memory、不外发。

operator 的普通读面（手机状态和 `GET /api/phone/diary`）默认只看到数量、日期和字数，正文不随
普通读取返回；只有她自己的工具路径（`X-Phone-Actor: model`）读得到正文。这是**普通读面的默认
边界**，不是本机管理员隔离，也不承诺挡住能直接读文件的人。

想单独看这条链是否通（不调用模型、不打印正文）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_diary_demo.py
```

## 她的相册与拍照

她通过 `chiyo_phone` 自己拍一张：`camera`（画面描述 `scene` + 她自己写的一句 `caption` +
一个 `operation_key`）。手机让**本机**的文生图运行时生成一张图，再走既有 Supply 相册正式保存；
`album` 按时间列目录（时间、说明、来源），`album_image` 读一张的说明与来源。

- 这是本机生成的图像，不是真实相机照片，也不是她亲眼所见；来源会如实标成 `local_generated`。
- 画面只来自调用者：生产入口原样接收 `scene`/`caption`，没有默认画面、不会自动拍照。
  `scripts/phone_camera_demo.py` 里的画面和说明只是演示常量。固定的只是生成参数
  （512px、12 步、Euler a、CFG 5），不是画面内容。
- `scene` 可以直接用中文写。手机侧会把它交给**本机已有的**文本服务（默认
  `http://127.0.0.1:18190`，可用 `PHONE_CAMERA_TRANSLATOR_BASE_URL` 指向其它 loopback 端口）
  转成标签式英文；她的中文原话和实际送去生成的英文都留在 `album_image` 的详情里
  （`scene` / `generation_prompt` / `translation`）。转换器只做技术转换，不替她选画面、不写说明。
  转换失败或服务正忙时明确拒绝（`CAMERA_TRANSLATION_UNAVAILABLE` / `CAMERA_TRANSLATOR_BUSY`），
  不会把中文原样塞给生成模型，也不会用固定模板冒充。本来就是英文的 `scene` 直接使用。
- 同一个 `operation_key` 重试直接读回原图，不会再生成第二张，也不会重新翻译或重新生成；
  同一 key 换画面或说明会被拒绝。
- 生成服务默认 `http://127.0.0.1:7860`（可用 `PHONE_CAMERA_BASE_URL` 指向其它 loopback 端口）。
  它正忙、出错或超时时明确拒绝并说明原因，不排队抢别人的 GPU，也不拿占位图冒充成功。
- 已知限制：2026-10-26 的演示图实际由 `waiIllustriousSDXL_v160` 生成，中文提示词下出过与画面
  无关的抽象图并带角落「@陈昊荣」文字水印。只读排查未在请求参数、已装脚本/扩展、WebUI 配置或
  安装文本中找到水印来源；同参数只换英文提示词的一次对照没有再出现水印（单样本，不据此要求
  产品只用英文）。画面质量取决于本机运行时与当前模型，不保证符合描述。
- **转换能力的现状（2026-10-27 实测）**：`rewrite2-4b-Q4_K_M` 在 `127.0.0.1:18190` 上三次真实
  请求内跑通。第 1 次（合成窗台场景）返回可用英文，但自行加了输入没有的 `reflections` 和
  `photorealistic/8k` 一类质量词；加强“只译已述内容、不加质量/风格词”的提示后，第 2 次因
  `finish_reason=length` 被截断，按新规则具名拒绝（不再把截断当成功）；第 3 次换场景
  （厨房木桌两只红苹果、银色小刀、窗外下雪）得到
  `kitchen, wooden table, two red apples, silver small knife, snow outside window`，语义与数量保持。
  转换失败、忙或截断仍明确拒绝，不会把中文原样送去生成。单次确认不等于任意长句都稳定。
- 照片和说明默认可见；日记正文的默认扣留规则不变。

两个本机运行时都**只在显式调用时启动**，只监听 loopback、只用本机已装模型、不自动常驻：

```bash
python -B scripts/camera_local_runtime.py translator   # 18190，翻译
python -B scripts/camera_local_runtime.py sd           # 7860，SD WebUI API（临时数据目录）
python -B scripts/camera_local_runtime.py sd --print-command   # 只打印命令，不启动
```

SD 第一次起来后还要把模型指到 `waiIllustriousSDXL_v160`（临时数据目录，不改原配置）：

```bash
curl -X POST http://127.0.0.1:7860/sdapi/v1/options -H "Content-Type: application/json" \
  -d '{"sd_model_checkpoint":"waiIllustriousSDXL_v160.safetensors","CLIP_stop_at_last_layers":2}'
```

想单独看转换这一层是否通（**不出图**；会真实调用一次本机文本服务）：

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/camera_translation_demo.py
# 父级复读上次结果（不再调用模型；失败也会把具名原因留在结果里）：
PYTHONDONTWRITEBYTECODE=1 python3 -B scripts/camera_translation_demo.py --reuse-last
```

想看拍照整链（会真实生成一张图，不调用产品认知模型）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_camera_demo.py
# 复用已经确认过的英文转换、不再请求文本服务（记录会标 local_llama_cpp_reused）：
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_camera_demo.py --reuse-confirmed-translation
# 已经生成过时，父级复读同一张（不再生成）：
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_camera_demo.py --reuse-last
```

## 出门走走（演示，不调用模型）

想单独看一条真实的世界生活链——从家走到公园看花圃、选择不行动，再走到街区家居店
看货/付钱，回家等交付、收下并摆放，最后同根停服重开验证余额和物件位置还在：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/world_outing_demo.py
```

- 每次在新隔离根 `/tmp/nyairo-outing-*` 里跑（保留不删）；World/Body 服务、经济钱包、
  手机 `PhoneWorld`/`PhoneShopping` 面都是真实 owner，移动走正式计时旅程，每段各自
  取得并消费正式授权。
- 时间是**注入的业务时钟**按世界包走道秒数机械快进，不是真走 390 秒；服务、授权和
  租约仍用真实时间。
- 路线、花圃/店面（`daily_neighborhood_v1` 的场景声明）、营业时间、价格、产权、
  2 小时到家交付都来自 owner；脚本只做「选去哪、买哪件、摆哪」的脚本替身决定，
  **不是模型选择**。
- 钱包是演示用的虚拟初始额度（10000 JPY），没有真实支付；不联网、不生成图片、
  不使用真实用户数据。这条链不涉及作品/阅读，因此不启动 Supply。

## 想让她真的行动（会花模型额度）

```bash
python3 scripts/nyairo_day.py run-day --root "$HOME/nyairo-day" \
    --live-model --duration-seconds 120 --max-new-requests 1
```

- `--live-model` 是唯一允许真实模型请求的开关；没有它，一个请求也不发。
- `--duration-seconds` 是最长运行时间，`--max-new-requests` 是这一段最多新请求数。
- 请求从实例自己的持久账本继续扣，不重置、不换 run；失败也不退款。`start` 可以继续开着，`run-day` 复用同一个 Supply/World。
- 默认 World 写门仍是关闭的，Memory 准入和预算规则都没有被这个入口改动。

## 停止与下次继续

```bash
python3 scripts/nyairo_day.py stop --root "$HOME/nyairo-day"
```

下次用同一条 `start`。材料、记录、账本、作品都还在，不 reset、不重建。

## 遇到这些情况，先看这里

| 你会看到 | 意思 / 怎么办 |
| --- | --- |
| `这个目录已经存在。为了不覆盖，请换一个还不存在的目录。` | 换新路径，或对已有实例直接 `start`。 |
| `这个端口已经被别的服务占用` | 换 `--port 3832`，或先停掉占用端口的服务。 |
| `模型还没有配置好，所以这次没有发任何请求` | 回到第 1 步；`start` 仍可正常启动，只是不会自主活动。 |
| `Supply owner 在等待时间内没有就绪` | 看 `$HOME/nyairo-day/logs/supply.log` 最后几行；原因代码在 `status`/输出里。 |
| `Memory 删除底账不能放在实例目录里` | 用 `--memory-authority` 指到实例目录之外。 |

每次失败都会保留原因代码和日志路径，不把“没成功”说成成功；服务是否真的起来，以 `status` 和手机读口为准。这个入口不新造运行平台、不迁移旧实例、不对外发送消息、不新增网页。

## 可选：虚拟身体需求（饿、渴、如厕）

默认关闭。只有对**一个新实例**显式启用，身体才会从 `body.runtime.v1`
升到 `body.runtime.v2`，多出白天的饥饿/口渴/如厕倾向；没有 profile 的旧实例
一个字段都不变，CONSUME 仍不产生营养。

正常用法就是启动时加 `--lived-needs`（第 2 节）；已有新实例要单独补写档案时：

```bash
python3 scripts/body_lived_needs_enable.py --instance "$HOME/nyairo-day" \
    --hunger 0.72 --thirst 0.78 --toilet 0.66
```

`status` 只显示档位（none/mild/noticeable/strong），不显示 0..1 原始值；
`phone` 里的 `world` 读口在 `body.needs` 带同一组档位。

- 初值是操作者给的字面参数，不是从过去经历或种子负荷推出来的。
- 随时间的变化只按服务活着时连续 tick 见证的时间累积；停机、睡眠、重启造成的
  间隔不补算（只记 skipped/unknown），没有午夜清零、没有到点自动行动、
  没有死亡/疼痛/健康惩罚。正常停服再重开，已经发生的需求原样保留。
- 吃、喝来自 World 在 `state/world_object_capabilities.json` 的
  `consumables[*].lived_need_effects` 显式声明；如厕来自同一文件的
  `lived_need_facilities`，必须在当前地点且该地点开门。声明没写效果就不回血。
- 是否去吃/喝/用厕所仍由她自己的正式 Decision 选；没有可用对象时只如实没有动作。

隔离演示（0 产品模型请求，Decision 是脚本替身）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/body_lived_needs_demo.py
# 不启动服务、不再行动，只复读上一次结果：
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/body_lived_needs_demo.py --reuse-last
```

## 验证入口本身（0 产品模型请求）

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/living_entry_demo.py
```

- 在 `/tmp/nyairo-living-entry-*` 建一个全新根，走真实 `init` / 前台 `start` / 正常停止；
  真 Supply/World/Body/Memory/PhoneService。
- 用真实 `chiyo_phone` 工具读 world / wallet / inventory（身体状况在 `world` 的
  `body.needs`），并提交一条合法 POSE；owner 回执和回读姿势都写进结果。
- 商店只在真的到店时可用；在家里读 `store` 会如实返回 `STORE_LOCATION_REQUIRED`，
  不假装到店。路线和选择是脚本替身，不是模型选择。
- 同根再启动一次，确认包、身体档案、姿势和钱包都没被重置；结果在
  `.agents/worker-runs/20261027-living-entry-demo.json`。

## 可选：一次有界真实自主拍照

这不是自动拍照，也不是第二个相机：手机仍是既有相机与相册，新增的只是一个**一次性会话入口**，
让产品主体模型在一次工具会话里自己决定要不要用手机拍照；最多 3 次产品请求，最多 1 张图。

```bash
# 1) 先用 preview 起两个本机服务（.claude/launch.json 里的具名配置）
#    camera-translator（18190）、camera-sd（7860）

# 2) 只准备、不调模型（0 产品、0 图）
/root/shigure-venv/bin/python -B scripts/autonomous_camera_once.py prepare

# 3) 只读核对
/root/shigure-venv/bin/python -B scripts/autonomous_camera_once.py status
/root/shigure-venv/bin/python -B scripts/autonomous_camera_once.py readback

# 4) 唯一一次真实会话（发送前写 started，进程断后拒绝重跑）
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && { set -a; . /root/.nyairo-model.env; set +a; }; exec env PYTHONDONTWRITEBYTECODE=1 /root/shigure-venv/bin/python -B scripts/autonomous_camera_once.py run --live-model'
```

- 提示只中性告知手机能做什么与该隔离 World 自己报告的可见事实；不写 scene、不诱导、不强制。
- 没选拍就是 `NOT_COVERED`（0 图），拒绝不是故障；翻译失败就停在转换处，不重造英文。
- 结果在 `.agents/worker-runs/20261027-autonomous-camera-result.md`；本窗口只做到 prepare/status/readback，
  真实主体判断尚未发生。
