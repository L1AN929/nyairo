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

### 她今天的时间线（只读）

`GET /api/phone/today/timeline`（`chiyo_phone` 的 `day_timeline` 动作）把**已经有记录**的
几件事合成一条时间线：当天开始的 Activity、她自己排进今天的计划（只表示排定，不表示已执行）、
World 动作、日记写入、相册保存、钱包交易。它不补空白、不按当前状态反推过去，也不生成总结。

```bash
curl -s http://127.0.0.1:3831/api/phone/today/timeline | python3 -m json.tool
```

- 每条都带 `time_basis`；Activity/计划用实例自己的 Life 时钟，日记/相册/钱包用各自 owner 的
  记录时间。不同时钟域不会硬排成全序：`ordering.comparable=false` 时按域分组，域内再按时间。
- operator（普通页面，无 `X-Phone-Actor`）默认只有骨架，`skeleton_only=true`；日记正文、
  相册 caption/scene/生成提示词、钱包私有备注都不进聚合。她的工具（`X-Phone-Actor: model`）
  拿到原入口 `detail_ref`，要正文仍走 `diary_read` / `album_image`，权限与扣留规则不变。
- body 只给读取时的当前快照（`history=NOT_RECORDED`），不插进过去。
- 零产品模型调用。想机械复现一次真实写入 → 时间线 → 同根重开：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/phone_day_timeline_demo.py
```

### 日记正文与主体写入：本机能力凭据

日记的正文读取和“她自己的写入”现在还需要**本次 serve 的本地能力凭据**，不再只凭
`X-Phone-Actor: model` 这句客户端自述：

- 每次 `start` / `serve` 在实例根的 `run/phone-capability.json` 生成一个不透明凭据
  （WSL/Linux 下真实 0600，原子替换），正常停服删除，重启轮换。
- `chiyo_phone` 从 profile 的 `phone.capability_file` 读路径；省略时默认
  `<phone.profile_home>/run/phone-capability.json`。它每次调用重新读文件，并在发送前核对
  凭据绑定的 endpoint 与 subject；不匹配就不发请求。
- operator 的普通页面/目录仍只看到数量、日期、字数，正文保持扣留。声明 `model` 但没有
  有效凭据的读取会明确被拒（不是空正文成功）；没有凭据的主体日记写入零条目、零版本。
- 同一实例根同时只允许一个 serve 轮换凭据；后启动的 serve 不会覆盖正在服务的凭据。
- **边界**：能读取这个文件的同 uid 进程、能读该文件的账号或管理员仍在保护边界外；
  这不是“仅她可读”，也不是完整的管理员隔离。

如果平台无法确认文件私有权限（例如 Windows 未核验 NTFS ACL），本次 serve 不会写凭据，
私人日记明确不可用（fail closed）；元数据和其它手机功能仍可用。

机械复现真实 serve + 真实 Supply + 正式工具的完整路径（0 产品模型调用）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/private_diary_authority_demo.py
```

### 街区包自带的内容（营业时间、阅读材料、真实店面）

`daily_neighborhood_v1` 的 `content.json` 自带一份公版阅读材料
（陶渊明《桃花源记》开篇，东晋原作，operator 提供，不是 AI 生成，也不是全文）。
用 `--world-package daily_neighborhood_v1` 新建实例时，`init` 会通过真实 Supply owner
把它建成阅读 artifact 并绑定；包内没有 `reading_material` 时不预置材料。给材料不等于强迫她读。

- 咖啡店按包里的营业规则开门/关门；柜台菜单牌是店里的真实内容，但这个街区包
  **没有接入饮品售卖**，不能点单，也没有“喝过就舒服”的奖励。
- 书店阅读角按 10–20 点营业；营业时可以通过手机阅读工作台真实读取那份公版正文的一页，
  保存停点，`understanding` 始终是 `UNKNOWN`。阅读不需要买下书，也不是完整书店买卖。
