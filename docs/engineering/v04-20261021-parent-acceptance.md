# 2026-10-21 v0.4 监工独立验收与停点

## 结论

旧作重访四项在**隔离真实本地 owner＋确定性决策/内容transport**的范围内收口：UI已有；Microsoft Edge真点验通过；历史片段经正常Life链进入真实writer请求；两个实际Life进程同根续同Activity通过。不是自然模型采用、resident长跑或G1全闭合。

EP-09当前删除/更正扣留与具名撤权拒绝通过；整体旧备份恢复防复活**FAIL / BLOCKED**。不新增回滚域外控制权威、不改契约、不把限制文档化冒认V-C通过。

手机相册/通讯录/日历/消息尚无选定的产品范围，也没有可直接复用的相应服务端契约；未施工。EP-04、19～22均未施工。不提交、不推送、不部署。

## 第〇步与派工边界

四组只读核对见 [状态核对](v04-20261021-ep-status-check.md)。先更新NOW，再派工。

旧作worker写集：新增 scripts/phone_work_revisit_owner_fixture.py、tests/test_v04_work_revisit_runtime.py、结果报告；仅更新既有restart测试笔记。V-C worker只写忽略目录probe及独立报告，两写集完全不相交。父级只写状态/工单/验收/检查点和preview启动配置，没有写业务代码。

过程中RUNBOOK.md与scripts/dispatch_worker.py出现独立修改，peer会话“你好吗”明确认领；保留不碰，不纳入本轮交付。memsearch的日记/index状态/pid变化全部保留，未stage。

## 独立验收（父级实际执行）

### 旧作与前端

- WSL `/tmp/rc104-venv/bin/python -B -m unittest -q tests.test_v04_work_revisit_history tests.test_v04_work_revisit_writer_chain`：21例，17.748s，exit0。
- `node --test tests/test_phone_projects_ui.mjs`：13/13，exit0。
- `tests/test_phone*.mjs` 全部6文件：121/121，0 skip，exit0。
- 新链 `tests.test_v04_work_revisit_runtime`：第一次后台命令exit1但仅留下exit行，没有错误正文，原因未定，**不倒写成通过**；紧接的前台独立运行 `Ran 1 test in 17.923s ... OK`、`PARENT_RUNTIME_EXIT=0`。未改源码后复跑通过；不以此声称已证明无偶发失败。

新链父级观察：两PID 21357/21573，`run:instance`，两次lease释放true；同Activity `actv_a74239bc08ebd637`。首轮START→ACTED，暂停新进程0 cognition/0 writer请求、head不变；resume CONTINUE→ACTED，同Activity新owner版本。当前head不含历史sentinel，首轮请求earlier_versions指向真owner version3、has_sentinel=true。续接请求引用的是写入前head的version4（上一版），**不是worker A新写出的head version5**。派生作品仍为一版，来源保留，同key重放没有新增作品/版本。错误artifact读版本具名VERSION_NOT_IN_ARTIFACT。

### 真实浏览器

服务只由preview_start启动：配置 `phone-work-revisit-owner`，3817；真实Supply socket owner＋PhoneService，不是内存owner。独立根 `/tmp/nyairo-work-revisit/work-revisit-preview-21936-1792595396394460378`，subject `test:work-revisit`。既有UI demo和3816夹具均未停未改。

先在app内置Chromium点：私人项目→夜航手记→全部三版→v1→和当前v3对比，正文新增2行、字数+40；派生“夜航手记（监工派生）”真实落库。preview_click在当前pane坐标归因失败，改用preview_eval派发DOM click（交互而非修改实现）；fill正常。console无error，network无失败请求。preview截图后续两次超时，没把它说成成功。

再用Playwright `browserType().launch({channel:'msedge',headless:true})` 启动这台机器实际Microsoft Edge，版本 **155.0.4283.62**，UA包含 `Edg/155.0.0.0`，不是把内置Chromium叫Edge。完整重新走同一路径并派生“夜航手记（Edge验收派生）”：

- v1正文预填真实sentinel；比较显示 `v1 → v3`。
- 保存派生POST 200，自动打开新作品详情；新artifact `artifact:d0b2c3f69db14210b07dbc80354a2652`。
- 实际GET项目列表200：3件（原件＋Chromium派生＋Edge派生）；不是同一请求重放造成重复。
- 原件仍3版，head仍 `artifact-version:d15455de17fe41679bf09d3ca9ad2e32`。
- Edge派生来源仍是原件v1，provenance verified=false，保持“首版自述标记，不是来源认证”的契约。
- Edge pageerror为空。截图 `.agents/worker-runs/20261021-edge-work-revisit.png` 已由父级打开检查并发给用户。
- Edge浏览器在finally关闭；自有3817服务用preview_stop关闭。隔离根保留，不清旧数据。

实际Edge首个定位器尝试因按钮名正则不匹配超时，随后按DOM实际文本修正定位器再完整走通；没有改产品代码或重复提交失败动作。

### Memory生命周期与撤权

- `tests.test_v04_memory_product.GovernanceTests`：2例0.726s，exit0。
- 独立复跑 `.agents/worker-runs/20261021-memory-vc-probe.py`：exit1，`V_C_RESTORE=FAIL`；根 `/tmp/vc-probe-22211-95419721`，所有副本保留。
  - 基线可读，DELETE后read/list/context均扣留。
  - 只恢复旧evidence、保留最新controls：仍扣留。
  - 旧evidence＋旧controls一起恢复：READY，正文重新可读。
  - controls缺失/损坏：BLOCKED，具名原因MEMORY_CONTROLS_DB_MISSING/MEMORY_CONTROLS_UNHEALTHY，不报空库。
  - CORRECT当前旧正文扣留true，不等于更正后的跨备份恢复认证。
- `tests.test_artifact_closeout_v04.ArchiveCloseoutOwnerTest.test_archive_without_a_usable_grant_is_refused_and_changes_nothing`：1例1.788s，exit0。只证明archive派发前撤权拒绝、无副作用，不代表所有披露/缓存撤权链。

详细worker报告见 [V-C实证](v04-memory-vc-20261021-result.md)。实现跨备份防复活需新的控制权威或恢复契约，超出当前授权；本轮按明确阻塞停，不修改设计基准、不默许旧备份安全。

### 工程门禁

父级实跑notes tree/format：各81篇exit0；archive0篇exit0。两新Python AST解析通过；本轮指定tracked文件diff-check通过。不是全量scripts/test.py/host-boundaries/cognition-faults全绿声明。新测试只是单步受控链，不证明完整自然生活、认知因果、24h、低预算或生产接线。

## 停点与下一步

本轮不再找新活。旧作重访限定体验已完成；V-C整体恢复防复活阻塞有实证；手机剩余件待用户产品选项及相应契约授权，未开新战线。下一阶段可选：只收文档停；另开完整手机体验；或另批设计恢复控制边界。用户没选前均不实施。
