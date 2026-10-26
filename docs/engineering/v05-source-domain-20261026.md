# v0.5 来源域窄链：来源拒读不跨域伪造方法退休

2026-10-26。离线、零产品模型；真实 Supply / M0 Memory / Practice / Sharing owner 与
AG-0/AG-1/AG-2/AR-0，Decision 与内容为脚本替身。

这一条只回答一件事：作品来源读被拒时，分享域的正文扣留与拒发是**来源域**的事实，
不能自动变成 Memory 证据不可用或 Practice 方法退休。链上依次是：

1. 真实 Decision 写出作品并提案做法（TENTATIVE），下一正式 `CONTINUE` 用真实可回忆
   经历给出 `HELPED`，owner 推进 TRIAL。
2. 正式 `prepare` 形成绑定当前作品来源的草稿 D。
3. 用既有 `SourceRefusingPort` **只**拒该作品来源的 `read_project`（故障注入，不是撤权，
   不动 Memory controls/delete）。下一完整正式请求里 D 的 `text_withheld=true`、
   `text_available=false`、原因为 `SOURCE_GOVERNED`；sentinel 不在请求、bounded、practice
   或 owner 读回答案里；显式 `send_local` 由 owner 拒，D 的 head/state/receipt 零变。
4. 同一请求里 M0 经历仍可读、Practice owner 的 `method_state` 仍是 TRIAL；
   作品来源权限没有被当成 Memory 证据权限，也没有自动退休做法。本轮实跑该受限回合
   方法行确实存在（1 条，`method_state=TRIAL`），不是条件断言空真；已证的边界是
   owner 仍 TRIAL、未自动退休，不据此声称本轮能命名同一结果或补齐整条因果。
5. 撤掉注入后，下一合法无 sharing 的 `NO_ACTION` 与读侧重读都从 owner 重新拿到 A/D 正文，
   D 的 state/head/receipt 不变，零自动 send/drop/rebind；这只是暂时故障恢复，
   不称撤权被逆转或删除复活。

边界：不新增生产字段、权限合同或治理规则；不要求多 PID/归档/CAS/退休全矩阵；
不证明真实模型自然采用、G1、V-C 恢复、24h 或用户收到。`Decision`/内容替身。

证据与运行：

```text
tests/test_v05_source_domain.py
scripts/v05_source_domain_smoke.py
```

WSL 固定命令（`/tmp/rc104-venv`）：

```text
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -q tests.test_v05_source_domain
PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B scripts/v05_source_domain_smoke.py
```

两条命令实跑 exit 0，19 项 check 全 PASS。夹具根在 `/tmp/nyairo-result-method-fixtures`，
自有服务在 `finally` 关闭，数据保留。

入口证据（worker报告）：A 的 stdout 是19项check，unittest的 `Ran 1 test` / `OK` 在stderr；worker称原test入口未改。父级不采“早前只收stdout”的原因解释，因为原命令已经 `2>&1`，缺行原因尚未定位。父级另以subprocess分离捕获stdout/stderr独立复验：A stderr明确 `Ran 1 test in 9.355s` / `OK`、`EXIT_A=0`，B19项全部PASS、`EXIT_B=0`。最终日志为 `.agents/worker-runs/20261026-v05-source-domain-final-parent-a.log`（另留stdout/stderr分流文件）、`20261026-v05-source-domain-final-parent-b.log`。原日志缺行原因仍未定位，不因本次通过而倒写历史。

来源身份边界：本轮被拒读的 source（`artifact:fff949ef...`）就是 `prepare` 绑定的当前
offered 作品，它与写作品的 receipt 作品（`artifact:fa82d4d...`）**不是同一个 id**；
因此本链证明的是"拒读被点名的当前 offered 来源不跨域伪造退休"，不能替代同一结果
跨版本的完整因果。