- 实体家居店仍走既有经济 owner 的柜台购买；经济目录不是库存，库存未建模。
- 世界包只列了静态 `weather_candidates`，没有按规则演进的天气源；本入口不把
  `configured_v1` 的 `clear` 默认当成已观测天气。

想机械复现“同一街区早晨 OPEN / 晚间 CLOSED，营业时读一页真实正文，闭店购买被拒，
同根重开保留”的零产品模型演示：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/neighborhood_time_shops_demo.py
```

演示用显式的业务时钟文件机械模拟 10:30 和 20:30 JST（不是真实等待），
决定是脚本替身，不是模型选择；它只调用仓库里真实的 World/经济/Supply owner。

## 她的日记

她想写的时候，会通过 `chiyo_phone` 自己写一条：`diary_write`（正文 + 一个 `operation_key`），
`diary` 按时间列目录，`diary_read` 读回某一条正文。日期由手机自己的业务时间记录，不让她或
operator 手填；不会每天自动生成、不补写、不进 Memory、不外发。

operator 的普通读面（手机状态和 `GET /api/phone/diary`）默认只看到数量、日期和字数，正文不随
普通读取返回；只有她自己的工具路径（`X-Phone-Actor: model`）读得到正文。这是**普通读面的默认
边界**，不是本机管理员隔离，也不承诺挡住能直接读文件的人。

`X-Phone-Actor` 目前仍是客户端声明，服务端不核验；任何能访问回环接口的客户端都可以自行声明
`model`。服务端可核验的 model/operator 身份尚未实现，方案与待批事项见
[私人日记可信身份](engineering/private-diary-authority-options-20261027.md)。写入侧另走 Supply
的 Governance grant 与 `ManagedArtifact` 写入者围栏，不靠这个声明放行。

写入准入的实际边界（`scripts/existing_authority_admission_demo.py`，不调模型、只打状态和计数）：

- grant 的撤销、过期、scope/target/payload 绑定与 use 计数，在每次真实写提交时重新检查；
  撤销前已成功的版本和 receipt 保留，不被倒写，也不自动补偿。
- “内容与 head 相同”的 `NO_CHANGE` 快捷路径不是权限：它同样先做一次只读 grant 检查，
  已撤销/过期的 grant 不能靠重复内容拿到成功答复。这一步只读，不 reserve、不消耗 use。
- writer 轮替走正规停服→起新服务，epoch 由 fence 自己推进；旧 writer 对象不能再写库。
- 这只证明本机 Supply 的 `COMMIT_MANAGED_ARTIFACT` 路径。调用者身份仍是本机 service
  principal 的 `SO_PEERCRED`，不是真实人 grantor；也不代表整个 Python 后端或网络面都被覆盖。

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

## 她当前有理由相信什么（S03 主张，0 产品模型）

S03 只回答「现在有理由相信什么」，不回答「世界实际上是什么」，也不授权任何动作：

- 每条主张带 **来源类别**：`OBSERVED`（她通过正式 World 读面真的观察到）、
  `REPORTED`（某个明确来源报告过，只证明来源说过）、`INFERRED`（绑定已有主张的推断，
  不是新证据）；
- 每条主张带 **范围与时间**：谓词、地点/对象、营业日 context、`valid_time`、
  `observed_at`、`recorded_at` 分开；适用时间未知就是 null，不拿抓取时间冒充；
- 状态是离散的：`SUPPORTED` / `DISPUTED`（两个来源真的不一致，并排显示）/
  `STALE`（过了复核期限，期限不会因为再读一次而重置）/ `UNKNOWN`（没有合格证据，
  不是 false）/ `SUPERSEDED`（被明确更正替换，历史仍可查）。

新 root 由 `prepare` 在 `s03/claims.sqlite3` 建自己的绑定；旧 root 没有绑定，读面如实
`S03_CLAIMS_UNBOUND`，不会在读取时新建第二个库。手机工具用 `claims` 读当前主张、
`claim_slot` 读同一 slot 的修订历史、`claim_observe` 对 World 做一次正式观察并登记证据
（请求体只有 operation key，客户端不能提交「我是 World」或任意 world id）。

隔离演示（真实 Supply、真实 `PhoneWorld` 快照、真实 phone HTTP、一次正式
`evaluate_once`；Decision/内容是脚本替身，0 产品模型、0 外网）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s03_claims_demo.py
# 只跑协议/持久回归：
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest tests.test_s03_claims
```

