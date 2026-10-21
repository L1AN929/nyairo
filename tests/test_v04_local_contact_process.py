#!/usr/bin/env python3
"""v0.4 阶段 A：本地来访端口的**跨进程**并发与并发 claim（真进程，不用线程冒充）。

为什么必须有这一份
------------------
``life_local_contact`` 的每一次写都是"读整份文档 → 改 → 原子替换"。单进程里那是自然的；
真实现场是**两个进程**（Life 循环一轮 + 一个投递方/手机侧），它们可能同时 append。
``_mutate`` 因此在一个**独立的 lock 文件**上取排他锁（POSIX ``flock`` / Windows
``msvcrt.locking``）。这份测试要证明的是那个锁真的存在，而不是"文档说它有"：

1. ``N`` 个真子进程各自 append ``M`` 条不同的事件到同一个 state root，全部退出码 0；
   最后账本里**恰好** ``N*M`` 条、event_ref 无重复、``seq`` 连续 -- 没有丢更新。
2. 两个真子进程同时对**同一条**事件 claim，只有一个拿到 claim，另一个拿到
   ``CONTACT_EVENT_CLAIMED``（而不是两个都以为自己拥有它）。
3. 子进程的 payload 完全一样但 key 由客户端显式给出：仍只有一条事件（跨进程幂等）。

证据边界
--------
* 子进程就是 ``sys.executable -B``，工作目录在本仓库；不联网、不读凭据、不开实例。
* 夹具目录只建不删（不用 ``TemporaryDirectory``）。
* 在 Windows 上跑的是 ``msvcrt`` 那条路径；在 WSL/Linux 上跑的是 ``flock``。两条都要过，
  不是"Windows 上跳过就当通过"。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"),
              str(ROOT / "components" / "life" / "scripts")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

import life_local_contact as lc  # noqa: E402

FIXTURE_ROOT = Path(tempfile.mkdtemp(prefix="nyairov04a-local-contact-proc-"))

SUBJECT = "chiyo"
HOME = "home:0123456789ab"
WHEN = "2026-11-20T09:00:00+00:00"

#: Appends ``count`` distinct events, one process at a time, no intra-process sharing.
APPEND_WORKER = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import life_local_contact as lc
root, tag, count = sys.argv[2], sys.argv[3], int(sys.argv[4])
inbox = lc.LocalContactInbox(root, subject_id=sys.argv[5], home_ref=sys.argv[6])
refs = []
for index in range(count):
    answer = inbox.submit({"event_kind": "LOCAL_MESSAGE",
                           "text": "%s-%d" % (tag, index),
                           "occurred_at": "2026-11-20T09:00:00+00:00"})
    if not answer.get("ok"):
        print(json.dumps({"error": answer}), flush=True)
        raise SystemExit(3)
    refs.append(answer["event_ref"])
print(json.dumps({"refs": refs}), flush=True)
"""

#: Claims one known event ref and reports what happened.
CLAIM_WORKER = r"""
import json, sys, time
sys.path.insert(0, sys.argv[1])
import life_local_contact as lc
root, event_ref, consumer, hold = sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
inbox = lc.LocalContactInbox(root, subject_id=sys.argv[6], home_ref=sys.argv[7])
answer = inbox.claim(event_ref, consumer=consumer)
if answer.get("ok"):
    time.sleep(hold)
print(json.dumps({"ok": answer.get("ok"), "status": answer.get("status"),
                  "reason_code": answer.get("reason_code"),
                  "claim_token": answer.get("claim_token")}), flush=True)
"""


def _root(name: str) -> Path:
    path = FIXTURE_ROOT / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _scripts_dir() -> str:
    return str(ROOT / "components" / "life" / "scripts")


