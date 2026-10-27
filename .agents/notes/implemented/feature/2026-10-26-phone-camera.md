# Agent Note: 手机拍照 —— 本机生成的一张图，进既有相册

Status: implemented

## Problem

手机已有私人相册（Supply `ManagedArtifact` + 媒体文件 + 清单，见
`2026-10-22-v04-private-album-import.md`），但相册只能由操作者显式提供 PNG/JPEG 字节导入。
设计 v2 第 15 章要求的是她自己的照片体验：她给画面与一句说明，手机里真的多出一张图，按时间
能翻到、停服重开仍在。直接塞占位图或外部 API 会伪造来源；另造图片 owner/库又会复制 Supply
的版本、权限与撤权语义。

本机有可用的 Stable Diffusion WebUI（loopback `127.0.0.1:7860`），但仓库跑在 WSL，而该服务
只监听 Windows loopback；WSL 自己的 `127.0.0.1` 到不了它，且不允许改它的 listen/防火墙。

## Decision

1. **拍照不是新 owner**：`components/phone/camera.py` 只负责向她指定的**本机 loopback** 运行时
   要一张图（512px、12 步、batch 1、无 hires、无插件、不改模型/`override_settings`）。生成的
   字节随即走**既有** `import_album_image`，由 Supply 保存媒体文件与清单；手机不写第二份图片
   存储、不新增表/库/owner。
2. **一个工具入口 + 一个窄 HTTP 路由**：`chiyo_phone` 增加 `camera`（`scene` + `caption` +
   显式 `operation_key`），HTTP 增加 `POST /api/phone/camera`；相册另加只读 `album`（目录）与
   `album_image`（一张的说明与来源）。没有页面、没有自动拍照、没有日配额、不写 Memory、
   不调用产品认知模型；caption 只由调用者给出，不额外生成。
3. **生成前先判幂等**：手机侧 `phone/camera.jsonl` 记录 `operation_key → artifact_id`、画面摘要
   与说明。同 key 同内容直接读回原图（`reused=true, generated=false`），不再请求 GPU；同 key
   换画面/说明明确拒绝（`CAMERA_OPERATION_KEY_REUSED`）。相册导入用派生 key
   `camera:<sha256(key)[:16]>`，与操作者自己导入文件的 key 分开。索引只是手机侧归属记录，
   图片真相仍在 Supply；索引写失败会如实标 `index_recorded=false`，不伪称幂等。
4. **忙就拒绝，不抢 GPU**：生成前只查一次 `/sdapi/v1/progress` 的 `job_count`；不为 0 立即
   `CAMERA_BUSY`，不排队、不重试、不换模型、不卸载别人的作业。出错/OOM/超时/非 PNG-JPEG
   一律具名失败，不拿假图或占位图代替。
5. **WSL 走既有 Windows 互操作桥**：直连 `127.0.0.1:7860` 失败且本机是 WSL 时，用
   `scripts/phone_camera_win_bridge.py` 在 Windows Python 里发**同一个** HTTP 请求（只做传输，
   再校验 loopback、拒绝重定向、不落盘、不打印）。不改 Windows 服务的 listen、不加防火墙
   规则、不新起出图服务；纯 Linux 主机上直连路径生效，桥不会启动。
6. **来源诚实**：相册 owner 清单仍是不可变的 `user_import / self_declared`（确实经用户导入），
   不改旧格式。手机侧在相册列表/详情上叠加 `generated_locally` 与
   `source_label="local_generated"`；工具与文档都说明这是本机生成的图像，不是真实相机照片、
   不是 World 渲染、也不是她亲历的画面。
7. **普通读面默认可见**：照片与说明和既有相册一样可读；日记正文默认扣留的规则不变，二者
   不共用读口。
8. **中文画面由本机转换器转成标签英文，原文与英文都留存（2026-10-26）**：该 checkpoint 对
   标签式英文更稳，但她不必改用英文说话。`components/phone/camera.py` 的 `SceneTranslator`
   把她的中文 `scene` 交给机器上**已经在跑的** llama.cpp（默认 `127.0.0.1:18190`，可用
   `PHONE_CAMERA_TRANSLATOR_BASE_URL` 改端口）做一次温度 0、≤160 tokens 的转换；只读
   `/slots` 的 `is_processing` 判忙，忙就 `CAMERA_TRANSLATOR_BUSY`，不排队、不重试、不换模型。
   答案必须是短的、不含中文的标签行，否则 `CAMERA_TRANSLATION_UNAVAILABLE`，**绝不**把中文
   原样送去生成、也不用固定模板冒充。本来就是英文的 `scene` 直接使用（`translated=false`）。
   手机侧 `phone/camera.jsonl` 同一条记录保存 `scene`（她的原话）、`generation_prompt`（实际
   英文）与 `translation`（`translated`/`source`）；`album_image` 详情原样叠加这三项，旧记录
   没有就返回 `null`，不从 hash 反推原文。同 key 重试在翻译之前就返回，既不重译也不重生成。
   Windows 桥的 URL 允许列表收紧为 7860／18190 两个既有本机端口，不是通用 URL 代理。