演示覆盖：观察到位置并带观察回执 → 两个报告冲突 `DISPUTED` → 一次源更正让旧解释退出
当前且历史保留 → 推断随 basis 过期 `STALE` → 无证据 slot `UNKNOWN` → 同根重开状态保留
→ 撤回来源后旧正文在 current/history 都扣留 → 主张不写 World、不写 Memory、不授权动作。

## 一件活动怎么停下、等、回来和真正结束（S09，0 产品模型）

S09 不新造 Activity，也不新增 Decision 词：它把既有的 START/CONTINUE/WAIT/PAUSE/RESUME/ABANDON、
LR-2/LR-3/LR-4/LR-5 与 AR-0/AR-1 串成一条能实际理解的链：

- **停点**：Activity 自己的 `checkpoint_ref` 与 owner 当前 head 一起构成停点；回来是同一个
  Activity id 的 RESUME，不是第二次 START。
- **安全边界**：`SAFE_BOUNDARY` 的 PAUSE 只登记 pending，Activity 保持 ACTIVE，直到真实的
  boundary event 才变 PAUSED；`ATOMIC` 直接拒绝 PAUSE，不强停。
- **等待**：writer 明确 `no_change` 后，WAIT 绑定 owner head 依赖；WAITING 不占执行槽（另一件
  真实活动可以开始），只有 owner head 真变了 LR-4 才发一个 OPEN 资格；`NO_ACTION` 仍保持
  WAITING，不自动恢复。
- **版本变化**：RESUME 读 fresh owner head；并发 head 变化让旧 expected head 被 owner 明确拒，
  不静默 rebase；新的 Decision 在新 head 上继续。同根重装配保留 Activity/停点/条件/资格/未决。
- **完成**：落一版不等于完成整件作品；有界收尾 Activity 只有在 owner 自己的归档回执满足
  LR-5 完成合同后才 COMPLETED，并带 `completion_evidence_ref`。
- **区别**：主动 ABANDON、Action FAILED、Action UNKNOWN（用原 owner operation 对账）、
  Activity 仍 ACTIVE/WAITING 的 blocked/失败证据分开展示。当前 Activity 枚举没有
  FAILED/BLOCKED 终态，也没有生产 CANCEL authority capability；这两项是具名缺口，不伪造。

手机 `/today` 与 timeline 的 Activity 行新增只读 `recovery`：有 OPEN 资格才给 `RESUME`，
PENDING 条件只说条件未满足，终态与 ACTIVE 分开；资格 store 读不到时写
`ELIGIBILITY_UNREADABLE`，不把「不知道」说成「没有」。

隔离演示（真实 Supply/Activity/LR-2/LR-3/LR-4/LR-5/AR-0/AR-1；Decision 与内容是脚本替身，
0 产品模型、0 外网）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s09_continuation_demo.py
```

## 作品 Markdown：外部编辑只观察，导入要显式确认（S01，0 产品模型）

受管作品的正身始终是 owner 里的 canonical 版本；工作区里的 `.md` 只是可重建投影。
两件健康分开报：

- `content_health`：`AVAILABLE` / `UNAVAILABLE` / `CORRUPT`，说的是 owner 正文；
- `representation_health`：`CURRENT` / `MISSING` / `DIVERGED` / `UNBOUND`，说的是那个文件。

投影丢了照样能读正文；显式 `rebuild` 只修 `MISSING`/`UNBOUND`，`DIVERGED` 不覆盖。
用受管路径外部改过的文件只会变成候选：作者标 `EXTERNAL_UNKNOWN`，canonical head 不动。
导入必须带这一条候选和它被编辑时的基线版本（CAS），`--confirm` 不能省；头已前进就
`HEAD_MOVED` 冲突，不三方合并、不悄悄 rebase。导入成功的新版本来历是
`EXPLICIT_IMPORT`，不是「她本人创作」。

```bash
# 读两个健康域（纯读，不打印正文）
python3 scripts/s01_projection_cli.py --socket <owner.sock> --subject <subject> \
    status --artifact <artifact-id>
