# a/b/c World adapter 构造参数：有限核对

父级在只读worker超时后，按监护追加的两项范围，独立核对现存脚本和本地git记录；未跑测试/服务/模型，未访问旧实例，未改代码。

- [默认参数](../../scripts/g1_real_adoption.py:1146)为 `world_feed=False`；a/b/c共用的两处phase调用（[2686](../../scripts/g1_real_adoption.py:2686)、[2838](../../scripts/g1_real_adoption.py:2838)）均省略该参数。只有d natural的[3041](../../scripts/g1_real_adoption.py:3041)明确传True。
- [1217—1230](../../scripts/g1_real_adoption.py:1217)：局部 `world_action_adapter=None`，仅world_feed且World READY时赋标准adapter，随后传给runtime。因此，**现存a/b/c两处phase的runtime构造参数确定为None**，不是仅凭注释或peer自述。全文件该标识命中未见其他补接；本次未补审组件内部的其他路径，不能将构造参数结论扩大成全runtime所有可达能力已穷尽。
- `git status --short`为`?? scripts/g1_real_adoption.py`；`git ls-files`与`git log --all -- ...`无输出，说明当前未跟踪且本地可达引用未查到该路径提交历史。**不证明任何地方都不存在历史副本。**

这是需要优先复核的明确装配风险，不能无视，也不将历史能力硬写为已证不存在。没有核到三批运行时源码快照、实际加载/后续补接记录，**a/b/c当时World执行能力本次仍未证实**；不凭现存默认值估计“极可能”或宣称举证责任倒置。d原运行有另行记录的adapter漏接及空step/action refs；d已完成标准接线离线验证，不倒写原运行，也不移用为a/b/c历史证据。

不据此解释不出门/不动/不返回，不重判作品版本、G1或今日各批结论。首只读单1200秒超时、无交付的失败史保留；此次有限核对不是补齐四文件核查。预算148/300不变，今晚到此停止。
