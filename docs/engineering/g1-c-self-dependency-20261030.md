# c 的自依赖三问：依赖指向谁、谁能改那个 head、body/park 为何没上

日期：2026-10-30　性质：只读静态核查，未访问任何 root、未跑测试/服务/模型　预算：141/300 不变

## 一句话

父级正向计算两段哈希均匹配：该条件确实等待原A在已知head之后换版。本次所列自主续写路径在暂停/资格门下不可用，同目标新START被过滤；另找到operator/导入写口，但未穷尽所有自主工具或证明现场存在独立更新者。按现树分支，head依据会短路body/park；c没有记录其当时可用性，因此仍UNKNOWN。

## 一、wait_dep:artifact.caae6cc7703c1f3b.8070662b561fd947 指向谁

公式（`life_wait_basis.py:83-106`）：`_token(v)=sha256(str(v or "").encode("utf-8")).hexdigest()[:16]`；
identity=`_token(_bare(artifact_id))`，`_bare` 只反复剥开头 `artifact:`，不改大小写、不截断；
ref=`"wait_dep:artifact."+identity+"."+_token(baseline_head)`，baseline 不剥前缀、不规范化。

输入来源（`autonomous_life_cycle.py:5515-5536, 5455-5497`）：`artifact_id` 是前台 Activity 的
`StepReadiness.target_ref`（`artifact:<hex>` 或 `artifact:artifact:<hex>`，剥完是同一个裸 id）；`baseline_head`
是 `readiness.source_revision` = `artifact_port.read_head(owner_ref)["version_id"]`，该 id 由 owner 铸成
`"artifact-version:"+uuid4().hex`（`store.py:671`）。wake 里的 `artifactversion:artifact-version:…` 是下游再包一层，
不是 store 存的字符串，不能当输入。

父级已用纯hash命令实际计算，两项结果均匹配（未导入业务模块、未读实例）：

```
sha256(utf8("359e74f506174e9e9577f48c3a56c3d7"))[:16]                  ?= caae6cc7703c1f3b
sha256(utf8("artifact-version:de6374d41f924034bf42768107b88949"))[:16] ?= 8070662b561fd947
```

对上能证明：这条 PENDING 条件绑的就是「原 A 在 de6374… 之后出新版本」，自依赖形状成立。
对不上只能说明这条 ref 不是按此函数这组输入算出来的（可能绑别的对象或别的 revision 字符串），不能反推「没有人能改」。
两种结果都不该写成「哈希所以永远无法确认」。这一步只证绑定，不证依赖是谁写的。

## 二、同作 head 能被谁改

- Life CONTINUE：`artifact_port.commit_version` → `commit_markdown`（`life_artifact_port.py:545-582`，调用
  `autonomous_life_cycle.py:9429`）。API 有，需 grant + expected_head CAS + artifact ACTIVE；自主可达，但同 revision
  被 `WORK_NO_CHANGE_CONFIRMED` 撤掉 CONTINUE；这是她自己的写口，不是独立更新者。
- 同作新 START：API 有（仍是 commit），但 A 一旦 PAUSED/WAITING，`artifact_continuation_source.py:407/855/332-336/786-794`
  一律 `PAUSED_TARGET_REQUIRES_RESUME` 撤回候选 → 自主不可达。
- owner 服务直调 `commit_markdown`（`store.py:608-682`）：有，需 `grant_reservation_id`、subject 必须等于授权 subject、
  expected_head 必须等于当前 head；不在 Life 决策链，属 operator 写口。
- 外部导入 `import_projection`（`life_artifact_port.py:451`；service 层 grep 命中 `life_supply.py:1027/1141`，未通读该文件）：
  有，把投影文件的外部改动作为该 artifact 新版本提交；调用方是 scripts 下 operator/演示 CLI，Life cycle 不调。
- 新建 artifact：只产生新对象，不改 A 的 head。手机 `work_context`/`work_version`（`phone_tool.py:208-242, 1029-1063`）
  只读。材料更新、world/body 事件不在 artifact head 命名空间，改不了 A。

限定结论：已核的原Activity续写和同目标新START不能越过现有门；operator commit/import是具名的其他写入能力，但存在API不证明本批有人会调用。未穷尽其他自主工具、所有主体或未来显式操作，不能把扫描结果升级为“只有CONTINUE”或永久死锁。A起止2版/head相同和87条暂停条件/资格采样是不同证据，不把这87条说成87次head读取。

## 三、body / park 在 r23 为何没成为最终绑定

顺序（`autonomous_life_cycle.py:5955-5956, 6277-6287`）：`wait_basis` 只产出 work/reading 依赖；`park` 仅在
`wait_basis` 为空时才计算（5956）；body 仅在 `wait_basis is None` 或 `source==PARK_SOURCE` 时才调
`body_wait_basis`（6278）。r23 的 source 是 `WORK_HEAD_AFTER_NO_CHANGE`，所以 body 与 park 都被短路，**根本没算过**。

因此二者在 r23 的可用性都是 UNKNOWN：不能从 fallback 存在推当时可用，也不能从 head 先命中推模型在几个里挑了一个。
模型当轮只能点名一条：`pause_condition_view` 只吐一个 `condition_ref`（2098-2150），AG-1 要求 hint 严格等于它。
park正式绑定所需的同回答`later_item`/`park_current=true`与时刻是静态规则定位；c已导出材料没有完整回答，不能据此断言当轮绝对没有这些字段。

附带静态发现（不施工）：`body_wait_basis` 每轮开头会清空 `last_body_wait_basis`，但短路轮根本不调它，
`body_breakpoint_view` 可能读到上一轮残留。所以视图里出现过 body 可用也不能当 r23 证据；后续某轮 fallback 可用，
也不改变已绑定的旧 PENDING 条件——LR-4 按 ref 精确匹配。

## 风险结论（带条件）

条件确绑原A/head已由父级正向匹配；原A采样中PENDING/资格0、起止head相同也已证。现有扫描支持“等待自己作品变化，而已核自续路径受阻”的结构风险，不证明所有自主写法或独立更新者均不存在，更不证明永久死锁。下一步规则讨论应回答是否允许主体重新审视自己选的依赖，而不是把外部API存在当自动解锁或把TTL过期当依赖满足。

父级核查说明：worker到30轮exit1，三产物已落盘；只采能核的有限事实，不采JSON自报COMPLETE。原引用lifysupply_real路径拼写错误，存在的是lifesupply_real；父级仅核路径存在，不冒逐行复审该镜像及其生产加载身份。本单0产品/0测试服务，无新增施工授权。
