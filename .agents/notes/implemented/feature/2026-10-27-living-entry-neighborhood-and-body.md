# Agent Note: 日常入口可选新街区与虚拟身体，以及显式本地动作

Status: implemented

## Problem

`scripts/nyairo_day.py` 已经能一条命令建实例、起手机/World、看状态，但它只会生成
默认世界，也没有把可选的虚拟身体需求接进入口。新人想用仓库里已经验过的
`daily_neighborhood_v1` 街区，还得自己知道 `one_day_life.prepare(world_package=...)`，
或者手动复制包、改 `world_object_capabilities.json`。测试里出现过的物体/设施声明
如果只写在演示脚本里，就会变成「demo 临时塞一个水瓶」，下一次实例又什么都没有。

同时，房间链（实体店/交付/布置/灯）和本地世界写动作都要显式开关：默认 World 写门
是 off，不能因为「启动了一个新世界」就悄悄开 canonical 或改全局配置。

## Decision

1. **入口的创建选项。** `init` 和首次 `start` 接受 `--world-package`（`examples/worlds`
   下的包名或一个包目录）和 `--lived-needs`（以及显式初值）。包会被复制进新根的
   `world-package/`，再沿既有 `worldgen` / `one_day_life.prepare` / `prepare_demo` 实例化；
   源包不被改写。没有这两个参数时仍是原来的生成世界和 v1 身体。
2. **创建选择固定。** 本入口在 `run/nyairo-day.instance.json` 记录包身份（`world_id`）
   和虚拟身体初值。已有 root 如果请求了不同的包或不同的初值，直接返回
   `NEED_NEW_ROOT_FOR_WORLD_PACKAGE` / `NEED_NEW_ROOT_FOR_LIVED_NEEDS`，不迁移、
   不覆盖、不重新启用。
3. **虚拟身体的设施走既有声明。** 世界包格式没有 consumable/facility schema，因此不扩
   包格式；启用 profile 时，入口在实例已有的 `state/world_object_capabilities.json`
   写入一组固定的家用声明（水瓶=解渴、饭团=解饿、家里洗手间=如厕），并用 substrate
   owner 自己的 writer 把水和饭团放到 `home/bedroom`。杯子没有声明，就不是水。
4. **运行开关显式且按进程树。** `start --room-living` 把同一组房间功能开关交给 World
   子进程和本进程的 `PhoneService`；`start --world-actions` 只给这次启动的 World 子进程
   设 `WORLD_BODY_INTENT_MUTATION_GATE=canonical`。未写参数时默认 off 不变，也不写
   任何全局环境。两个开关记在入口自己的 marker 里，同根重开沿用。
5. **可读面。** `status` / `phone` 显示街区名与 id、虚拟身体是否启用及档位
   （不显示 0..1 原始值）、本地动作/房间链是否开启、手机工具入口；`phone` 从真实的
   `/api/phone/state` 取世界与身体块。`today` 在还没开始过一天时仍是
   `CANONICAL_STATE_ABSENT`，不伪装成空白一天。
6. **验收。** `scripts/living_entry_demo.py` 在全新隔离根里走真实 `init` → 前台 `start`
   → 真实 `chiyo_phone` 工具（world / wallet / inventory + 一条 POSE）→ 正常停止 →
   同根再启动。它证明的是入口和工具到 owner 的接线；脚本选择不是模型选择，
   0 产品模型请求。

## Alternatives considered

- **把水瓶/饭团/厕所写进 `daily_neighborhood_v1` 包。** 包里没有 consumable/facility
  字段；加字段就是新 schema，而且 `apply_interiors_to_package` 会重建室内对象。
  否决：使用既有的实例能力文档，不扩外部格式。
- **让入口自己做长期后台服务（no `--foreground`）。** 后台子进程在演示父进程活着时
  会变成僵尸，`stop` 的 pid 探测会超时。验收改用入口自己的前台模式，由演示进程
  直接 `wait()` 回收；用户平时仍可用后台模式。
- **`--room-living` 顺带打开本地写门。** 看起来省一个参数，但会把「房间链」和
  「世界写权限」合成一个开关，违背默认 off 和最小权限。否决：两个显式开关。
- **已有 root 上直接换包。** 会把世界身份、substrate、经济与已有位置混在旧数据上。
  否决：只允许新 root，已有 root 返回具名原因。

## Consequences

- 新用户可以用一条带参数的命令得到「真实街区 + 可选身体」，并在 `status` 里看见
  包、档位和工具入口；旧实例与不带参数的行为逐字不变。
- 家里有正式声明的三件虚拟供给，但吃/喝/如厕仍走正式 Decision 与 owner 回执；
  入口不排程、不强制行动，`NO_ACTION` 合法。
- 虚拟需求仍是虚拟倾向，不是人体测量；停机间隔不补算（见
  [虚拟身体需求](2026-10-27-body-lived-needs.md)）。
- 本地动作只对显式 `--world-actions` 的这次进程树开启；默认 gate、Memory 准入、
  预算和世界包格式都没改。演示里的路线/姿势是脚本替身，不代表自然选择，也不构成
  G1/24h 证据。
