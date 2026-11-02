# Agent Note: 她知道来话属于谁，但“认识谁”不是权限

Status: implemented

## Problem

`LocalContactInbox` 只记“有人来过”，答不出“谁”；native `ParticipantResolver` 只是 Telegram 单通道 JSON 配置（id 手写、无别名/端点/来话关联），不能当实例域注册表。

## Decision

新增实例域窄 owner `components/life/scripts/participant_registry.py`（独立 SQLite、`BEGIN IMMEDIATE`、endpoint 唯一、operation key 幂等、opaque id、来源固定 `operator:cli`、链接前核真实 inbox），手机只读投影 `GET /api/phone/participants[/<id>]` 与两个只读工具动作。operator CLI 主入口 `--instance`：用既有加载器导出 state root/subject/home；调用方自报的 `--state-root/--subject` 只在根上已有同 subject inbox 或既有账本时兼容；写路径 POSIX 核 `euid`/ownership、拒 symlink 与 group/other 可写，Windows 写 fail closed。

## Alternatives considered

把来话列表投影成联系人（无发送者身份，同名/同 source 会串人）、复用 native 单通道配置（缺别名/端点/来源/关联，需改其既有协议）、做全局身份关系中台（混身份与权限，超 F-140）——均否决。

## Consequences

同名不合并、endpoint 不默默改绑、未链接来话保持 `UNRESOLVED`；账本缺失是 `ABSENT`，损坏/错实例是具名不可用。`created_by=operator:cli` 只是来源标签，同 uid 进程/root 与 TOCTOU 残差不在边界内，Windows 写 fail closed；删除/合并、外部账号抓取、关系权限、F-141、模型自然采用未覆盖，不代表 S13 完成。