# 外部编辑后看候选
python3 scripts/s01_projection_cli.py --socket <owner.sock> --subject <subject> \
    observe --artifact <artifact-id>
# 明确确认导入（grant 用已有的 COMMIT_MANAGED_ARTIFACT）
python3 scripts/s01_projection_cli.py --socket <owner.sock> --subject <subject> \
    import --artifact <artifact-id> --candidate <candidate-id> \
    --base-version <被编辑时的版本> --grant <grant-id> \
    --operation-key s01:import-1 --confirm
# 只修缺失/未绑定的投影
python3 scripts/s01_projection_cli.py --socket <owner.sock> --subject <subject> \
    rebuild --artifact <artifact-id>
```

隔离演示（真实 Supply owner、真实受管 Markdown、真实 CAS 与重启；0 产品模型、0 外网）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s01_projection_demo.py
```

## 她当时选了什么、没选什么（S04 决定摘要，0 产品模型）

一条已提交的决定可以直接查，但只给**记录里真正有的东西**：

- **摘要**：`decision`、理由代码、`short_rationale` 是否存在；没有就写 `NOT_RECORDED`，
  不用模型事后补一句「她当时想……」。
- **选中与未选**：选中项来自 `selected_candidate_ref`；未选/延期/硬拒/当前不可执行项来自
  AG-1 提交时一并落盘的处置表。普通未选候选不保证长期保存，读到什么就是什么。
- **Decision 与 Adoption 分开**：`DECIDED_NOT_ADOPTED` / `DECIDED_ADOPTION_REJECTED` /
  `DECIDED_AND_ADOPTED` 三态。决定是历史，只有 S09 的 Adoption 产生当前约束。
- **旧决定复核**：用正式 `validate_decision_for_adoption` 纯读复核，会给出具名拒绝
  （例如 `STALE_ACTIVITY_REVISION`）；即使复核通过也只是可以重新走进既定流程，
  不继承旧许可、不自动执行。CLI 没有接正式 runtime 时明确报 `NOT_EVALUATED`。
- **rationale 扣留**：手机读面永远不返回正文；本地 CLI 默认只报长度，`--show-rationale`
  才在操作者自己的终端显示。

```bash
python scripts/s04_decision_cli.py --instance <life-instance> --list
python scripts/s04_decision_cli.py --instance <life-instance> --decision-id dec:xxxxxxxx
```

手机只读面：`GET /api/phone/decisions/review?decision_id=dec:xxxxxxxx`（缺省只列最近骨架）。
读取不写 Decision/Adoption/Activity。