## Alternatives considered

- **在相册清单里直接写 `local_generated`**：最直接，但要改 owner 的不可变清单格式与导入绑定
  digest，旧读端和新清单会分叉；否决。改为手机侧叠加来源标签，owner 存储不动。
- **同 key 先请求 owner、由 owner 的 `NO_CHANGE` 去重**：能省一次生成吗？不能——生成发生在
  导入之前，这样每次重试仍会先跑一次 GPU；否决。改为生成前查手机侧索引。
- **改 SD WebUI 监听 0.0.0.0 / 加 portproxy / 改防火墙**：让 WSL 直连最省代码，但这是系统级
  部署与安全面变更，超出本单授权且会影响其它使用者；否决。使用已有的 WSL→Windows 互操作，
  只转发本次请求。
- **新图片 owner / 手机独立媒体库**：可单独迭代，但会成为第二私人真相源并复制 Supply 的权限
  与撤权语义；否决。
- **外部 image API 或下载模型/依赖**：来源与隐私都不可控；否决。
- **把中文原样当提示词**：最省一步，但既有单样本显示该 checkpoint 可能出与画面无关的图；
  而且这不是“转成它能懂的英文”，等于把问题留给生成器；否决。
- **词典/固定场景模板/规则拼接**：不花模型调用，但只能覆盖预设词表，遇到没见过的画面就会
  丢对象、数量或颜色，属于伪转换；否决。
- **新起一个翻译服务或换翻译模型**：会引入新进程、新配置与新依赖，且本机已有空闲服务；
  否决。只复用现有 loopback 文本服务，并限制输出预算、单请求、不排队。
- **把英文提示词写回相册 owner 清单/当作亲历证据**：相册清单是不可变 owner 格式，英文提示词
  只是手机侧的生成参数记录，不是 owner 的权威；否决。留在 `camera.jsonl` 与读面叠加里。

## Consequences

- 一张照片仍是一件普通 `ManagedArtifact` 首版清单 + 一个 owner 命名的媒体文件；版本、CAS、
  grant、tombstone 与既有相册完全一致，`ARCHIVED`/`TOMBSTONED` 后列表与字节同样被扣留。
- 生成质量取决于本机运行时与所选模型；512px 是本单的最小合理参数，不追求画质，也不代表
  “真实照片”。WSL 场景依赖 Windows 互操作（`python.exe` 可见）；没有该互操作时，直连失败会
  以 `CAMERA_UNREACHABLE` 具名拒绝，而不是假装成功。
- 手机侧 `camera.jsonl` 只用于来源标注与生成前幂等；删掉它不会删照片，只会让同 key 重试
  再次生成（相册导入仍按派生 key 去重，不会多一条）。
- **画面由调用者给，演示常量不等于生产固定**：生产入口只转发调用者的 `scene`/`caption`
  （`phone_tool.py` → `server.py` → `service.camera_capture` → `generate(text)`），没有默认画面、
  没有自动拍照；`scripts/phone_camera_demo.py` 里的 SCENE/CAPTION 只是演示脚本自己的常量。
  生产固定的是生成参数（512px、12 步、Euler a、CFG 5），不是画面内容。
- **本机实际出图与已知限制（2026-10-26 复查）**：演示图实际由 `waiIllustriousSDXL_v160`
  [a5f58eb1c3] 生成（seed 2978869744、12 步、Euler a、CFG 5.0、512×512、Clip skip 2），
  不是 noobaiXL。该中文提示词样本出的是与画面无关的灰金抽象图，右下角带「@陈昊荣」文字水印。
  只读排查未在请求体、`/sdapi/v1/scripts` 脚本列表、extensions/内置脚本/modules、`config.json`
  与 `ui-config.json`、安装内文本文件中找到任何水印来源；7860 监听进程即本安装 `launch.py`，
  无外部代理。用同一 seed/模型/采样器/步数/CFG/尺寸、只把提示词换成英文再出一次
  （`scripts/camera_language_check.py` → `phone-camera-english.png`），画面为窗台杯子与热气、
  无文字水印。单样本只说明该次受提示词语言影响：不能证明水印烘焙在模型权重里，也不能排除
  未找到的二进制/资产型后处理；据此不新增“只能英文”规则或自动翻译层。
- **转换层的实际单样本（2026-10-26，一次授权调用）**：`127.0.0.1:18190` 的
  `rewrite2-4b-Q8_0` 在 `/slots` 显示空闲、WSL 经既有 Windows 桥可达；对一句合成中文的
  一次转换返回的答案超过 400 字符上限，被记为
  `CAMERA_TRANSLATION_UNAVAILABLE:translator_answer_too_long`，没有生成图片、没有保存、也没有
  第二次调用。结论只能到“这条链路能发出一次真实转换请求并按规则拒绝不合格答案”，**不能**说
  这个本地模型能稳定产出可用标签，更不能说她的中文场景已经能自然出图；放宽预算/换提示词/
  换模型都需要下一次明确授权后再验。描述中已写明这一限制。
