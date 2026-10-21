# rc1 实体购物、房间布置与灯光：父级体验验收

## 结果与基线

限定本地生活链通过，直接底座仍为 GitHub `v0.3.0-rc1` 的 `41e818faaa24115fbf43db2977b15585c04144af`，施工根 `D:\nyairo-rc1-body-backend`、分支 `work/rc1-phone-world-body`，承接此前手机/世界/身体成果，不是新建钱包或重置演示。

产品与测试由国内 WorkBuddy DeepSeek 分工实施；父级负责规格、写集、实际页面体验与收口。源码仍未暂存/提交/推送/打标/打包/部署。rc1 MANIFEST 与两份移动修复脚本保持原样，插件仍只有此前手机注册 12 行增量；MANIFEST 是 rc1 发布清单，不代表当前开发树已封包。

新契约与实现结果分别见 [房间契约](rc1-room-living-contract.md)、[核心结果](rc1-room-living-result.md)、[服务结果](rc1-room-api-result.md)、[界面结果](rc1-room-ui-result.md)。此前阶段的 [世界身体验收](rc1-world-body-parent-acceptance.md)保留为历史，不覆盖其失败或改写其证据范围。

## 当前入口与启动

**本机 <http://127.0.0.1:35931/>**，验收结束时保留运行，不是自启动或生产服务。

- 仍是 `/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094` 同一实例。
- 当前位于家中客厅，坐姿，书桌/椅子/床头灯在客厅，灯开着；原点心放在客厅，未消费。
- 余额 **2860 虚拟 JPY**，四笔购买：此前点心 240、本轮书桌 4200、椅子 1800、灯 900；没有真实支付。
- WSL Ubuntu UID 0，不能称为普通账户/多用户/Windows 原生部署验证。
- 最终 serve PID 13765、Supply 13766、World 13767，仅作历史记录，未来不得据此盲目杀进程。

停止自己的现有 serve 后，在 WSL 中同根重启：

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py serve \
  --instance /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094 \
  --port 0 --tick-seconds 5 --with-room-living
