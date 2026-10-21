# rc1 主体私人材料与生活续接：父级限定验收

## 结论和方向纠正

本轮主体侧阅读执行与续接机制通过；不是新UI交付，也不是已经启用真实模型自主生活。用户明确“他自己的手机，他自己有就行了呗，我还要看吗”，因此手机属于主体自己的私人生活工具，普通用户查看/点按钮不是它成立的条件。已有页面保留为可选观察/调试，本轮零web改动。

仍以 `D:\CHIYO-总体设计-v2.0-final.zip` 为设计来源，原文选段 L280–339 的私人空间/自己的手机/实际内容、16章的私人活动不交账/历史承重/安静和未知、18/19现行边界共同约束。本轮不把13章ARCHITECT_PROPOSAL冒充生产授权，不建全局LifeManager、手机人格或平行内容库。

唯一施工根 `D:\nyairo-rc1-body-backend`，branch `work/rc1-phone-world-body`，HEAD仍 `41e818faaa24115fbf43db2977b15585c04144af`（GitHub v0.3.0-rc1），承接前轮dirty手机/World/Body/房间/阅读成果。产品代码与测试由国内WorkBuddy DeepSeek实施，父级只做管理、接口核对、scratch验收与本报告。未提交/推送/打标/打包/部署。

关联：[核心契约](rc1-subject-materials-contract.md)、[核心结果与偏差](rc1-subject-materials-core-result.md)、[无界面运行说明](rc1-subject-materials-runbook.md)、[实例绑定结果](rc1-subject-materials-binding-result.md)、[前轮阅读验收](rc1-reading-parent-acceptance.md)。施工单 `D:\NYAIRO-rc1-主体私人材料与生活续接-施工单.md`。

## 落地能力

- `subject_private_materials.py` 从现有Supply材料与PDI读取当前版本、真实acquisition支持的最近停点、next_page、读尽/撤权/未知与批注计数。版本变更不继承旧版最高页；正文/笔记不进入通用处境事实。
- 现有Life cycle/候选源对接该处境；生产prompt可见有界ASCII事实键，不靠被sanitizer丢弃的值传递页码。PDI进度变化影响阅读候选新鲜性，读尽不再生成新可执行阅读机会。
- 已在前台的 `READ_PERSONAL_MATERIAL` 继续同一Activity读页，不误走作品writer；作品前台也不被当成阅读。读尽后允许NO_ACTION/PAUSE/ABANDON，没有新增自动完结合同。
- cycle作品选择及resident基线过滤已知非作品，优先显式绑定，不重写通用 `latest_resumable`。阅读取得+停点仍经现有Decision/Adoption/AR0/Supply CAS，同一PDI版本落地。
- 新增 `components/phone/life.py`、`phone.py life-preflight/life-run`，映射手机清单的live socket与同源材料/subject/workspace/grant，沿用nested Life state/run_id/请求窗口；不覆盖nested清单、不另起Supply/World、不自动接模型或开新额度。`one_day_life`仅修三个已复现argparse缺陷。

## 当前实例和真实盘点

验收沿用 `/tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094`，同一Supply/World服务，未重建实例。WSL UID0，不能称普通用户或Windows原生部署验证。

开工只读证据 `subject-parent-before.json`：钱包2860虚拟JPY/四购买、家中客厅坐姿双手空、家具与灯on；work版本1，阅读材料版本3（前轮第二版两页，停在第2页），PDI版本6；普通笔记一条，疑问一条。手机live Supply socket是serve代，nested Life仍指向已退出prepare代；两份清单的材料/workspace/grant一致。这证明需要显式映射live绑定，而不是另造数据。

备份：phone/nested instance各留 `-backup-20261018-091620`；原材料另存scratch `subject-original-material-backup-20261018-091620.json`，清单 `subject-parent-backups.json`。未恢复任何备份；后续不能用旧PDI/材料/经济档覆盖已发生历史。

## 无浏览器、无用户消息的真实owner有限验收

父级scratch `subject_worker.py` 注入明确标记的 `test-only-subject-reading-driver`，只从AG1合法候选中选START/CONTINUE/NO_ACTION；真实runtime、Activity lease、AG2、AR0与Supply均为现有实现。作品writer一旦被调用就抛错。没有provider请求、Memory消费者、聊天请求、Web写入或后台常驻。fake选择不是她已经自主选择的证据。

