# G1限定真实模型采用：本批结果（2026-10-30）

**本批已结束，G1未覆盖，不续跑。** 唯一运行于08:12:05.115—08:13:35.166 CST，约90秒后因 `PROVIDER_OR_TRANSPORT_FAILURE` 提前停止。run与父级事后audit均exit 0；这是入口正常收尾，不是业务全绿。

## 实际发生

- 新实例 `/root/nyairo-g1-real-20261030`，run `run:g1-real-20261030`，域 `g1:real:20261030`。真实模型模式，未使用离线脚本Decision或内容；provider/model参数审核吻合。
- 首轮正式记录 `START / COGNITIVE_REQUIRED / ACTED`，Activity `actv_d4f21db04f9cf535`，Action `actn:f25413285edc5df1` 为 `SUCCEEDED`。报告记录目标作品 `ba763339199b48a88c130c016fdcfd7d`、`owner_version_created=true` 及版本ref。
- 同份报告却有 `write_confirmed=false`，P1未建立实验所需的A。版本引用转换、owner当前head与成果识别失败的确切因果未核清，不把它解释为模型没写，也不直接认定仅前缀问题。
- 随后一次认知为 `DECISION_FAILED / BLOCKED`，停批标签为 `PROVIDER_OR_TRANSPORT_FAILURE`。具体failure_class、HTTP状态、finish_reason及逐请求发送/响应未核清，不能据此定为截断、超时或供应商拒绝。
- operator中断、P2重启、同Activity续作、UNKNOWN原操作对账、CAS冲突及NO_ACTION后续作均未覆盖；不是已开展但未检出能力。

## 预算与收尾

父级事后audit确认持久预留 **3/20**，不退款，剩余17不续用。历史97笔确认预留＋本批3＋相机未知1＝保守 **101/300，余199**；这是沿用历史与本批现场账本，不是重读全部旧实例的总账。预留不等于成功往返次数。

P1 runtime报告closed，Supply退出0且socket无活listener，socket文件保留。父级定向检查本根Python匹配0，P1/owner PID19022/19023均不存在；lease仍UNKNOWN，不冒称FREE。根、数据库、失败产物和旧dirty均保留，未重放或新增第二批。

## 验证和未完成的诊断

真实运行前，父级最终本单 `offline-check` 实际exit 0、28项检查PASS，使用真实owner/两PID与明确模型替身，不等于真模型覆盖。首次prepare与audit0实际通过。运行后只读audit确认finished/NOT_COVERED与账本3。

只读收口首派在施工模型可用性探测TimeoutError，exit 3、worker未启动；原入口/原模型/原工单唯一重试满1200秒，exit 124，未生成约定JSON或结果。定向残留检查无匹配；不第三试，不换模型/通道，不以施工网关失败推断产品失败同因。成果识别不一致与产品失败细因留为UNKNOWN。

没有改生产规则、默认World gate、安全设置或旧实例，没有删除、外发、提交、推送或部署。G1整体、G2历史因果、完整V-C、理解与24h边界不变。

## 后续离线修复停点

本人直接继续后，新增入口版本引用识别修复单；worker因施工网关503退出，留下部分script/test，无最终交付。父级本单offline-check实际exit2，24unit中1红，定向用例确认“错作品”负控返回True；该helper与手塞head的夹具缺少身份配对，不能直接推断正式owner读取已串作品。部分修复未接收，未新增产品调用或访问已结束实例，未回写本批历史结论。原批失败细因及首成果识别的历史因果仍未知。

## 离线后续：版本引用识别与身份配对

本批结束后只做离线修复，0 产品调用、未访问任何旧实例、未 prepare/run 真实批次，101/300 不变。
入口 P1 的成果识别现在要求两件事同时成立：owner 重读回来的对象就是 receipt 指向的那件作品
（`ArtifactResumePoint.artifact_id`），且它的 head 与本轮 `owner_version_id` 指向同一版本。
版本 ref 只认现有链证明的三种写法（`artifact-version:<hex>`、
`artifactversion:artifact-version:<hex>`、`artifactversion:<hex>`），重复包装与裸 hex 不认；
原始 refs 与旧 payload 不改。离线 `offline-check` 实际 exit 0、28 项 CHECK 全 PASS
（含本入口 26 例单测），正控走真实 runner 的 `extra.owner_version_id → _safe_wake → P1` 链。

这只闭合比较与身份配对。真实 run 首成果识别失败的历史因果仍未核清，产品失败细因仍为
UNKNOWN，本批仍记 `NOT_COVERED`，不据新离线正控补造 pause/P2 或改上面的结论。
