# Agent Note: v0.4 私人相册的真实本地导入与同 owner 保存

Status: implemented

## Problem

手机已有文本作品，却没有图片的取得、保存与取回契约。只加图标或用开发截图种一屏，会把夹具冒认成真实相册；另建手机 JSON/数据库又会绕过既有 Supply 权威。用户已授权监工按总体设计决定这类实现细节，要求推进一块真实可用的体验，而不是空壳。

## Decision

每张用户实际选择的图片由**同一个 Supply ManagedArtifact owner** 管理，不新增 owner、表、库或 capability：

* 一件相册图片就是一件普通 `ManagedArtifact`。它的首版正文是一份确定性的 JSON 清单（`kind = "album.image.v1"`），只保存说明、媒体类型、文件名的 SHA-256/字节数，以及固定的自述来源（`user_import` / `self_declared`）；正文里**没有**图片字节，也没有时间戳，因此同样输入得到同样清单。
* 图片字节位于该 owner 实例 root 下的 `media/` 目录，文件名只由 owner 从内容摘要产生（`<sha256>.png|jpg`）。调用方永远不能提供路径、URL、文件名、owner 或 authority；读取时文件名、大小与摘要都必须与清单一致，任一不符就不出图。
* 先写媒体文件（临时文件 + `os.replace`，同内容复用），再走现有 `create_artifact` + 首个 `commit_markdown` 的协调事务。数据库事务失败可能留下无引用文件，本版不声称 GC 或物理擦除。
* 只接受 PNG/JPEG，解码后上限 5 MiB。检查 base64 严格性、magic、PNG 的 IHDR/IEND 与 JPEG 的 SOI/首个段/EOI；这是结构与签名检查，不是完整解码或内容净化。浏览器真实解码预览，预览失败不提交。
* Supply 新增四个窄 op：`import_album_image`、`list_album_images`、`read_album_image`、`read_album_image_bytes`。全部沿用原 subject 校验、`COMMIT_MANAGED_ARTIFACT` grant、active workspace 与 `ARTIFACT_EXTERNAL_ACTION` 开关；导入使用现有 `COMMIT_MARKDOWN` 协调命令，不复制第二套治理。
* 传输上限按用途分开：socket **接收帧**可读到 8 MiB，只有 `import_album_image` 的 `validate_request` 放宽到 8 MiB；其余旧 op 仍是原来的 1 MB，超限仍回 `PAYLOAD_TOO_LARGE`。HTTP 只有相册导入路由的 body 上限是 8 MiB，旧 POST 仍为 64 KiB。手机另有独立窄 transport（响应上限 12 MiB），不放宽 `LifeArtifactPort` 的 1 MB 通用响应上限。
* 手机 HTTP：`GET /api/phone/album`（列表，无字节）、`GET /api/phone/album/<artifact_id>`（说明/来源）、`GET /api/phone/album/<artifact_id>/image`（真实字节，先匹配）、`POST /api/phone/album/images`（`{caption, content_type, data, operation_key}`）。三条相册 GET 与其余读接口一样先过 Host/`Origin`/`Sec-Fetch-Site` 闸门：错误来源回 403 JSON 信封，绝不返回图片字节；浏览器同源 `<img>` 正常。图片响应是精确的 `image/png` 或 `image/jpeg`，带 `nosniff`、`no-store`、`no-referrer`、`CORP: same-origin`；仍然只有同源 `img`，不用 blob。
* **清单不是认证，取值必须绑定到真实导入**：读取（列表/详情/字节）除了 subject、`ACTIVE` 与清单形状，还要求该 artifact 的 `CREATE` 操作键在 Governance 里有一条 `CONSUMED` reservation，其 `action_digest` 与“此 subject、此 workspace、此说明、此媒体摘要/字节数”的导入 payload 完全一致，并且当前 head 仍是这次导入的首版。因此把别人的清单复制进普通 artifact、或把已 tombstone 图的清单复制进新 artifact，都不会被当成相册导入；同一 subject 用普通 `create_artifact` 写同样清单也一样被扣留。没有新增表、owner、密钥或第二本账：绑定使用的是既有 `artifact_operations` 与 `permission_reservations` 的只读事实（Workspace owner 已有同类只读先例）。
* 同一个 operation key 指向同一意图：重放先核对原 artifact 仍属于**同一 subject**、仍 `ACTIVE`、head 仍是同一清单、绑定仍在，并且请求携带的 grant 当前仍可用（被撤权/过期/换目标一律拒绝且**不写文件**）；通过后才返回同一工件与 `NO_CHANGE`，不会造第二个版本、也不会把 tombstone 的文件补回来。同 key 不同图片在写文件前就判 `CONFLICT`。不同 key 导入同一图片是两条相册记录，但复用同一份字节（各自都有真实导入绑定）。
* 相册清单**不是可续写文章**：`PrivateProjectService` 的列表/读取/历史/比较/来源/提交/关闭对清单做窄只读排除，`LifeArtifactPort.latest_resumable` 跳过清单，手机项目面的类型识别把清单标成 `album` 并禁用续写。排除只依据 owner head 里的结构化清单，且它只是分类，不是认证；subject 与生命周期检查照旧。
* `ARCHIVED`/`TOMBSTONED` 后当前说明、字节与列表都扣留，但媒体文件不物理删除。