```

打开输出的 URL；`--port 0` 可能换端口。此次使用固定 35931 通过 Windows 页面验收；若重启报 EADDRINUSE，先核对自己的进程/端口，不删状态、不批量杀进程，可另选空闲 loopback 端口。

`--with-room-living` 只在本次 serve 内存中为服务与 World 子进程同时启用营业规则和房间环境。实例 `features` 原来的 daily=0 未改写；不带 flag 重启可停用本轮入口，但不撤销已发生的交易、产权、摆放或灯状态，不能拿旧备份覆盖数据当作功能回退。详见 [用户指南](rc1-room-userguide.md)。

## 同实例实际页面链

内置浏览器因当前会话视图无法显示而拒绝打开，父级用独立 headless Edge + CDP 加载真实产品页面、点击按钮与确认弹层；直接 fetch 只读回核实，不直接写业务状态。

1. 启用前实际位置是家中客厅，原余额 9760、一笔点心购买；核验自己 PID/cwd 后正常停止。对 instance、经济、substrate、world、Body runtime/transitions/consequences 七份现有文件创建 `-backup-20261018-040146` 副本（清单在 scratch），没有执行任何备份还原。
2. 带新 flag 同根启动。随机端口在 WSL 内部正常，但 Windows 本地转发 `ERR_CONNECTION_RESET`，页面尚未加载；保留工具失败。正常停自己的服务，改用已验证可访问的固定端口，Windows HTTP 200 后再继续，没有改防火墙或全局网络。
3. 从家实际走 `home → street_1 → home_goods_store`，两段为世界声明 120s + 176s；页面每次先显示出发、位置仍是起点，等待 owner 到期结算才到达。未改时钟、路线或营业时段。
4. 到店且营业中，首笔书桌购买返回 503 `WORLD_LOCATION_UNAVAILABLE`，余额未变。取证发现 GET/POST 的位置读取分叉，POST 还硬编码旧世界 ID，真实生成的海滨世界因此被拒。DeepSeek 改为同一处 place 级只读位置判据；缺失位置仍拒绝，未放宽到店门。
5. 正常停服加载修复。固定端口一度报 EADDRINUSE，完整异常栈保留，另选可访问端口后恢复同一实例。原购买键 `39fa909d-7082-43e4-97b1-3fef55f6ee43` 查询为 404，经济 owner 确认没有交易；父级只恢复 sessionStorage 中这个未决键（非业务状态注入），由正常页面确认按钮用**原键**恢复书桌意图。没有重复出门或重新初始化。
6. 真实购买书桌、椅子、灯，余额依次 **5560 → 3760 → 2860**；各一笔产权与扣款。再实际沿原路回家，仍 2860/四笔。
7. 本实例原有配送模式为 `instant_home_delivery`（0s）；没有为了验收关闭两小时配送或缩短时长。到货物化由 owner tick 完成，GET 不交付。时间配送的 7200s 机制仅在隔离时钟测试验证，不称为本轮两小时实钟证明。
8. 从真实页面分别 ARRANGE 三件家具到当前客厅，全部 owner 提交成功，未把家具塞进手里。初次验收脚本错误要求 `owned_furniture` 总数为三，而该投影也含原点心，导致脚本红；快照已证三件 FURNISH 都在 living。改为按 `usable_kind=FURNISH` 筛选，保留原红，不重复布置。
9. 实际走回卧室，拿起原点心，再走回客厅。脚本曾用错误 CSS 行选择器找书桌，未提交桌面动作即红；核对真实页面存在书桌面和按钮，改验收定位器后从当前手持状态续验，没有再走路或重买。
10. 页面把原点心放在本次购买书桌的 **instance 级 desk_surface**，读回左手空、该桌面 items 含原点心；再拿回原点心并放回客厅。坐下生效；椅面来自实际购买实例，声明 seated/rest。本轮没有绑定“坐在哪一把椅子”的身体接触状态，不夸为座位接触物理。
11. 页面开灯成功，实际等待 65s 后刷新，关灯成功，再开灯成功并保留为 on。Body transitions 中出现 `cause_class=ENVIRONMENT_INPUT`、`environment_model=environment.light.v1`、来源 `world_room_environment@home/living:light=dim:power=on`。感官负荷按旧数学小幅变化，没有触发 elevated 信号，未调参数/阈值制造效果。
12. 正常停服、同根重启后，**13 项稳定事实对比全部为真**：钱包/四笔/位置/手持/三件家具摆放/surface 关系/交付/灯身份及 power、since/坐姿/执行身份/笔记/收藏一致。灯的 on_seconds 和随时间推进的 Body 浮点不作机械等值要求。此实例笔记、收藏仍是空态，不冒充本轮新写验证。
13. 重启后真实页面只读核对通过：显示房间光、书桌面、2860 余额；390px 无横向溢出，PNG 保存成功，页面 runtime exception 0。不据此宣称人工全设备视觉/完整键盘验收。

## 定向验证与修复范围

核心实现者报告最终 `test_store_room_owner.py` 25 项、`test_room_environment.py` 18 项通过；服务新 API 23、工具 16、启用 flag 13 项通过；界面新 room 21 项通过。旧世界 API 的动作清单断言由明确五项更新为与 owner 七项精确一致，保留原五项存在校验；不是放宽成“返回什么都行”。

父级只再做实际生活链、重启比对与最终门禁，没有重刷整库。最终 HEAD 仍 rc1，41 篇 notes tree/format、`git diff --check`、rc1 保护项均退出 0，索引为空、无 tracked 删除项。

父级在集成前抓到并由 DeepSeek 定点闭合：房间块在 objects 内而非顶层的 P/U 消费错误；启动器硬关营业导致环境变量无效；柜台到货缺 substrate 物化导致永远 NOT_MATERIALIZED；旧 root 灯光观察锚点 gap 问题；随后实际页面抓到柜台位置读取硬编码世界 ID。所有修复有定向回归，未重开泛化审查。

## 证据与偏差

会话 scratch 中保留：
- `room-parent-before-room-enable.json`、`room-parent-backups.json`；所有 serve/rebind/失败启动与异常栈脚本/日志。
- `room-browser-shop-1792296396603.json`（到店后柜台 503 原红）、`room-browser-recover-shop-1792297596851.json`（原键恢复三购买及回家 PASS）。
- `room-browser-furnish-1792297924221.json`（脚本错误分类原红）、`room-browser-finish-1792297971425.json`（定位器错误原红）；都不是伪装成产品故障后的重复业务操作。
- `room-browser-useheld-1792298104332.json`（桌面/取回/坐姿/灯实钟链 PASS）、`room-browser-readonly-1792298283982.json`（重启页面 PASS）、`room-parent-restart-checks.json`（13 true）、前后快照与 `room-final-mobile.png`。

原始 CDP request 证据可能包含本次临时 CSRF，不作为公开交付物上传或复制到仓库；不得原样转发。长期文档仅存非凭据 operation/对象身份。

本轮 P 施工者曾误用 `git add -N` 后 `git reset` 撤销 intent-to-add，已在 note 留偏差；父级最终检查索引空，后续没有提交发布。前一阶段缓存清理违规保留在前一验收，本轮未报告或执行物理删除。不要把 tracked 无删除项等同于全文件系统零删除证明。

## 明确上限与停用

没有全生理/新饥饿营养/温度噪音/自动关灯/3D/容器/书籍内容/通用 USE/自动自主购物或实际模型调用。书架目前没有容器内部能力；家具功能只按声明，不因买了就自动解锁一切。旧裸 bed_surface 兼容层仍不具 instance 级区域信息，不声称本轮把历史床面物理全重建；新购买桌/椅 surface 是 instance 级。

环境的间断观察只做明确有界无输入推进，不把停机时间编成灯光持续亲历；本轮正向环境输入实钟证明为一段 65s 等待，不是全天/24h。时间配送未实钟等待两小时，只有机制验证。无生产切换、真实支付/外发、旧个人 profile 迁移或发布资产。

当前限定生活链没有阻塞，完成本轮后停止扩张。停用是正常停自己的服务、同根不带 `--with-room-living` 重启并保留发生的历史，不是覆盖旧经济档回退余额。发布另需授权。
