# Agent Note: 外部叫停——停止活动，不抹掉已经发生的效果

Status: implemented

## Problem
本机 operator 需要独立于模型 ABANDON 的停止入口；kill 进程不能代替 owner 结算或证明未执行。

## Decision
独立本地凭据经 phone 邮箱交 resident/LR-2 接纳；QUEUED 不等 PENDING。真实 submit 门读 intent，ATOMIC 经 owner exit、SAFE_BOUNDARY 同 key 重投经 owner 核无在途 Action 后确认，再收 CANCELLED；保留效果和未决 refs。

## Alternatives considered
kill 后强写 FAILED 实现最快，但伪造未知结果且丢失结算；让模型调用 stop 复用最省事，但混淆 operator 权限与自主放弃，均否决。

## Consequences
父级固定 demo exit0：真实跨PID消费、提交拦截、两种 owner 边界、UNKNOWN 原执行对账、真停服重启后旧凭据拒绝。未配 Memory 的隔离根诚实报 FAILED_PENDING_RETRY/WRITE_DISABLED；consumer 类＋替身 port 单测不等 M0 写库。live 检查非线性化、同 uid/admin 不在保护内、Windows fail closed，非自然采用/G1/24h。