隔离演示（真实 Supply/AG-1/AG-2/Activity owner、正式 `evaluate_once`；Decision 与内容是脚本
替身，机械业务时钟，0 产品模型、0 外网）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s04_decision_review_demo.py
```

## 一次经历到底记住了没有（S05 交接 / 准入 / 召回分层，0 产品模型）

**交接成功不等于还记着。** S05 读面把三件事分开：

- **源事件**：AG-2 的 handoff 身份（`lexp:result:` 或旧的 Activity 粒度）、动作 / 结算 /
  作品 / 版本 refs；只给不透明 ref，不回显正文。
- **准入 receipt**：AG-2 持久化的投递记录（`ACCEPT` / `DUPLICATE` / `DEFER`）与尝试次数。
  这是**历史**结果，不是「现在仍可召回」。
- **当前召回**：走既有治理投影后的 Memory 读面。控制记录生效后是
  `NOT_RECALLABLE`，但投递 receipt 仍保留原来的 `ACCEPT`。这是既有 epoch 规则，
  不是逐源隔离；本组演示没有下发任何控制，所以不展示这一段。
- **分层**：源证据与已接纳的 M0 记录是 `AVAILABLE`（F-069 的 raw 层）；
  Episode 分组、episode summary（F-069 的 summary 层）、embedding/检索索引与派生失效
  （F-070）在当前实现里**不存在**，一律报 `NOT_IMPLEMENTED`，不造空表。
- **合法“零新记录”**：同一结果重投时 M0 自己的幂等准入返回 `DUPLICATE`（零新行、零新版本，
  F-068 现有存储层）；设计 F-071 的语义 `NO_CHANGE` / `DO_NOT_ADMIT` 评估器尚未实现，
  也如实报 `NOT_IMPLEMENTED`，不拿 writer 的 `no_change` 或一个 `DUPLICATE` 冒充完整评价。
- **其余未证**：F-066 只到 M0 source 可证，Episode 未实现；重启只是额外验证，不是 F-071。

```bash
python scripts/s05_memory_review_cli.py --instance <life-instance> --list
python scripts/s05_memory_review_cli.py --instance <life-instance> --handoff lexp:result:xxxxxxxx
```

隔离演示（真实 Supply / M0 / 控制账本 / AG-1 / AG-2 / 正式 `evaluate_once`，脚本 Decision
与内容，0 产品模型、0 外网。治理 DELETE/CORRECT 的扣留链标 NOT_RUN，改为一次可撤的
读端故障注入，验证读失败具名 `UNAVAILABLE` 且撤除后恢复，不是撤权）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s05_memory_reliability_demo.py
```

## 一次动作到底做到哪一步（S10 请求 / 尝试 / receipt / 对账，0 产品模型）

**请求不是尝试，尝试不是 provider 受理，受理不是到达/用户收到，timeout 不是 FAILED。**
S10 的只读 CLI 把 AR-0 已有的五类事实分开列：

- **请求**：`PREPARED` 只表示请求已冻结（`REQUESTED_FROZEN`），不表示已 dispatch、更不表示
  已经发出去；AR-0 没有独立的 DispatchAdmitted 记录，就报 `NOT_RECORDED`。
- **尝试**：`attempt.started_at` 只证明本地尝试开始；它不等于 provider 收到请求。
  只有 `DEFINITE_PRE_SUBMISSION_FAILURE` 的尝试才允许 `not_sent=true`。
- **receipt**：`ACCEPTED` / `DELIVERED` / `READ` 各自显示；`ACCEPTED` 不是到达，
  本地收件箱/草稿也不是用户已收到，`user_received` 默认 `NOT_RECORDED`。
- **观测效果**：owner 重新读到的 `ResultEvidence`（`verified`）与 provider 回执分开。
- **对账**：每次 `reconciliation` 的 strategy/result/前后状态都列出来；`UNKNOWN` 保持
  `UNKNOWN`，查询耗尽也不会自动改成 `FAILED`。
- **具名缺口**：AR-0 没有 child/DAG/PARTIAL 结果向量，也没有 `QUIESCED_UNRESOLVED` 或
  bounded query scheduler（delays/maxQueries），一律报 `NOT_IMPLEMENTED`，不造平行 store、
  不加私有 timer。相同内容/hash 只是相容性证据，不能证明本次 Action 成功。
- **纯读**：发现未回收的提交 WAL 直接报 `WAL_PENDING_RECOVERY`，不替旧 writer 重放。

```bash
python scripts/s10_action_review_cli.py --instance <life-instance> --limit 20
python scripts/s10_action_review_cli.py --instance <life-instance> --action-id actn:xxxxxxxx
python scripts/s10_action_review_cli.py --state-root <instance>/life/state --format text
```

