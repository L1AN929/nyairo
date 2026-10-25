# v0.4 旧作重访收口 · worker 结果（2026-10-21）

本单只动测试、夹具脚本、笔记与本文；业务源码、服务端契约、落盘格式零改动。
浏览器点验未由 worker 做，留待监工用 `preview_start` 独立点；本文不声称 Edge 通过。

## 改了什么

| 文件 | 性质 |
| --- | --- |
| `scripts/phone_work_revisit_owner_fixture.py` | 新增。真 Supply owner + 真 PhoneService HTTP 的 preview 夹具，seed「夜航手记」三版 |
| `tests/test_v04_work_revisit_runtime.py` | 新增。真 owner Life 写作链 + 两 PID 同根重启，模块级 `load_tests` 只收这一条新用例 |
| `.agents/notes/implemented/testing/2026-10-17-cross-process-life-restart-harness.md` | 补当前落地测试事实与边界 |
| 本文 | worker 交付记录 |

## 实跑命令与结果

仓库根在 WSL 下为 `/mnt/d/nyairo-rc1-body-backend`。

1. 新用例：

   ```bash
   PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v \
     tests.test_v04_work_revisit_runtime
   ```

   结果：`Ran 1 test ... OK`，17.273s（连续三次通过，含两次候选顺序不同的窗口）。
   输出只有新用例，未带继承的 W05 套件。临时根由既有 `DScenarioBase` 夹具在 `/tmp/w05inst-*`
   下创建；worker 未手工删除、未复用旧根，也没有清理别的会话留下的根。

2. 相关回归：

   ```bash
   PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -q \
     tests.test_v04_work_revisit_history tests.test_v04_work_revisit_writer_chain
   ```

   结果：`Ran 21 tests ... OK`，17.522s。

3. 前端：`node --test tests/test_phone_projects_ui.mjs` → 13 pass / 0 fail。

4. 夹具冒烟（WSL，端口 3818，root `/tmp/wr-preview-check-18001`）：

   - stdout：`base_url` / `root` / `subject` / `artifact_id` / `title=夜航手记` / `versions=3`；
     未打印 CSRF。
   - `GET /api/phone/projects/<work>/versions` → 200，`total_versions=3`，版本 `[3,2,1]`，`complete=true`。
   - `GET /` → 200 `text/html; charset=utf-8`。
   - SIGTERM 后端口 3818 已关闭，stderr 0 字节；脚本只关自己的 HTTP server 与 owner。
   - 改掉一处 `__import__` 写法后又在 3819 / `/tmp/wr-preview-check-18002` 复验启动与停止：
     stdout 元数据齐全、SIGTERM 后进程消失且端口关闭。既有根（18001）被清晰拒绝，未覆盖。

5. 静态与门禁：

   - 两个新 Python 文件用 `ast.parse` 解析通过（未用 `py_compile`）。
   - notes tree 校验：`ok: 81 note(s) tree and relative links verified`。
   - notes format 校验：`ok: 81 note(s) verified`。
   - `git diff --check`（笔记）rc=0；两个新增文件用 `git diff --no-index --check /dev/null <file>`，
     无空白错误输出（no-index 因文件差异本身返回 1，不是空白告警）。

## 链上的实际事实（最后一次运行）

worker 在用例末尾打印了一行非敏元数据（无正文），关键值：

- 两个子进程 PID：`19560`、`19720`，均不同于父进程；两次 `runtime.close()` 后
  `lease_released=[true, true]`。
- run identity：`run:instance`，两进程相同；`utc_day_calls`/`utc_day_max`/`run_window_max`
  跨进程延续，未重置。
- 同一 Activity：`actv_cdef234d1e64b447`；worker A 首轮 `START` → `ACTED`，worker B 暂停轮
  `PAUSED_BY_OPERATOR`（0 请求、0 写入），resume 后 `CONTINUE` → `ACTED`，Activity 仍 ACTIVE。
- 版本：写作前 head `artifact-version:05c3707f...`；worker A 后 `...10b0ae60...`；worker B 后
  `...3ea99f79...`。
- 上一版 sentinel：`artifact-version:aff8b6f1...`（version 3，带 `SENTINEL-OLD-1`）。
  worker A 的真实请求 `earlier_versions=[{version 3, has_sentinel=true}]`，
  `tail_of_current_text` 不含 sentinel；worker A 写出新版本后，worker B resume 的
  `earlier_versions` 是 worker A 写作前的 head（version 4），不是 worker A 新写出的
  head（version 5），也不再含 sentinel。
- 派生作品：`artifact:8d6499f9...`，重启后来源仍指向原作品与 version 3；同一 operation key
  重放返回同一 artifact，版本数保持 1。
- 具名拒绝：用同 subject 的另一个 artifact 读该版本，owner 拒绝 `VERSION_NOT_IN_ARTIFACT`。

## 未验证

- 浏览器点验（Edge/Playwright）未由 worker 执行；夹具只证明真 HTTP/真 owner 可起可停。
- 没有真实产品模型请求：decision 是确定性 `WorkLoopback`，content transport 是
  `RecordingClient`，只证明请求体由 `_next_content` 自动组装，不证明真模型采用。
- 仍是单步 `evaluate_once`，不是 resident 循环；24h/长跑、调度背压未涉及。
- 未跑全库 `scripts/test.py`，不称全绿；只跑本单验收与相关回归。
