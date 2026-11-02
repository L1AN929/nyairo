# G1第二批：实际行为与结论（2026-10-30）

**本批结束，G1未覆盖，不续跑。** 唯一运行于10:47:55.827—11:00:52.237 CST，约776秒，因20次请求预留硬上限到达而提前停止，不是跑满30分钟，也不是报告为供应商故障。run与父级事后audit均exit0，audit为finished/NOT_COVERED；shell成功表示程序收尾，不代表G1通过。

## 模型实际选择与效果

1. P1首轮模型选START，正式Activity `actv_860851137eb78b7a`、Action `actn:656eeeef3441472a`成功，作品A `0d4f7852ad4e4657ad52c9d8927e77c2`得到新版本。owner head为 `artifact-version:b2af7b1c8b1740c98338ff9f423aa413`，本轮版本ref为对应合法包装，入口 `write_confirmed=true`，当时版本数2。
2. operator解除全局暂停后，模型选择PAUSE原Activity A；未出现同Activity A的成功CONTINUE。
3. 模型START另一作品 `dc08258c3d01477aaf8b6018ab7c1edf`（Activity `actv_524be82406a511da`），成功写入，随后CONTINUE同一Activity再写一个版本，再PAUSE。
4. 模型START第三件 `6d79121957424b47b04fbd66675244c9`（Activity `actv_cfd520a42523dff2`），成功写入，随后PAUSE。
5. 模型START第四件 `8b524687035b48bda919da1569008b3b`（Activity `actv_e058437df16189e6`），成功写入；接着本批到额停止。

以上是安全报告里的明确Decision枚举/Activity/Action/owner版本事实：共4个START、1个CONTINUE、3个PAUSE，5条成功Action及owner新版本记录，涉及4个Activity/作品。不读取或披露作品正文，不据模型reason_codes推断真实动机、关系或完整用途。另有3条流程outcome/status为NO_ACTION，但decision及route为空；不能仅凭llm_calls=1把它们当模型明确NO_ACTION，也不把它们归因为纯规则或具体失败，细因未核。

## operator干预与未覆盖

- A首结果后，operator于10:48:21显式全局pause；P1 runtime正规关闭。P2是不同PID，同根同run读回暂停，暂停轮llm_calls=0；10:49:09由operator解除。不是模型自主中断或自主恢复，也不是crash测试。
- P1 PID30444、P2 PID30652，两个Supply PID30445/30654。两阶段共享同一run/账本。
- A在P2起止均2版，head不变；模型未成功续原A。不能把后来另一作品的CONTINUE挪来满足同A场景，也不能据本次没返回A断言模型无接续能力。
- 因未到原A续写阶段，丢回执与并发head注入均0，UNKNOWN原操作对账、CAS拒绝、目标链NO_ACTION及其后续作均未覆盖。没有人为强制换事、补造资格或解除静默追结果；整体G1仍NOT_COVERED。

## 预算、验证与收尾

持久账本20/20，包含Decision/内容及任何失败预留，不退款、不续杯。未逐请求核purpose、usage或全部发送/响应，因此不将20预留等同20成功往返。此前保守101＋本批20＝**121/300，余179**；等价于确认预留120＋相机未知1。此为历史记录加本批账本，不是重读全部旧实例的现场总账。

父级运行前独立 `offline-check --batch b` 实际exit0/37checks PASS，scratch `/tmp/g1-real-offline-nhb037hg`保留；它是明确模型替身/真owner的离线证据。首次prepare/audit0实际exit0，身份为batch b / `run:g1-real-20261030-b` / `g1:real:20261030-b`，账本0/未started后唯一run。

运行后父级audit实际exit0。两阶段runtime报告closed，两个Supply均exit0、socket_live_after=false，graceful_stop_line.seen=false原样保留，不冒所有日志无异常。父级定向本根Python匹配0，四记录PID均不存在；socket文件与所有数据保留，lease仍UNKNOWN，不报FREE。未读写第一批或其它旧根，未提交推送部署、外发或改默认World gate/安全设置。当前无在途施工或验收，不自动新开第三批。
