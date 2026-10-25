# 2026-10-22 私人相册：父级独立验收

## 结论

**EP-16 私人相册本块验收通过**：真实本地PNG/JPEG选择→浏览器解码预览→保存到真实Supply owner→列表与大图→说明和自述来源→刷新→完整停服并同根重启后回看，均由父级实际Microsoft Edge155验证。不是fixture种图冒认产品；验收图片是通过真实文件输入导入的两张浏览器截图。通过仅限这一块，不代表EP-16全部完成、真模型图像理解或全项目完成。

用户要求更换故障模型后，全局Haiku/Sonnet/SMALL_FAST三条模型env改为`workbuddy-wg/cn:deepseek-v4.1-flash`，JSON验证通过，主会话模型与权限规则未改。当前运行环境映射仍空，不能断言桌面审批请求实际采用了此路由；后续preview_start确实成功，未绕过审批。施工确实使用指定国内DeepSeek，最后一轮关闭fallback，无自动换模型。

监工未写业务代码。未stage、未提交、未推送、未部署。EP-09双旧备份恢复仍FAIL，其他手机应用、EP04/19～22不扩施工。

## 派工与失败历史

- 两组只读探索与一组具体设计经dispatch，见`v04-album-20261022-plan.md`。
- album-build首版exit0，父级初验Supply137/HTTP7/Node131通过；审查发现GET闸门、伪清单读取/重放权限、fixture删socket缺口，未接收。
- 首轮权限返修遇worker503和执行壳502；用户要求重试后确认无残留writer，再按原模型原任务重派，exit0。日志`.agents/worker-runs/20261022-171942-album-review-fix-retry.log`。
- 浏览器首次真实导入两张成功后，实际图片URL漏`/api/phone`返回404；旧Node恰好断言错误相对路径，不能用其绿冒认浏览器通过。父级停止自有preview后按`20261022-album-image-url-fix-task.md`四文件写集派唯一国内DeepSeek。
- 最后返修dispatcher `b2o0ukx4q` exit0，模型`workbuddy-wg/cn:deepseek-v4.1-flash`，fallback关闭、no-probe；日志`.agents/worker-runs/20261022-182410-album-image-url-fix.log`。增加`albumImageSrc`仅为实际img补API_BASE，metadata request仍只补一次前缀，列表和详情共用。新增stub DOM boot/列表/详情真实img属性断言。worker报告负控红→绿，父级独立验证最终树，不冒认亲自跑了修复前红树。

## 实现边界

图片由现有Supply ManagedArtifact owner的实例media目录管理；不可变首版清单引用字节，复用grant、subject、UoW。不新增canonical表、owner、数据库或capability，不改旧文本/单parent语义。PNG/JPEG≤5MiB，相册独立窄传输，旧op限额保留。

读取核验真实CREATE operation与既有Governance CONSUMED reservation/action_digest；复制manifest或hash不授读取权。replay核同归属、ACTIVE、原导入绑定、当前grant；撤权/tombstone不补文件。GET沿原Host/Origin/Sec-Fetch闸门。fixture每次新socket generation，同根仅更新live绑定，不重seed、不删socket，未知目录拒绝。

不自动识图、生成、抓取、外发或写Memory，不声称亲历；说明来自用户填写。文件+SQLite无跨介质原子事务，允许孤儿、不GC、不擦EXIF；签名/浅结构检查不是完整解码或净化。

## 父级独立自动化

后端权限返修最终树，WSL Python3.12.3、`/tmp/rc104-venv/bin/python -B`、`PYTHONDONTWRITEBYTECODE=1`：

1. 仓库根`-m unittest -v tests.test_v04_album_images`：**8 tests / 8.169s / OK / exit0**，输出`bhksjzxrv`。真实Supply socket+Phone HTTP，覆盖字节/大传输/CSRF/注入/GET来源闸门/幂等/项目排除/tombstone/同根停启。
2. `components/supply`下`PYTHONPATH=.`、`-m unittest discover -s lifesupply/tests -q`：**142 tests / 91.232s / OK / exit0**，输出`b0uyk0gjx`。含相册20项。原fork多线程DeprecationWarning保留。
3. 相关Phone API/项目API/旧作history/writer四模块：**41 tests / 109.845s / OK / exit0**，输出`busducxj6`。
4. URL修复后父级Python展开7个`tests/test_phone*.mjs`交Node：**133 pass / 0 fail / exit0**，输出`b7b2nohko`。含相册12项与实际DOM img src接线。不是仅worker自述。
5. URL修复后父级在仓库根执行notes tree/format/archive：**82 / 82 / 0**，exit0，输出`blhqjpko1`。此前误在D盘根运行得到0篇，虽exit0但不算门禁通过，已纠正；没有掩盖。
6. 本块diff --check通过，index空，具名MANIFEST/db.py/contract.py/OWNERSHIP_REGISTRY无diff。此前本块9个Python源AST通过。URL修复未动后端，不重复已绿后端套件；全量scripts/test.py未跑，不声明全库绿。