def _spawn(code: str, *args: str) -> subprocess.Popen:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONUTF8"] = "1"
    return subprocess.Popen([sys.executable, "-B", "-c", code, *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            env=env, text=True, encoding="utf-8")


class CrossProcessAppendTest(unittest.TestCase):
    """N 个真进程并发 append：一条都不能丢。"""

    def test_parallel_appends_lose_nothing(self) -> None:
        root = _root("parallel-append")
        inbox = lc.LocalContactInbox(root, subject_id=SUBJECT, home_ref=HOME)
        workers = 4
        per_worker = 6
        processes = [_spawn(APPEND_WORKER, _scripts_dir(), str(root), "w%d" % index,
                            str(per_worker), SUBJECT, HOME)
                     for index in range(workers)]
        collected: list[str] = []
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=180)
            self.assertEqual(proc.returncode, 0,
                             "worker failed: %s\n%s" % (stdout, stderr))
            payload = json.loads(stdout.strip().splitlines()[-1])
            self.assertNotIn("error", payload, payload)
            collected.extend(payload["refs"])
        self.assertEqual(len(collected), workers * per_worker)
        self.assertEqual(len(set(collected)), workers * per_worker,
                         "two processes minted the same event_ref")
        summary = inbox.summary()["data"]
        self.assertEqual(summary["event_count"], workers * per_worker)
        listed = inbox.list_events(limit=lc.MAX_LIST)["data"]
        self.assertEqual(listed["matched"], workers * per_worker)
        sequences = sorted(row["seq"] for row in listed["events"])
        self.assertEqual(sequences, list(range(1, workers * per_worker + 1)),
                         "the append sequence skipped or repeated a number")
        # Every event survived with its own body, and no body was lost to a race.
        bodies = set()
        for event_ref in collected:
            detail = inbox.get_event(event_ref, include_body=True)
            self.assertTrue(detail["ok"], detail)
            bodies.add(detail["data"]["event"]["body"])
        self.assertEqual(len(bodies), workers * per_worker)

    def test_parallel_replay_of_one_client_key_is_one_event(self) -> None:
        root = _root("parallel-replay")
        inbox = lc.LocalContactInbox(root, subject_id=SUBJECT, home_ref=HOME)
        code = r"""
import json, sys
sys.path.insert(0, sys.argv[1])
import life_local_contact as lc
root = sys.argv[2]
inbox = lc.LocalContactInbox(root, subject_id=sys.argv[3], home_ref=sys.argv[4])
answer = inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "同一条",
                       "occurred_at": "2026-11-20T09:00:00+00:00",
                       "operation_key": "contact_op:cross-process-replay-0001"})
print(json.dumps({"ok": answer.get("ok"), "status": answer.get("status"),
                  "event_ref": answer.get("event_ref")}), flush=True)
"""
        processes = [_spawn(code, _scripts_dir(), str(root), SUBJECT, HOME) for _ in range(4)]
        answers = []
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=180)
            self.assertEqual(proc.returncode, 0, "%s\n%s" % (stdout, stderr))
            answers.append(json.loads(stdout.strip().splitlines()[-1]))
        self.assertTrue(all(answer["ok"] for answer in answers), answers)
        refs = {answer["event_ref"] for answer in answers}
        self.assertEqual(len(refs), 1, answers)
        self.assertEqual(inbox.summary()["data"]["event_count"], 1)
        applied = sum(1 for answer in answers if answer["status"] == "applied")
        self.assertEqual(applied, 1, "exactly one process created the event")


class CrossProcessClaimTest(unittest.TestCase):
    """两个真进程同时 claim 同一条事件：只有一个赢。"""

    def test_only_one_process_can_claim(self) -> None:
        root = _root("parallel-claim")
        inbox = lc.LocalContactInbox(root, subject_id=SUBJECT, home_ref=HOME)
        submitted = inbox.submit({"event_kind": "CO_PRESENCE_INVITATION",
                                  "scope": "坐坐", "occurred_at": WHEN})
        event_ref = submitted["event_ref"]
        processes = [_spawn(CLAIM_WORKER, _scripts_dir(), str(root), event_ref,
                            "consumer_%d" % index, "0.4", SUBJECT, HOME)
                     for index in range(2)]
        answers = []
        for proc in processes:
            stdout, stderr = proc.communicate(timeout=180)
            self.assertEqual(proc.returncode, 0, "%s\n%s" % (stdout, stderr))
            answers.append(json.loads(stdout.strip().splitlines()[-1]))
        winners = [answer for answer in answers if answer["ok"]]
        losers = [answer for answer in answers if not answer["ok"]]
        self.assertEqual(len(winners), 1, answers)
        self.assertEqual(len(losers), 1, answers)
        self.assertEqual(losers[0]["reason_code"], "CONTACT_EVENT_CLAIMED")
        # The ledger agrees with the winner, and the attempt count is real.
        claim = inbox.claimed()["data"]["events"]
        self.assertEqual(len(claim), 1)
        self.assertEqual(claim[0]["attempts"], 1)
        self.assertEqual(claim[0]["event_ref"], event_ref)
        self.assertEqual(inbox.summary()["data"]["event_count"], 1)


class LockFileTest(unittest.TestCase):
    """锁文件是**独立**的一条，且只在写的时候出现。"""

    def test_the_lock_is_a_separate_file_and_only_write_paths_take_it(self) -> None:
        root = _root("lock-file")
        inbox = lc.LocalContactInbox(root, subject_id=SUBJECT, home_ref=HOME)
        inbox.summary()
        inbox.list_events()
        inbox.operation("contact_op:000000000000000000000000")
        self.assertEqual(sorted(item.name for item in root.iterdir()), [],
                         "a read created files")
        inbox.submit({"event_kind": "LOCAL_MESSAGE", "text": "写一次",
                      "occurred_at": WHEN})
        names = sorted(item.name for item in root.iterdir())
        self.assertIn(lc.STATE_FILENAME, names)
        self.assertIn(lc.LOCK_FILENAME, names)
        self.assertNotEqual(lc.STATE_FILENAME, lc.LOCK_FILENAME)
        # No leftover temp documents: every write replaces atomically.
        self.assertEqual([name for name in names if name.endswith(".tmp")], [])


if __name__ == "__main__":
    unittest.main()