- **2026-10-27 中文转换复验与修复（三次请求内）**：`rewrite2-4b-Q4_K_M` 在 18190 上真实请求三次。
  第 1 次（合成窗台场景）返回 248 字符可用英文（55 tokens、stop），但追加了输入没有的
  `reflections of buildings and trees` 与 `photorealistic/8k` 等质量词；据此收紧提示词，只说
  “只译已述内容、不加质量/风格/构图词”，不列举具体禁词（4B 模型会把列举词当输出）。第 2 次触发
  `finish_reason=length`（生成满 160 tokens 未完），被新增的截断检查具名拒绝
  `CAMERA_TRANSLATION_UNAVAILABLE:translator_answer_truncated`，未生成、未保存。第 3 次换场景得到
  `kitchen, wooden table, two red apples, silver small knife, snow outside window`（78 字符、ASCII），
  对象、数量、颜色与天气保持。旧的 `translator_answer_too_long` 在这台机器的 Q4 上未复现；新解析对
  截断、中文、闲聊、非英文仍拒绝，400 字符上限未放宽。
- **本机运行时的显式启动**：`scripts/camera_local_runtime.py` 只按已知路径起既有 llama-server
  （Q4_K_M、18190、loopback、`-np 1`、GPU）与既有 SD WebUI（7860、`--nowebui --api`、`--data-dir`
  临时目录、`--ckpt-dir` 指回原安装、`--skip-load-model-at-start`、`--no-download-sd-model`），
  不改原 config/ui-config、不装依赖、不联网下载、不自动常驻；`--print-command` 只打印命令。
  `.claude/launch.json` 追加两个具名项，既有四项不动。
- **2026-10-27 真实整链**：`scripts/phone_camera_demo.py --reuse-confirmed-translation` 用上面第 3 次
  同一场景的真实输出（记为 `local_llama_cpp_reused`，不冒新的转换调用），经真实
  `phone_handler` → `PhoneService` → `LocalPictureLab`（7860、waiIllustriousSDXL_v160、clip skip 2、
  512×512、12 步）→ Supply 相册保存；图 `.agents/worker-runs/20261027-camera-photo.png`。相册 1 张，
  `album_image` 同时给出中文原话、实际英文、说明与时间；同 key 重放不生成第二张；停服同根重开仍可读。
  画面经图像描述读回为厨房木桌、红苹果、金属刀、窗外雪景与暖光，无文字水印；描述只识别到一只苹果，
  与提示的 two red apples 存在数量差（512px、12 步），不据此称完全保真。
- 验证：`tests/test_phone_camera.py` 12 例（真 Supply + 计数假生成器 + 假转换器：保存与来源标注、
  中文原话与实际英文都进详情、转换失败零生成零保存、转换器忙零生成、同 key 不重译/不重生成、
  同 key 换画面拒绝、忙时零保存；另有 6 例纯转换规则：英文直用不发请求、中文一次请求、
  忙时拒发、中文/闲聊答案拒绝、截断答案拒绝、标签前缀与代码围栏清理）。`scripts/camera_translation_demo.py`
  只做一次本机转换、不出图，`--reuse-last` 无调用复读结果。`scripts/phone_camera_demo.py`
  仍是真机整链演示（真实生成、相册保存、列表/详情/字节、同 key 复读、正规停服同根重开）。
  未跑全库。
- **2026-10-27 有界自主会话入口（本窗口只到 prepare/status/readback，未发产品请求）**：
  新增 `scripts/autonomous_camera_once.py`（`prepare` / `status` / `run --live-model` /
  `readback`）。`prepare` 用既有 `phone.demo.prepare_demo` 建**全新隔离 root**（真实
  Supply/World/手机实例、无合成作品或阅读 seed、持久请求账本 3/3），并用现成
  `scripts/setup_profile.py` 生成 Hermes profile；`run` 才使用现有产品模型与
  `run_agent.AIAgent`，工具集 `chiyo_world` 来自现成 `chiyo` 插件注册的 `chiyo_phone`
  （不是 `Instance` 的临时人设，也不是裸 SDK 请求）。每个产品请求先经既有
  `LifeRequestBudget.reserve` 再发送，硬 cap 3；隔离 profile 里的 `camguard` 只允许至多
  一张图，成功出图后阻断再次 `camera`。`run` 的提示只中性告知可用能力与该隔离 World
  自己报告的可见事实，不写 scene、不诱导。本窗口实际只跑了 prepare/status/readback 与
  两次 0 产品探针（World 事实读取、注入“已生成”结果后的守卫阻断），真实主体判断未发生；
  18190/7860 的启动交给父级用 preview，未用 Bash 裸启。入口与守卫可用不等于自然采用，
  G1/V-C/24h 边界不变。
