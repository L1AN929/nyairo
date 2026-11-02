# Agent Note: 私人日记的本地能力凭据 —— 每次 serve 轮换，无凭据 fail closed

Status: implemented

## Problem

日记正文读取此前只由客户端自填的 `X-Phone-Actor: model` 决定（`server.py` 的 `_actor()`），
CSRF token 又从 `GET /api/phone/state` 发给任何回环客户端，只是跨站防护，不是调用者身份。
写入侧已有 Governance grant + `WriterFence`，但读正文和“主体写入”本身没有服务端可核验的
凭据。方案与停止门见
[private-diary-authority-options-20261027.md](../../../../docs/engineering/private-diary-authority-options-20261027.md)
和旧 note [phone-diary](2026-10-26-phone-diary.md)。

## Decision

1. **每次 serve 签发一个不透明凭据**：`components/phone/capability.py` 用 `secrets.token_urlsafe(32)`
   生成，绑定本实例 `subject` 与**实际绑定的** `http://127.0.0.1:<port>`，写入实例根下的
   `run/phone-capability.json`。文件用 `O_EXCL|O_NOFOLLOW` 临时文件 + `fsync` + `os.replace`
   原子替换，真实 POSIX 下必须是 0600；目录不得被 group/other 写。serve 正常停止时删除，
   重启重新签发；服务端只与内存中的当前 token 常量时间比较，所以旧 token 一律拒绝。
2. **服务端只在日记私人面校验**：`GET /api/phone/diary/<id>` 在 actor 声明为 `model` 时必须带
   有效凭据，缺失/错误/本 serve 不可用分别返回 403 `CAPABILITY_REQUIRED` /
   `CAPABILITY_INVALID` / `CAPABILITY_UNAVAILABLE`，**不降级成“成功的空正文”**；
   operator（缺失/非 model actor）仍是元数据 + `body_withheld: true`。
   `POST /api/phone/diary` 在读取 body、调用 owner 之前就要凭据；缺失时零条目、零版本。
   `X-Phone-Actor` 保留为“请求意图”记录，不再单独决定私人正文/主体写权限。
3. **正式工具从文件读取**：`chiyo_phone` 的 profile `phone.capability_file` 给出路径；省略时
   默认 `<phone.profile_home>/run/phone-capability.json`。每次调用重新读文件（不缓存旧
   token），发送前校验文件 `endpoint` 等于 profile 的 loopback `base_url`、`subject` 等于
   `/state` 公布的主体；不匹配就拒绝，不发请求。只有 `diary_read` / `diary_write` 附
   `Authorization: Bearer`，其它动作不带；HTTP 重定向一律不跟随，token 不会发给别的地址。
4. **不能安全存放就不签**：Windows（本项目不核验 NTFS ACL）、目录可被他人写、权限位不满足、
   同一实例根已有 live serve 持有 `flock` 时，serve 不写凭据，`capability_status` 具名
   `UNAVAILABLE_*`，只有私人日记 fail closed；普通元数据和其它手机功能继续可用，也不会
   留下半写的 secret。
5. **范围只到日记边界**：不改 Memory 准入/删除权威、不改 Supply grant/fence/CAS、不新增
   owner/数据库/身份 issuer、不扩到 gateway DM、不把相册或其它手机 API 改成私有。

## Alternatives considered

- **继续用 `X-Phone-Actor` + 一个新的客户端自填 header** —— 否决：仍是客户端自证，
  服务端没有任何可核验的秘密。
- **用 Unix socket 的 `SO_PEERCRED` 当身份** —— 否决：模型工具与电话服务通常同 uid，
  同 uid 的任意本地进程都能连；最多是传输硬化，不能证明主体身份。
- **把凭据同时绑定 gateway DM 会话** —— 本轮否决：方案 B 已批准只绑定 subject + 实例
  endpoint；DM 绑定会把认证合同扩到本单之外。
- **在 Windows 上写一个 0600 文件并声称安全** —— 否决：没有核验 NTFS ACL 就不能声称
  私有；此时不写凭据、私人日记 fail closed，比“看起来有权限”的假安全更诚实。
- **每次请求都从 `/state` 下发 token 或把 token 放进 HTML/URL** —— 否决：那等于把
  凭据发给任何回环客户端，和 CSRF 一样不构成身份。

## Consequences

- 回环 HTTP 上的普通客户端、浏览器、只伪造 `X-Phone-Actor` 的请求不再能读日记正文或写主体
  日记；但**能读 0600 文件、同 uid 的本地进程、能读该文件的管理员仍在保护边界外**。
  这提升的是“任意声明 → 持有本次 serve 的实例凭据”，不是“仅她可读”，也不承诺 S14/S13
  的全部条目已通过。
- 每次 serve 轮换；同根并发 serve 由 `flock` 保护，后启动者不会覆盖 live 服务的凭据。
  正常停服后工具看到的是文件缺失，私人日记 fail closed；崩溃残留的旧文件会在下次 serve
  被覆盖，旧 token 仍被服务端拒绝。
- 写入侧仍走既有 Life Supply `commit_markdown` 的 Governance grant 与 `WriterFence`；
  本地能力凭据不替代 owner 授权，也不改变 CAS/幂等/回执语义。
- 演示与最小测试：`scripts/private_diary_authority_demo.py`（真实生产 serve + 真实 Supply +
  正式 `phone_handler`，覆盖正常读写、同 key 幂等、operator 扣留、缺/错凭据拒绝且零副作用、
  subject/endpoint 不匹配、重启轮换与旧 token 拒绝、无凭据旧配置 fail closed、受控不安全
  存储不留 secret）；`tests/test_phone_capability.py` 只测凭据文件边界与工具发请求前的
  subject/endpoint 校验。两者都不打印 token 或正文。