隔离演示（真实 Supply / AR-0 / AR-1 / 正式 `evaluate_once` + CLI 子进程，脚本 Decision 与内容，
0 产品模型、0 外网；有序停启不是 crash 矩阵）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s10_action_outcomes_demo.py
```

## 未来事项、今天的计划与 operator 义务是四件不同的事（S08 F-093～F-097，0 产品模型）

**认领不是承诺，未来的事不是当前 Activity，operator 的义务不是她自选。**
S08 的只读 CLI 把谁拥有什么分开列：

- **未来事项**（`fitem:`）：由她**已提交的 Decision** 的 `later_item` 真正写出的延期意向；
  `decision_ref` 是用同一个公开意向键重算出来的归属，不是猜的。取消/完成事项不删记录，
  也不复活；没有 Decision 记录就报 `NO_DECISION_RECORD_FOUND`。
- **今天的计划**（`plan:`）：`plan_today` 只是把一件**已有**的 OPEN 事项排进今天，
  记录里带提交它的 `decision_ref`；排计划不是开始做。
- **operator 义务**（`oblig:`）：外部给的当日安排，`writable_by_subject=false`；
  她可以去做，但不能改期/取消。指向 `oblig:` 的 `future_item_actions` 会在域名层被拒绝。
- **当前 Activity**（S09）：只列已经采用的现在，`status`/`decision_ref` 来自 Activity owner。
- **Agenda / Eligibility**（LR-4）：只是"条件满足后的恢复资格"，不执行任何动作；
  有前台时不抢占、不消费。
- **Commitment**：S08 **还没有承诺 owner**。`fitem:`、`oblig:`、Agenda 都不会被改叫承诺，
  也不存在 `promised=true`；F-095 与 F-097 的重新协商部分因此报
  `NOT_IMPLEMENTED`，方案见 `docs/engineering/s08-future-organization-options-20261027.md`。

```bash
python scripts/s08_future_review_cli.py --instance <life-instance>
python scripts/s08_future_review_cli.py --state-root <instance>/life/state --format text --now 2026-10-27T21:00:00+08:00
```

隔离演示（真实 Supply / AG-1 / AG-2 / LR-4 / 正式 `evaluate_once` + 同根重装配，脚本 Decision
与内容，0 产品模型、0 外网；有序停启不是 crash 矩阵）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/s08_future_organization_demo.py
```

## 动作效果、活动生命周期与后果域分开看（INFRA F155～F160，0 产品模型）

**文件已保存不等于所有生活后果都已结算。** 这条只读读口把一次真实动作拆成三条轴：

- **动作结果**（AR-0 + AR-1）：Action 状态、attempt、receipt、effect claims、
  `settlement_status`/`outcome_kind` 各自显示；`SETTLED` 只说明结果已结算。
- **活动生命周期**（LR-2 + LR-5）：Activity 仍是 ACTIVE 就报 ACTIVE，动作成功不会自动结束它；
  LIFE 域是否“已处理”只认 LR-5 自己消费过、带这次 `settlement_ref` 的证据。
- **后果 envelope**：设计 VIII.7 的 `ConsequenceEnvelope` / obligation compiler /
  target-owner receipt 在本仓库**不存在**，一律 `NOT_IMPLEMENTED`；AR-1 的 outbox 只是
  通知扇出，不冒充 envelope closure。

分域时两件事分开：AR-1 的 `memory` 通知按生产策略是 `DELIVERY_FAILED`
（`ProductionExecutionRefused`），它不是 Memory 的真实后果；Memory 的真实后果是 AG-2
经历交接（`lexp:*`）投递到 M0，读面按 `source_ref` 精确核实。没有绑定 Memory 时报
`UNVERIFIED`，不报“没有记录”。交接失败/挂起（`FAILED_PENDING_RETRY`）时，写入与 AR-1
结算照旧成功，但复核会明确写“不是所有后果域都已处理”；恢复后走既有
`dispatch_experience_handoffs` 重投原 handoff，同一身份、一次新记录，再投是 M0 的
存储幂等 `DUPLICATE`（不是语义 NO_CHANGE）。

```bash
python scripts/infra_settlement_review_cli.py --instance <life-instance> --limit 20
python scripts/infra_settlement_review_cli.py --instance <life-instance> --action-id actn:xxxxxxxx
python scripts/infra_settlement_review_cli.py --state-root <instance>/life/state --format text
```