## Alternatives considered

- **只读 fixture 图库**：最快验证版式和点击，且完全不需要写路径；但实际素材来源不存在，仍要新的读取契约，也不满足“用户导入、刷新/重启仍在”的产品目标，因此只允许用于验收环境，不作为交付。
- **图片 base64 塞进文本正文**：可复用全部旧写端口，文件与元数据天然一致；但会把二进制伪装成可续写文章、浪费读上下文，并混淆媒体权威与文本协议，否决。
- **手机独立媒体数据库**：可以单独迭代、不触 Supply 内部；但会成为第二 owner/私人真相源，违背 phone API 单 owner 的既定决定，否决。
- **给 canonical schema 加媒体表**：类型最干净、查询最高效；但首版只需“一图一不可变引用”，迁移承重面超过本块，且会触发 canonical schema 迁移的停止条件，否决；若以后必须迁移则单独决策，不默默替换。

## Consequences

收益：相册图片有真实字节、真实 owner、真实幂等与生命周期；同一图片的字节可复用；手机只经 owner 端口取图，不扫目录、不读 Supply 盘或 DB；项目面与 Life 续作不会把清单当文章。

代价与边界：

* 多介质写入（文件 + SQLite）没有跨介质原子事务：只承诺“先文件后引用”，数据库失败可能留孤儿文件，不声称 GC、回滚或跨备份防复活；EP-09 的备份恢复限制不因相册而消失。
* 签名/结构检查不是完整解码、不是内容净化，也不擦除 EXIF/位置/拍摄时间；图片内容不被当成亲历事实，也不自动写 Memory 或经历。
* 一个列表/项目读可能为分类而多读若干 head，已按页大小乘小倍数封顶；相册列表最多返回 200 条并如实标 `bounded`。
* `read_album_image_bytes` 的 base64 响应约 6.7 MiB，占用手机相册窄通道的 12 MiB 上限；通用 Life 通道没有放宽。

## Testing

真实链证据（WSL，`/tmp/rc104-venv`）：

* `components/supply` 下 `python -m unittest -v lifesupply.tests.test_album_images`：20 项通过（含真实 socket、>1 MiB 图片走完整帧、旧 op 1 MB 上限、同 key 重放/异意图冲突、跨 subject、tombstone 扣留、同根重启；返修新增两个 subject 各带真实 workspace/grant 的伪造清单、tombstone 清单复活、异 subject 重放、撤权重放、tombstone 重放）。
* 仓库根 `python -m unittest -v tests.test_v04_album_images`：8 项通过（真实 Supply owner + PhoneService + loopback HTTP；CSRF/字段注入、大图完整往返、真实字节与响应头、三条相册 GET 的 Host/Origin/Sec-Fetch 403 与同源 `<img>` 可用、tombstone 后 404、不进入项目/`latest_resumable`、同根完全停启后读回）。
* 相关回归：`tests.test_v04_album_images tests.test_phone_api tests.test_phone_projects_api tests.test_v04_work_revisit_history tests.test_v04_work_revisit_writer_chain` 共 49 项通过；Supply 既有 owner 套件按原 discover 命令复跑 142 项通过。
* 手机前端 `node --test tests/test_phone_album_ui.mjs` 12 项通过；既有 `test_phone*.mjs` 共 133 项通过。
* 浏览器验收返修：父级 Edge 真实导入 PNG/JPEG 后，`<img src>` 实际是 `/album/<id>/image`，
  而服务只挂 `/api/phone/album/<id>/image`，缩略图与大图 404；旧 Node 测试恰好断言了错误地址。
  现新增纯函数 `albumImageSrc`（`API_BASE + albumImagePath`）并让 `albumPicture` 的真实 `<img>`
  走它；`request()` 仍用相对路径、只补一次前缀，metadata 路径未改。测试用 stub DOM 真实渲染列表与详情，
  断言两处 `img.src` 都是完整同源地址；把接线临时还原为旧路径时该断言实跑红
  （actual `/album/artifact:abc/image` vs expected `/api/phone/album/artifact:abc/image`），
  恢复后绿。该 harness 不加载图片、不做布局，浏览器网络与像素仍由父级复验。
* 预览夹具 `scripts/phone_album_owner_fixture.py` 每次运行绑定新 generation socket，停止不删旧 socket；新根与同根重启 `--check` 均为 `album_count=0`、exit 0；既有目录缺少有效 `instance.json` 时 exit 2 拒绝；同根重启只更新 `supply_socket` 绑定，不重 seed。真实浏览器文件选择、预览、保存与刷新由监工独立验收。
