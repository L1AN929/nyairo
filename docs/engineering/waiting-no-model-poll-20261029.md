# 长逻辑跨度的等待：维护零模型，只发资格，不自动恢复

这是 v2 总纲 14§5 场景 3（等待结果时不空转）在既有机制上的**用法证据**，不是新机制。
没有新增等待、调度、预算或 TTL 政策，业务源码本轮零改动。

## 现在可以跑什么

```bash
# 完整链（真实 Supply owner + 真实 runtime + 正式 runner 维护入口）
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/waiting_no_model_poll_demo.py

# 只钉两处最小契约（upkeep 判定 + runner.maintain 透传），不重复跑整链
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest -v tests.test_waiting_no_model_poll
```

demo 全部 CHECK 通过时以 `WAITING_NO_MODEL_POLL_DEMO_OK` 结束、exit 0；任一 CHECK 红即 exit 1。
夹具根在 `mkdtemp` 下，跑完保留（不 cleanup / 不 rmtree / 不删 socket；owner 用 drain +
自己的 socket guard 正规停）。

## 这条链实际证明了什么

1. **真实 WAIT 绑的是 owner head。** 正式 START 作品 A → 写者回 `no_change` → AG-1 选
   `WAIT` → AG-2 给 `waiting_spec`，Activity 仍同一 id、状态 WAITING，LR-4 里一条
   `PENDING` 条件，`parameters.baseline_head_ref` 就是 owner 当时的 head，`expires_at` 为空
   （依赖条件本来就没有截止时间，这里没有为凑绿去延长任何 TTL）。
2. **逻辑时间跨 1h / 6h / 24h 的维护不花请求。** 每次 `runner.maintain` 都真实重读 owner
   （`checked == unchanged >= 1`），head 没变，条件仍 PENDING；`llm_calls == 0`、
   `changed == 0`、无新资格、无新 Action、无新版本；单次维护前后预算快照不变、脚本认知
   调用数与内容调用数都不动。
3. **正常重装配后等待还在。** 正规 stop/drain/close 后用同一根目录重新装配，WAITING 的
   Activity、停点、revision、条件 id 与 baseline 都读回来。**这是同一进程（同 PID）内的
   重装配，不是跨进程重启**，demo 里直接把 PID 打出来了。
4. **只有真实换版才发资格，且只发一次。** 另一个真实写者经 CAS 推进 owner head 后，一次
   维护恰好产生一条 `OPEN` 资格，Activity 仍 WAITING；再维护两次不重复、不花请求、不写版本。
5. **NO_ACTION 保留等待，而正常认知不是零成本。** 资格 OPEN 期间一次 `NO_ACTION` 回合是
   正常认知回合：`llm_calls == 1`、脚本认知调用 +1，等待状态与 OPEN 都保留。demo 把
   「维护花了 0 次」和「正常考虑要花 1 次」分开计数，没有用 `act=False`／预算 0／禁模型
   去凑零调用。
6. **新鲜 Decision 才恢复一次。** RESUME 回到同一 Activity id，资格变 `CONSUMED`，
   `expected_head` 等于换版后的新 head，写出的新版本号严格递增、无重放。
7. 无关作品 B 全程 head 与版本号不变。

## 明确没有证明什么

- 不是 G1，不是 24h soak，不是真实一天：逻辑时钟是用参数注入的，没有等任何墙钟。
- 不是跨进程重启（同 PID），也没跑 crash／半写。
- 不是真模型自然采用：Decision 与内容都是脚本替身，0 次真实产品模型调用、0 外网。
- 没有覆盖资格过期、暂停（PAUSED）条件、身体/时间类条件；这些走各自既有链。

## 代码位置

- `scripts/waiting_no_model_poll_demo.py` — 整条链与逐项 CHECK。
- `tests/test_waiting_no_model_poll.py` — `quiet_upkeep` 判定（“没检查”不算“零调用”）与
  `AutonomousLifeRunner.maintain` 的 `observed_at` 透传、`llm_calls` 如实上报。
- 维护入口本身是既有 `AutonomousLifeCycle.maintain_waiting`（`autonomous_life_cycle.py:2232`），
  `AutonomousLifeRunner.maintain` 只是逐字转调；demo 因为共用夹具只交出 `cycle`、不交
  runner，才在本进程按装配同样方式把 runner 建在同一个 cycle 上。