隔离演示（真实 Supply / AR-0 / AR-1 / AG-2 / LR-5 / M0 Memory / 正式 `evaluate_once` +
CLI 子进程，脚本 Decision 与内容，0 产品模型、0 外网；有序停启不是 crash 矩阵）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/infra_consequence_settlement_demo.py
```

## 启动或重启前先做一次恢复只读检查（INFRA F161～F167，0 产品模型）

**正常重启不等于备份恢复。** 先看清这个实例是哪一种：可以正常装配、需要走已有恢复入口、
文件读不出来、还是必须人工先停掉另一个写者。这条检查只读：不取 writer lease、不重放 WAL、
不启动 runtime、不写业务数据，也不发模型请求。

```bash
python scripts/infra_runtime_recovery_cli.py --instance <life-instance>
python scripts/infra_runtime_recovery_cli.py --instance <life-instance> --json
# 想同时向 Supply owner 查未决动作的原始 operation（只读）：
python scripts/infra_runtime_recovery_cli.py --instance <life-instance> \
    --supply-socket <life-supply.sock> --subject <subject>
```

结论四档：

- `ASSEMBLABLE`：正常入口可启动；
- `NEEDS_EXISTING_RECOVERY`：有未恢复提交（WAL）或未决 Action；走既有
  `LifeIntegrationCoordinator.recover_all_and_reconcile(auto_reconcile_unknown_actions=True)`，
  按**原 operation** 对账，不新造 key、不重放成功动作；
- `MANUAL`：另一个活进程持有写者权威，先用它自己的入口 stop；
- `READ_BLOCKED` / `UNKNOWN`：文件不可读或证据不足，停下来看具名原因，不要删文件“解锁”。

退出码：`0` 可行动（前两档），`3` 需人工（`MANUAL`/`READ_BLOCKED`），`4` 证据不足。
**不拿“进程不在了”当 lease 已释放的证明**；检查本身不探测 flock，报告里会写清未评估项。

隔离演示（真实 Supply owner / 真 runtime / 真 SIGKILL 到 `response_lost_after_write` /
同根新进程恢复 / 旧 revision CAS 拒写 / 认知阻塞时只读仍可继续，脚本 Decision 与内容，
0 产品模型、0 外网）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/infra_runtime_recovery_demo.py
```

## 她的通讯录（参与者身份，F-140）

来话账本只记“有人来过”，不记“谁”。想知道某条来话属于谁，需要本机 operator 显式登记参与者并
关联来话；**没有登记的事件就是 `UNRESOLVED`，不会按显示名或来源猜人**。

入口只认实例，不认调用方自报的 state root / subject：

```bash
INSTANCE="$HOME/nyairo-day"    # 有 instance.json 的那一层

# 登记两个人（同名也不会合并）
python scripts/participant_registry_cli.py --instance "$INSTANCE" \
  create --display-name "小林" --alias "Kobayashi" --operation-key operator:create-1
python scripts/participant_registry_cli.py --instance "$INSTANCE" \
  create --display-name "小林" --operation-key operator:create-2

# 给同一人显式绑第二个通道；已有 endpoint 指向别人会被拒绝
python scripts/participant_registry_cli.py --instance "$INSTANCE" \
  bind-endpoint --participant participant:... --provider telegram --account-key 42 \
  --operation-key operator:endpoint-2

# 把一条真实来话显式关联到某人（先 resolve-event 看它现在是否为 UNRESOLVED）
python scripts/participant_registry_cli.py --instance "$INSTANCE" \
  link-event --event-ref contact_evt:... --participant participant:... \
  --operation-key operator:link-1

# 只读：这次会算出哪个 state root / subject / home
python scripts/participant_registry_cli.py --instance "$INSTANCE" binding
```

`--instance` 用仓库已有的加载器读实例文件，自己算出三件事：Life state root（标准实例是
`<实例>/one-day/life/state`；嵌套 Life 实例记录过 `NYA02_RESIDENT_STATE` 就以它为准）；
subject（手机实例自己的 `subject`，也就是来话 inbox 和手机读面用的同一个值；实例文件是
Life one-day 格式时取 `subject_id`）；home（与手机来访面同一套来源顺序）。`canonical_life_state.json`
里的 Life/global subject 是另一个域，会如实报告但不与实例 subject 比较。