具名权限负控：
- `lifesupply.tests.test_album_images.AlbumOwnerTests.test_foreign_subject_cannot_serve_another_import_by_copying_its_manifest`
- `test_same_subject_cannot_revive_a_tombstoned_picture_by_copying_its_manifest`
- `test_replay_from_another_subject_is_refused_and_serves_no_bytes`
- `test_replay_after_grant_revocation_writes_nothing`
- `test_replay_after_tombstone_does_not_restore_the_media_file`
- `tests.test_v04_album_images.AlbumPhoneTest.test_album_reads_pass_the_same_host_origin_and_fetch_guard`

## 实际浏览器证据

所有服务通过preview_start/preview_stop，不用Bash或peer绕审批起服务。相册隔离根始终`/tmp/nya-album-parent-20261022-1525`，3818；不动旧产品实例、3816或旧PENDING。

1. 实际Microsoft Edge **155.0.4283.62 headless**，390×844。浏览器截图产生真实PNG/JPEG，通过`input[type=file].setInputFiles`选择，img自然宽高>0后填写说明并点击保存。说明分别“PNG 浏览器验收截图”“JPEG 浏览器验收截图”。初始album_count=0，保存后2张；这些是测试输入，不是产品内置内容或图像理解证据。
2. URL修复后preview server `7b17127f-4932-4ec1-80de-a39dc22361f7`，bindings=reused、album_count=2。**没有重导**。两图列表、两图详情均解码为390×844；实际GET `/api/phone/album/<id>/image`分别200 `image/png`/`image/jpeg`，详情200。PNG id `artifact:602653ade48749d3abf08df9686576a0`；JPEG id `artifact:82e1ab7d39b54117bae8a8f4642d31a6`。
3. 详情显示原说明与“你从本机导入（自述）”，明确不代表看过/理解、不自动写Memory。页面reload后原两图仍解码正常；sessionStorage会恢复相册页，验收脚本据实际状态检查，而非误等主屏按钮。
4. 选择坏PNG后显示“这张图片预览失败”，保存按钮disabled；选择GIF后具名拒绝，保存disabled。取消返回仍共2张，没有新增导入请求或记录。
5. 390px下grid为176px+176px；375px下168.5px+168.5px，无水平溢出。preview pane的隐藏布局曾报告0宽，不作布局证据；以上取实际Edge可视viewport的computed styles。
6. preview_stop完整停止，再preview_start同配置同根；第一次立即重起报端口占用，随后只读核对无进程/监听，再正常preview_start成功。新server `06140030-b09f-4066-9ea8-97d900203e58`：**reset=no、bindings=reused、album_count=2**。全新Edge context于375×812再次读取同两id、原说明、图片自然尺寸与来源，均通过。没有新建根或seed替代恢复。
7. 实际图片头200、正确mime、nosniff、no-store、CORP same-origin。相册请求无失败，pageErrors=[]，无CSP错误；console另有`/favicon.ico`404，不声称console全空。preview服务启动时还记录`/api/phone/state`的BrokenPipeError及错误响应再次写断开的连接；相册操作及重启回看未失败，该日志缺陷未在本块修复。
8. 已preview_stop最后自有服务；后续验收Edge均finally关闭。最初globalThis跨调用试验引用未保留，该次browser关闭未确认，不冒认全部早期资源已清理；不广泛杀用户进程。隔离根和两图保留。

验收脚本失败也保留：最初profile in use；globalThis跨调用不持久TypeError；第二次保存后说明同时在结果卡/列表导致strict locator歧义；修复前图片404；修复后脚本误等reload后主屏按钮超时。后续以具名阶段、DOM实际状态重验成功，不把失败倒写为首次成功。

截图（已发送网格及重启后截图）：
- `.agents/worker-runs/20261022-edge-album-grid.png`
- `.agents/worker-runs/20261022-edge-album-detail.png`
- `.agents/worker-runs/20261022-edge-album-jpeg-detail.png`
- `.agents/worker-runs/20261022-edge-album-after-restart.png`

## 验收分域与停点

V-A：相册subject/伪清单/来源闸门；V-B：幂等、刷新与同根完整停启回看；V-E：本地导入相册完整体验，均在上述范围内通过。V-C只证当前tombstone和重放扣留，**EP-09双旧备份恢复仍FAIL，未承诺跨备份防复活**。V-D不做认知因果/图像理解断言。真实产品模型calls=0，不是resident/24h或生产部署。

本轮相册已验收，旧作限定链已验；EP09受阻原因已明确。按原收尾线停止，不自行开展通讯录/日历/消息/商店或其他阶段。下一阶段须用户另选；本报告不把局部通过扩大成全项目完成。
