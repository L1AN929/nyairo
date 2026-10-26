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