`--state-root` / `--subject` 只作兼容：只有该根上已有能核对的身份（同 subject 的来话
inbox，或已有参与者账本）时才允许，**空目录 + 调用方自报 subject 不能开第一本账**；第一次
登记用 `--instance`。根上已有另一个 subject 的来话时直接具名拒绝，不落库。

写入口只在本机 POSIX 下工作：状态根与账本必须属于当前 uid、不能是 symlink、不能 group/other
可写；不满足就具名拒绝且不落库。Windows 没有可核的 ACL，写入口 fail closed（手机读取不受
影响）。`created_by=operator:cli` 是来源标签，不是对“真人”的认证；同一 uid 的其它进程或
root 不在这个边界内。

她的手机工具只能看、不能建：

- `chiyo_phone(action="participants")`：列出她认识谁（稳定 id、显示名/别名、来源、关联来话计数）；
- `chiyo_phone(action="participant", participant_id=...)`：一位参与者的端点与关联来话引用；
- 手机 HTTP 只有 `GET /api/phone/participants[/<id>]`，没有创建/绑定/发送写接口。

参与者只是身份：不继承任何权限、不表示关系，也不等于用户已收到消息。同一套本机演示（0 产品模型、
0 外发、数据保留）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/participant_registry_demo.py
```

## 显式忘记一个来源（F-153/F-154）

本机 operator 用实例自己的绑定提出请求；没有 HTTP 写路由，模型也没有删除工具：

```bash
python scripts/forget_request_cli.py --instance <手机/Life 实例根> \
    request --operation-key operator:forget-1 \
    --source-ref artifact:... --retention-pending
python scripts/forget_request_cli.py --instance <实例根> status  --case forget:...
python scripts/forget_request_cli.py --instance <实例根> retry   --case forget:...
python scripts/forget_request_cli.py --instance <实例根> confirm --case forget:...
```

- 协调库必须在实例环境里显式配 `NYA02_FORGET_CASE_DB`，并与 authority 一样放在实例备份集之外；
  CLI 的 `--case-db` 只能与它相同，运行时 gate 也读同一个名字。
- 派生 owner 用实例环境显式配置：`NYA02_SUPPLY_ARTIFACT_DB` 与 `NYA02_SUPPLY_PROJECTION_ROOT`。
  没配置时，事件指向的 artifact 会作为显式未覆盖面，不会假装清过。
- `--hold` 立刻扣留指定 source/event，但不写 DELETE、不擦正文；跨重开仍扣留，不会自动超时释放。
  之后要真删，用 `confirm` 显式确认同一个 case；`retry` 不会把 hold 升级。
- 屏障先落保留权威（event id + typed 来源 `{kind, namespace, ref}` 两个独立域），再由 M0 的
  `MemoryControlLedger.erase` 与 Supply 的 `ManagedArtifactStore.erase_source` 各自清理；
  两者都要已提交屏障、只接受本 owner 的 scope，且按 operation 幂等。
- M0 擦除保留 event 身份但清空正文/来源 JSON（`payload_erased=1`），并 `secure_delete +
  checkpoint + VACUUM`；Supply 清每个版本正文（`content_erased=1`）和绑定投影文件。
- `COMPLETE` 由 owner ack + 布尔物理读共同决定；离线备份未擦、显式未覆盖面、物理正文残留都会
  停在 `WAITING_RETENTION` 并具名原因。备份副本仍含正文属于 retention，不当成“所有副本已擦”。
- 恢复副本先跑 `recovery_apply`（RECOVERY_ONLY）校验保留权威并重施屏障/派生清理，再开放读取；
  权威缺失或低于 floor 一律 `BLOCKED`，不用新 floor 掩盖旧代际。
- 隔离演示（0 产品模型、夹具保留）：

```bash
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/forget_generation_demo.py
```
