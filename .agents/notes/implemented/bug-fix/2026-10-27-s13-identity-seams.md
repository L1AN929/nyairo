# Agent Note: 撤销授权的协调日志也要记下它属于谁

Status: implemented

## Problem

`REVOKE_GRANT` 的协调记录漏传已有 `subject_id`，无法从记录本身辨明被撤授权属于谁。

## Decision

从 owner 的 grant 行读取 subject，传入既有 UoW 字段，不接受请求自报身份、不改协议。

## Alternatives considered

仅凭 operation_id 最省改动，但单行缺归属；请求传 subject 最直接，却可伪造；新增列最显式，但现有字段已够，均不采用。

## Consequences

真实 socket 验证撤销记录归属正确、伪身份和陈旧 revision 拒绝且零效果；多一次 owner 读取，参与者注册与分支谱系仍未实现。
