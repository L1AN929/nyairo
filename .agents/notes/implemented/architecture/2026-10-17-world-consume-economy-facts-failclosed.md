# Agent Note: 消费的可食判定要求「经济事实可证」——坏的 economy 文件不等于没有 economy

Status: implemented

## Problem

`CONSUME` 的可食判定要回答一句否定命题：「这个 instance **不是**经济物件」。这句话只有在经济事实确实可读时才成立。修复前的两条路径都把它当成了默认可答：

- 网关的候选投影 `except Exception: return {}`，把「读不到」洗成「空经济」；
- resolver 走 `world_body_binding.load_economy()`，其 `_read_json` 把 `OSError`/`ValueError` 统一 `return None`，同样洗成「没有经济」。

于是父级探针把一个截断文件 `{"items":` 写到 `state/world_property_economy.json` 后，候选仍然是 `available=True`，`submit CONSUME` 返回 `applied` / `applied_to_body=True`，substrate 真的被改了。这不是「恰好没被吃掉」的小概率问题，而是**坏输入被当成了健康默认**：崩在写一半的经济文件，等于临时变成「所有物件都不是经济物件」的通行证。真实世界里那正是最需要保守的时刻（owner 刚被写坏）。

区分必须成立，因为两种情形在语义上完全不同：

- **根本没有经济 owner**（文件不存在）：世界没有经济，所以「不是经济物件」可证，世界声明的免费/已持有可食物件应当照旧可用；
- **有经济 owner 但读不动**（权限/OS 错误、非普通文件、超界、非 UTF-8、JSON 损坏、root 不是对象、过不了经济 schema）：什么都证不了，本次消费必须拒绝。

## Decision

规则收敛到 `world_consumable_contract` 一处，`economy_facts(hermes_home)` 返回三态：

| status | 何时 | 对 CONSUME 的含义 |
|---|---|---|
| `absent` | `os.stat` 抛 `FileNotFoundError` / `NotADirectoryError` | 没有经济 owner，可证「非经济」→ 保持既有行为 |
| `present` | 文件存在且 `world_property_economy.validate_state()` 接受 | 只有这时才允许出现 `ECONOMY_OBJECT`，也只有这时才有经济物件 |
| `unreadable` | stat 其他 `OSError`、非普通文件、`st_size > MAX_ECONOMY_BYTES`、非 UTF-8、JSON 损坏、root 非对象、schema 拒绝 | 不可证 → `ECONOMY_FACTS_UNAVAILABLE`，本次消费拒绝 |

具体约束：

- **只读、有界**：一次 `os.stat` + 一次带上界的二进制读（`MAX_ECONOMY_BYTES = 4 MiB`，读到超界也算不可读，绝不截断后当真解析）。不开锁、不建目录、不迁移、不修复、不写回。
- **复用 owner 的 schema**：结构校验只调用 `world_property_economy.validate_state()`，本模块不重写经济 schema、不建第二份经济真相、不改 `world_property_economy` 与全局 binding。
- **两个入口同一规则**：网关候选投影用 `_economy_facts()`（坏 → `available=False`、`blocked_reason="ECONOMY_FACTS_UNAVAILABLE"`、`candidates=[]`，并带 `detail`），resolver 用同一个 `economy_facts()` 并在 verdict 里先判经济事实、再判声明；两侧都不再出现 `except → {}` / `except → None`。
- **拒绝理由可分辨**：verdict 码 `ECONOMY_FACTS_UNAVAILABLE` 映射到 `NOT_CONSUMABLE` + `legacy_reason_code` 前缀，和「未声明」「不可食」「经济物件」都不同；网关层则因候选为空按既有暴露策略返回 `NOT_EXPOSED`（零变更），两者都明确拒绝。
- **不因持有而豁免**：手里拿着的食物同样要过这条判定——否则「先拾起再吃」就成了绕过坏 economy 的后门。
- **范围**：只收紧 `CONSUME` 这一条判定。别的 World 动作（MOVE/PICK_UP/PLACE/POSE、经济自身写入）本轮不扩大审计面，行为未改。

## Alternatives considered

- **把所有失败一律当 `absent`（维持旧行为，只加日志）**：改动最小、对旧 home 最友好，而且「读不到就不要更严格」听起来像保守。但它正是本缺陷：它把不可读的 owner 当成不存在的 owner，于是坏文件成了免费通行证；记一条日志也不会让拒绝发生。否决——权限问题必须影响许可，不能只影响日志。
- **在 `world_consumable_contract` 里写一份最小经济 schema（只看 `items[].item_instance_id`）**：比复用 owner 校验简单，也能覆盖「损坏 JSON」。但那会变成同一份文件的第二种解释：owner 认为非法（重复 id、金额为负、交付对不上）而这里认为「能读出 items 就算数」，两个判断迟早分叉。否决——同一 owner 的结构判断只能有一个来源。
- **让 `world_property_economy.load_state()` 直接抛错并在那里改**：最省代码，但那是经济 owner 的读口，`load_state` 是全局共享的（结算、投影、交付都在用），改它的错误语义会波及本包之外的调用者。否决——本包只在自己的判定面收紧，不改别人的读口。
- **把三个入口（候选、resolver、Body）各自兜底**：实现快，但三处判定会漂移，且没有任何一处是「唯一真源」。否决——收敛到 `economy_facts()` 一个函数，网关与 resolver 都只是调用方。
- **坏 economy 就把所有 World 动作一起拒绝**：更「安全」，但会把一次可修的 owner 文件损坏放大成整台世界不可用，且超出父级给的范围。否决——只让依赖这条事实的那个动作失败。

## Consequences

- **收益**：坏输入不再产生许可。父级探针（`parent_world_daily_f.py`）从 `PARENT_F_EXIT=1` 变为 `0`：候选 `available=False` / `blocked_reason=ECONOMY_FACTS_UNAVAILABLE` / `detail="economy state is corrupt JSON"`，提交 `rejected`（`NOT_EXPOSED`，`world_unchanged`/`body_unchanged`），substrate 字节不变、无回执、无 Body 记录。真实存在的合法经济仍按 `ECONOMY_OBJECT` 拒绝；合法空经济、以及根本没有该文件的 home 行为不变（有测试固定这三态）。
- **代价**：每次候选投影与每次提交都会多做一次 `stat` + 一次有界读 + 一次经济结构校验（`validate_state` 上限 256 条 items/transactions，本地内存，无锁无写）。经济文件超过 4 MiB 的家会一直被拒，直到 owner 自己把它缩回界内。
- **已知上限**：`_labels()`（纯展示用的人名映射）仍自己读同一文件并 `except → {}`，读不到只是退回用 instance id 显示，不参与许可判定；`world_object_capabilities.json` 的声明块损坏仍按「无声明」处理（`NOT_DECLARED` 拒绝），本轮未改。跨进程/多写者一致性不在本包范围内。
- **升级触发条件**：若将来经济文件合法地会超过 4 MiB，应把上界改成 owner 声明的常量而不是在这里调大；若别的 verb 也需要「经济事实可证」这个前提，应把 `economy_facts()` 提升为 resolver 的公共前置，而不是各自再抄一份三态判断。
