# ACTIVE 放弃离线实验：夹具修正后正式链跑通（限定）

日期：2026-10-30。仅证明本隔离脚本链；不是出口通用已通，也不是 c 当时合法放弃。

历史（保留）：worker bs1yimkh1 30轮 exit1，test 已落盘；父级实跑 Ran1/0.050秒/FAILED(error1)/exit1，卡在 AG1_EVALUATE——fixture 把 EXECUTABLE_NOW 当 candidate capacity state 传入，AgencyDecisionError: invalid candidate capacity state。那是夹具参数错误，不是业务门拒绝；当时正式 Decision/AG2/LR2/ABANDONED 均未到达。

本次修正：删掉误传的 candidate_capacity_overrides（容量由显式 AVAILABLE 维度计算），链本身不变。实际执行：

```bash
wsl -e bash -lc 'cd /mnt/d/nyairo-rc1-body-backend && PYTHONDONTWRITEBYTECODE=1 /tmp/rc104-venv/bin/python -B -m unittest -v tests.test_active_abandon_reachable'
```

结果：worker实跑 Ran 1 test，OK，exit 0；父级随后独立同命令 Ran 1 test in 0.598s，OK、exit 0，原逐跳断言通过。父级工具的stdout/stderr交错，不拿其截断JSON重造完整逐字段日志。逐跳均从真实对象读取：
- 真实 LR-2 准备 START → Activity ACTIVE，前台指向它；
- 真实 AG0 候选 + 真实 AG1 evaluate_opportunity 的合法集包含 ABANDON，脚本 Decision 提交同 store；
- 同 store 回读 Decision = ABANDON；真实 AG2 adopt_decision 签发 ABANDON 预提交授权，adoption_status=APPLIED；
- 真实 LR-2 ABANDON transition 回执在同一 store 可读；同 Activity status=ABANDONED，前台释放。

安全收尾：reason_code 只写白名单异常类型名，未知写 UNKNOWN，不再拼接异常 message；未读取 prompt/正文/headers/env/密钥。网络用测试局部硬失败入口（socket.connect/connect_ex、create_connection、getaddrinfo），finally 恢复；先 loopback 自检证明真被拦截并计数，再清零自检。链上 network_calls=0 是本次真实计数器读数，不是初值声称；产品 adapter 是脚本替身，无 transport。

提示材料：prompt_says_abandon_withheld 初始化 UNKNOWN；本次用真实 pause_condition_view（带/不带 basis）经正式 adapter 白名单构造后窄查整词 "abandon"，得 NOT_FOUND_IN_SCANNED_MATERIAL：所核 PAUSE 材料没有一句话说“ABANDON 被撤下”；PAUSED 下 ABANDON 的撤下是 AG1 合法集计算的结构事实（固定合法性说明），不是提示文案。basis=STATIC_MATERIAL：本进程未真实构建/发送 prompt，未扫全 prompt，不据此说提示送达或 c 历史。

未验证：c 历史当时是否合法放弃、提示是否送达/被理解、真实模型自然采用、G1。未起服务、未访问旧 root/a/b/c、未跑旧 suite、未改生产、未联网；mkdtemp 保留。产品调用 0，占额 141/300 不变。