1. 旧材料已到末页：进程26123返回NO_ACTION，PDI与work零版本变化，lease释放。
2. 父级显式提供三页非秘密机制验收材料，走既有 `PhoneReading.replace_material` owner入口，保留原artifact，版本3→4。操作键 `parent-subject-material-f2e8f765666f8ba2`。这是operator提供材料，不是自主取得或私人心得。
3. 进程26176：START真实读第1页，随后CONTINUE真实读第2页，均 `ACTED`，同一Activity `actv_da1b5cdf89d359b5`。作品head不变；PDI两次取得+停点提交，各一版，释放lease后退出。
4. 新进程26242：同一state root、同一run_id `run:one-day`、同一请求窗口，初始Activity与前进程一致；CONTINUE真实读第3页，work仍版本1，PDI再加一版，正常释放lease。两进程PID不同，不以同进程重建冒充进程重启。
5. 进程26279：当前材料EXHAUSTED，返回NO_ACTION、PDI零写、work零写、lease释放。没有重复读最后一页、没有伪造自动完成或去写作品。
6. 最后纯读核对 **20项全部通过**：钱包/位置/手持/家具/surfaces/灯身份电源since/姿态/笔记条目/收藏不变，PDI恰从6→9（3次真实读页），作品head不变，同活动/不同PID/同run/请求窗口不重置，旧版和新版读尽均零写，末页3，所有lease释放，真实模型调用0。

主体runtime均已退出，未留下自主常驻。原phone serve保留只是前轮可选观察服务；无需用户打开页面。内存中没有私自增设模型、budget或Memory权限。

## 实际发现与定点修复

真实盘点发现当前版停点有效但 `needs_reread=true`：初处境把任何旧版页存在视为当前停点永久过期。已由核心作者定点修为“当前有效停点决定当前重看状态；无当前有效停点才以旧版记录提示重看”，保留prior_revision_pages和旧批注stale；并补 acquisition 类型/source_id匹配，不能拿别家材料的取得凭证当本材料停点。两个新反例及两个准确受影响既有纯读用例通过，未重跑全组。修后当前实例只读证明 `EXHAUSTED/page3/needs_reread=false/reason空/prior_revision_pages=[1,2]`，未改历史条目。

最后父级比较曾红在notes整体响应：正文/锚点/身份均相同，只有共享PDI head和version6→9变化。首次红快照保留 `subject-parent-final-first-red.json`；改验收断言为笔记items一致并额外要求PDI恰三版推进，随后20项通过。不是产品笔记丢失，也不重复执行读页。

## 定向测试、门禁与偏差

- 核心新增：11条处境 + 9条阅读活动exact IDs通过，runner只取本类 `__dict__`，不继承discover；随后重看/source绑定窄修4条通过。新纯读测试文件最终有13个用例，新增两条不与前11重复计能力。
- P绑定入口32项WSL通过无skip；Windows5项POSIX skip，不能当原生运行已证。CLI help/read参数缺陷修复已定向验证。
- 前核心曾扩大到旧继承集成56项429.540s和其它旧组，偏离限定测试要求。父级停止该委派，原会话不能resume，接替者只核对并终止已归属的 `c_run_affected.sh`/w05r测试，未碰当前服务；原日志保留，未完成旧组不算通过。之后仅新增exact IDs和窄修，不再刷旧套件。
- 原核心已有被停/接替不是第二轮泛化review，产品未整体重做；父级只查真实接线与实际红点。
- 最终从实际repo cwd：46篇notes tree/format、git diff --check、MANIFEST和两份rc1移动fix保护检查均exit0；HEAD不变、索引空、无tracked删除。World/web/插件既有diff与开工前一致。无tracked删除不等于全文件系统零删除证明；夹具和失败证据保留。

主要证据在会话scratch：`rc1-subject-phone-*-recon.md`、`subject-parent-before.json`、`subject-parent-backups.json`、`subject-parent-material-import.json`、`subject-worker-exhausted-before.json`、`subject-worker-first.json`、`subject-worker-second.json`、`subject-worker-exhausted.json`、`subject-parent-final-first-red.json`、`subject-parent-final.json`（20 true）、`c-new-materials.log`、`c-new-cycle.log`、`c-fix-needs-reread.log`、旧扩大回归原日志。未运行旧源码负控，不能把新增反例称为旧码负控。

## 运行边界和真正剩余条件

真实命令：

```bash
cd /mnt/d/nyairo-rc1-body-backend
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py life-preflight \
  --instance /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094
PYTHONDONTWRITEBYTECODE=1 python3 scripts/phone.py life-run \
  --instance /tmp/nyaw05r.jdK3q/rc1-world-handover-1792288094 --once
```

父级真实执行结果：preflight退出1、仅model阻塞；life-run退出3 `NO_MODEL_PROVIDER`，`model_called=false/processes_started=0`。这份验收实例没有配置真实provider。**机制已验证，不等于真实模型已启用；真实自主运行仍需另行明确模型配置与消费授权**。本轮不擅自读取别处密钥、不改profile或全局设置，不把BLOCKED改说她主动安静。

不宣称全手机自主购物/日历/联系人/相册/私人创作、真实理解、24h生活或生产发布。没有新grant验证读口时权限仍由owner执行时硬拒，不能把候选可见性当授权。材料读尽的Activity保留ACTIVE，可合法NO_ACTION/PAUSE/ABANDON，未新增自动完成合同。当前phone服务旧进程加载的是前轮Python模块，本轮主体已通过新进程加载新Life代码，未声称旧进程所有模块热加载。普通用户无需查看/操作这些调试入口。
