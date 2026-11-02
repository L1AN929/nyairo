# Agent Note: 相机链路一次验证用独立夹具，而不是复用旧 demo 的固定产物

Status: implemented

## Problem

相机链已有真机证据（`scripts/phone_camera_demo.py`），但它固定写 `20261027-camera-photo.png` /
`20261027-camera-state.json`，中文链那次还显式复用已证英文（`--reuse-confirmed-translation`）；
`--reuse-last` 会重开旧根。因此它不能用来证明“正式生产 `SceneTranslator` 在本机实时转一次、
出一次图、进新相册”，复跑还会覆盖旧产物。

## Decision

新增 `scripts/phone_camera_chain_once.py`（`serve` / `verify` / `readback`）：`serve` 只接受新根
（非空即拒）并复用旧 demo 的 `Owner`/profile/工具 helper 起真 Supply + 真手机 HTTP；`verify` 只作
客户端，先用 `O_EXCL` 落 `started` 再调用唯一一次 `camera`，之后同一根一律拒绝二次 verify；图片
字节只在校验 sha 后以 `xb` 写新 PNG；`readback` 纯 GET，不 camera、不写 instance。业务源码零改动。

## Alternatives considered

- **给旧 demo 加 `--scene/--root/--photo` 参数**：最强理由是复用已验代码、改动最小。否决原因正是
  要动的固定常量与 `--reuse-last` 语义：为本次验证改它们会碰旧产物路径，且旧单证词会与本单混在一起。
- **新增一条 HTTP 路由或 serve/verify 专用服务端 API**：最强理由是一次进程即可完成。否决原因：
  既有 `camera`/`album`/`album_image` 与 `/api/phone/state` 已经够用，新路由属于对外契约变化。
- **让 verify 自己起 HTTP**：最省父级步骤。否决原因：本单要求服务只由父级 preview 拉起，worker
  不得起长期 HTTP；拆开也让“一次生成”与“只读复核”在进程边界上分开。

## Consequences

- 收益：同一根内可证明“实时转换 → 一次出图 → 相册一行 → 图片字节可读”，且失败/未知/完成都不会被
  第二次 verify 重试；旧根与旧 PNG 不被读改写。
- 代价：多一个夹具脚本与一组单测；父级需先起 18190/7860 与 `camera-chain-once` preview。
- 边界：画面是脚本常量，不是她的选择；不证明自主拍照、自然采用、G1 或 24h；产品模型调用 0。

## Verification

`--help` exit0；`tests/test_phone_camera_chain_once.py` 8 例在 WSL 全过（Windows 7 过 1 skip），
其中 POSIX 冒烟用真 Supply 建新实例并读到相册初始 `count=0`。真实转换与出图未由 worker 执行
（本机转换 0 次、出图 0 次），留给父级 `verify`；预期各最多一次。
