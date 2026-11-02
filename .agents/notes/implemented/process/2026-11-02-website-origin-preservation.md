# Agent Note: 官网源站同步保留现网导航修复与 WebP 资源

Status: implemented

## Problem

仓库里的 `website/app.js` 落后于现网：现网 `nyairo.com` 已修复移动端菜单 bootstrap 时序与选择文档后的自动关闭，仓库版仍是旧的 `DOMContentLoaded` 写法，直接部署仓库版会把现网修复回退。现网 `index.html` 还引用了 4 个 WebP 品牌/页脚图，仓库 v0.5.1 版仍是 PNG。另外，本站真实域名由香港源站 `/var/www/nyairo` 提供，gh-pages 只是镜像，不能拿 gh-pages 更新冒充公网已更新。

## Decision

同步时以现场文件为准先核增量，而不是整文件覆盖：

- `website/app.js` 对齐现网基线（仅允许 LF 行尾），保留现网完整导航行为，不发明新行为；
- `website/index.html` 在已交 v0.5.1 内容上只替换基线的 4 个 WebP 引用（`nyairo-icon-light-66.webp`、`nyairo-icon-66.webp`、`nyairo-horizontal-light-72.webp`、`nyairo-horizontal-72.webp`），并把 `app.js` 缓存参数改为 `20261102-origin-navfix`，避免同缓存键换字节；v0.5.1 入口、安装版本与 v0.4 历史正文保持不动；
- 现网 VPS 上的 `install.sh` 仍是 v0.4.0，本单源码不改 installer；但父级本次源站部署的六文件白名单包含已验收的 v0.5.1 installer，源站旧 v0.4 installer 将一并更新；
- 四个 WebP 图像已按现网同名文件逐字节纳入仓库 `website/assets/`，以免下次部署缺图。

## Alternatives considered

**直接用现网 `index.html` 整文件覆盖本地。** 图像引用会自动对齐，但现网首页仍是 v0.4.0 旧版，整文件覆盖会把已交付的 v0.5.1 入口、安装命令和 changelog 一起回退，损失大于收益，否决。

**直接从仓库 app.js 部署，只补菜单的一两行。** 改动最小，但现网 app.js 的改动是成套的（bootstrap 时序、侧栏链接点击关闭、`routeHash`/`loadDoc` 的 `autoCloseSidebar`、toggle 的 `pushState` 分支、初始化去重），逐行挑选容易漏拍并制造新的中间态，否决；整文件对齐现网才是“保留现网修复”。

**以 gh-pages 分支更新作为官网已更新的证据。** 省钱省事，但真实域名走 Cloudflare 到香港独立源站，gh-pages 成功不代表 `nyairo.com` 内容变了，否决。

### 既有笔记检索

- [源码推送与官网发布边界](2026-10-17-v03-source-publication-boundary.md)：部分重叠；发布白名单、单写者隔离、上线前备份与公开目录外核对继续适用。
- [v0.3.0 发行版本指针更新](2026-10-19-v03-release-install-pointer.md)：部分重叠；版本指针一致性与“不改写旧历史验收”继续适用，本笔记不改变其结论。

## Consequences

- 收益：现网移动菜单修复不因部署回退，WebP 资源引用与现网一致；缓存键变更确保浏览器拿到新 `app.js`。
- 代价：`website/index.html` 与现网首页在版本内容上仍不一致（现网是 v0.4.0）；`website/install.sh` 源码侧未改，其与现网 v0.4.0 的差异由父级本次源站六文件白名单部署一并消除。
- 本笔记只记录源码侧同步事实，不代表父级已完成源站部署、浏览器点验或公网已更新。
