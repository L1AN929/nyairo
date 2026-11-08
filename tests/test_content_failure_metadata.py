"""内容失败的安全 stage/cap/finish_reason：异常 metadata 与持久 marker 同一份安全事实。

2026-11-06 用户明确批准有限扩展：`bounded_failstop.json` 在原有四键
（``run_id / reason_code / failure_category / at``）之上，允许增加且仅增加三个定型顶层键
``stage / actual_cap / finish_reason``。旧的四键 marker 仍被识别并阻断后续 reserve/send，
不补写、不迁移；非模型 stop（deadline、预算持久化失败等）保持四键原形状。

覆盖四件事：
1. 不同固定 writer 用途失败时，异常 metadata 与持久 marker 记下同一份实际 stage、本次实参
   真正送出的 cap、异常已有的 finish_reason；并用真实阅读 writer 做「实参 512 与构造默认
   2048 不同」的负控。
2. 缺失字段记 UNKNOWN：client 构造 2048 但 complete 未传 cap、异常也没报实发值时必须
   UNKNOWN，绝不退到构造 fallback；已有实发 metadata 优先于调用实参；bool 不算整数。
3. 任意自由文本（异常 message、purpose 字符串、额外 metadata）不进入安全事实；持久 marker
   的键集合精确等于原四键加三项，没有 usage/token 计数或其它字段。
4. failstop 打开时失败同步落锁、下一次 reserve/transport 不再增加；默认关闭时不建 marker、
   异常与旧路径逐字一致；旧四键 marker 未改写入也照旧阻断。

离线：复用既有 g1 守卫（AF_INET/AF_INET6/DNS/create_connection 硬拒，AF_UNIX 保留）。
scratch 由 mkdtemp 创建并保留，不删除。不发真实请求。
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components/life/scripts"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

from agency_model_client import AgencyModelClient, AgencyModelClientError  # noqa: E402
from life_request_budget import (  # noqa: E402
    FAILSTOP_FILENAME,
    FAILURE_DIAGNOSTIC_KEYS,
    REASON_FAILSTOP,
    BudgetedModelClient,
    LifeRequestBudget,
    RequestBudgetError,
)
import subject_reading_response as srr  # noqa: E402

READING_CONTEXT = {
    "source_id": "src:offline", "source_revision": "rev:offline", "page": 1,
    "text": "离线的一页正文，不来自任何真实材料。", "previous_page": None, "total_pages": 3,
}

#: The persisted failstop marker's original contract; old markers stay valid and blocking.
MARKER_KEYS = {"run_id", "reason_code", "failure_category", "at"}
#: The exact approved extension: three typed keys, nothing else.
PERSISTED_KEYS = MARKER_KEYS | set(FAILURE_DIAGNOSTIC_KEYS)


def scratch():
    return Path(tempfile.mkdtemp(prefix="content-failure-"))


def marker_path(root):
    return Path(root) / FAILSTOP_FILENAME


def read_marker(root):
    return json.loads(marker_path(root).read_text(encoding="utf-8"))


def budget(root, *, failstop=True):
    return LifeRequestBudget(Path(root), utc_day_max=20, run_window_max=20,
                             run_id="run:content-failure-metadata", failstop=failstop)


def client(purpose, *, cap, transport):
    """The formal content client, with an injected script transport and a fixed purpose."""

    return AgencyModelClient(
        config={"provider": "offline", "model": "offline", "base_url": "https://offline.invalid/v1",
                "api_key": "offline-placeholder"},
        transport=transport, purpose=purpose, max_output_tokens=cap, timeout_s=5.0,
        request_json_object=True)


class ScriptTransport:
    """Only the transport is replaced; the real client, wrapper and writers still run."""

    send_split_capable = True
    provider = "offline"
    model = "offline"

    def __init__(self, *, failure=None, metadata=None, status=None):
        self.failure = failure
        self.metadata = dict(metadata or {})
        self.status = status
        self.calls = 0
        self.caps = []

    def complete(self, *, system_prompt, user_prompt, timeout_s=None, max_output_tokens=None):
        self.calls += 1
        self.caps.append(max_output_tokens)
        if self.failure:
            raise AgencyModelClientError(
                self.failure, "PRIVATE_SENTINEL prompt completion reasoning title endpoint",
                status=self.status, metadata=self.metadata)
        return SimpleNamespace(text='{"kind":"no_change"}', latency_ms=1)


class BareInner:
    """A client-like object exposing neither a fixed purpose nor a construction cap."""

    def complete(self, **kwargs):
        raise AgencyModelClientError("TRANSPORT_TIMEOUT", "PRIVATE_SENTINEL")


class ContentFailureMetadataTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import g1_real_adoption as guard_mod

        cls._guard = guard_mod._offline_network_guard(scratch())
        cls._guard.__enter__()
        selfcheck = guard_mod._offline_guard_selfcheck()
        wanted = ("inet_refused", "inet6_refused", "dns_refused", "connect_refused", "unix_ok")
        if not all(selfcheck.get(name) for name in wanted):
            cls._guard.__exit__(None, None, None)
            raise AssertionError("offline network guard self-check failed: %r" % (selfcheck,))

    @classmethod
    def tearDownClass(cls):
        cls._guard.__exit__(None, None, None)

    def test_01_actual_stage_cap_and_finish_reason(self):
        # 负控：真实阅读 writer 用 512 调，client 构造默认 2048 —— 记录的必须是实参 512。
        root = scratch()
        transport = ScriptTransport(failure="PROVIDER_OUTPUT_TRUNCATED",
                                    metadata={"finish_reason": "length"})
        writer = srr.ModelReadingResponse(
            BudgetedModelClient(client("reading_response", cap=2048, transport=transport),
                                budget(root)))
        with self.assertRaises(srr.ReadingResponseError) as caught:
            writer.respond(READING_CONTEXT, observed_at="2026-11-06T00:00:00+00:00")
        self.assertEqual(transport.caps, [512])
        cause = caught.exception.__cause__
        self.assertIsInstance(cause, AgencyModelClientError)
        self.assertEqual(cause.metadata["stage"], "reading_response")
        self.assertEqual(cause.metadata["actual_cap"], 512)
        self.assertEqual(cause.metadata["finish_reason"], "length")
        # 持久 marker 精确等于原四键 + 三项，值与异常同源；没有 usage/token 或自由文本。
        marker = read_marker(root)
        self.assertEqual(set(marker), PERSISTED_KEYS)
        self.assertEqual(marker["failure_category"], "OUTPUT_TRUNCATED")
        self.assertEqual(marker["stage"], "reading_response")
        self.assertEqual(marker["actual_cap"], 512)
        self.assertEqual(marker["finish_reason"], "length")

        # 其余固定用途：stage 来自实际 client 构造的 purpose；其中一例实参覆盖构造值。
        for purpose, constructed, called in (("artifact_edit", 512, 900),
                                             ("shared_practice", 700, 700),
                                             ("local_reply", 260, 260)):
            root = scratch()
            transport = ScriptTransport(failure="PROVIDER_OUTPUT_TRUNCATED",
                                        metadata={"finish_reason": "length"})
            wrap = BudgetedModelClient(
                client(purpose, cap=constructed, transport=transport), budget(root))
            with self.assertRaises(AgencyModelClientError) as caught:
                wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=called)
            meta = caught.exception.metadata
            self.assertEqual(meta["stage"], purpose)
            self.assertEqual(meta["actual_cap"], called)
            self.assertEqual(meta["finish_reason"], "length")
            marker = read_marker(root)
            self.assertEqual(set(marker), PERSISTED_KEYS)
            self.assertEqual(marker["stage"], purpose)
            self.assertEqual(marker["actual_cap"], called)
            self.assertEqual(marker["finish_reason"], "length")

    def test_02_missing_fields_are_unknown_not_guessed(self):
        # 核心负控：构造 2048、本次 complete 未传 cap、异常也没报实发值 => UNKNOWN，不是 2048。
        root = scratch()
        wrap = BudgetedModelClient(
            client("reading_response", cap=2048,
                   transport=ScriptTransport(failure="PROVIDER_OUTPUT_TRUNCATED",
                                             metadata={"finish_reason": "length"})),
            budget(root))
        with self.assertRaises(AgencyModelClientError) as caught:
            wrap.complete(system_prompt="s", user_prompt="u")
        self.assertEqual(caught.exception.metadata["actual_cap"], "UNKNOWN")
        marker = read_marker(root)
        self.assertEqual(set(marker), PERSISTED_KEYS)
        self.assertEqual(marker["stage"], "reading_response")
        self.assertEqual(marker["actual_cap"], "UNKNOWN")
        self.assertEqual(marker["finish_reason"], "length")

        # 无实发 metadata 时调用实参有效；非内容用途 stage 不猜。
        root = scratch()
        wrap = BudgetedModelClient(
            client("agency_cognition", cap=512,
                   transport=ScriptTransport(failure="TRANSPORT_TIMEOUT")),
            budget(root))
        with self.assertRaises(AgencyModelClientError) as caught:
            wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=321)
        meta = caught.exception.metadata
        self.assertEqual(meta["stage"], "UNKNOWN")
        self.assertEqual(meta["actual_cap"], 321)
        self.assertEqual(meta["finish_reason"], "UNKNOWN")
        marker = read_marker(root)
        self.assertEqual(set(marker), PERSISTED_KEYS)
        self.assertEqual(marker["stage"], "UNKNOWN")
        self.assertEqual(marker["actual_cap"], 321)
        self.assertEqual(marker["finish_reason"], "UNKNOWN")

        # 已有实发 metadata 优先于调用实参。
        root = scratch()
        transport = ScriptTransport(failure="PROVIDER_OUTPUT_TRUNCATED",
                                    metadata={"finish_reason": "length",
                                              "max_output_tokens_sent": 777})
        wrap = BudgetedModelClient(client("artifact_edit", cap=2048, transport=transport),
                                   budget(root))
        with self.assertRaises(AgencyModelClientError) as caught:
            wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=512)
        self.assertEqual(caught.exception.metadata["actual_cap"], 777)
        self.assertEqual(read_marker(root)["actual_cap"], 777)

        # bool 不是整数：实发 metadata 的 True 不认，退回本次实参；bool finish 记 UNKNOWN。
        root = scratch()
        transport = ScriptTransport(failure="PROVIDER_OUTPUT_TRUNCATED",
                                    metadata={"finish_reason": True,
                                              "max_output_tokens_sent": False})
        wrap = BudgetedModelClient(client("artifact_edit", cap=2048, transport=transport),
                                   budget(root))
        with self.assertRaises(AgencyModelClientError) as caught:
            wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=512)
        marker = read_marker(root)
        self.assertEqual(caught.exception.metadata["actual_cap"], 512)
        self.assertEqual(marker["actual_cap"], 512)
        self.assertEqual(marker["finish_reason"], "UNKNOWN")

        # 既无固定用途也无构造 cap 的入口：三项全部 UNKNOWN，不伪造 0 或 fallback。
        root = scratch()
        with self.assertRaises(AgencyModelClientError) as caught:
            BudgetedModelClient(BareInner(), budget(root)).complete(system_prompt="s",
                                                                    user_prompt="u")
        meta = caught.exception.metadata
        self.assertEqual(meta["stage"], "UNKNOWN")
        self.assertEqual(meta["actual_cap"], "UNKNOWN")
        self.assertEqual(meta["finish_reason"], "UNKNOWN")
        marker = read_marker(root)
        self.assertEqual(set(marker), PERSISTED_KEYS)
        self.assertEqual(marker["stage"], "UNKNOWN")
        self.assertEqual(marker["actual_cap"], "UNKNOWN")
        self.assertEqual(marker["finish_reason"], "UNKNOWN")

    def test_03_free_text_never_enters_the_safe_facts(self):
        secret = "PRIVATE_SENTINEL prompt completion reasoning title endpoint"
        root = scratch()
        transport = ScriptTransport(
            failure="PROVIDER_OUTPUT_TRUNCATED",
            metadata={"finish_reason": secret, "content_chars": 3, "text": secret,
                      "message": secret})
        wrap = BudgetedModelClient(client(secret, cap=512, transport=transport), budget(root))
        with self.assertRaises(AgencyModelClientError) as caught:
            wrap.complete(system_prompt=secret, user_prompt=secret, max_output_tokens=512)

        meta = caught.exception.metadata
        # 未知 purpose 与自由文本 finish_reason 只能记 UNKNOWN；安全计数保留，自由文本键被丢弃。
        self.assertEqual(meta["stage"], "UNKNOWN")
        self.assertEqual(meta["actual_cap"], 512)
        self.assertEqual(meta["finish_reason"], "UNKNOWN")
        self.assertEqual(meta["content_chars"], 3)
        self.assertEqual(set(meta), {"stage", "actual_cap", "finish_reason", "content_chars"})
        self.assertNotIn("PRIVATE_SENTINEL", json.dumps(meta))
        # 持久 marker 精确等于七键，且不含任何自由文本或额外的 text/message 键。
        text = marker_path(root).read_text(encoding="utf-8")
        self.assertNotIn("PRIVATE_SENTINEL", text)
        marker = json.loads(text)
        self.assertEqual(set(marker), PERSISTED_KEYS)
        self.assertEqual(marker["stage"], "UNKNOWN")
        self.assertEqual(marker["actual_cap"], 512)
        self.assertEqual(marker["finish_reason"], "UNKNOWN")

    def test_04_failstop_blocks_next_and_default_is_unchanged(self):
        # 打开 failstop：失败同步写 marker；下一次 complete 在 reserve 前被挡，transport 不增。
        root = scratch()
        transport = ScriptTransport(failure="PROVIDER_OUTPUT_TRUNCATED",
                                    metadata={"finish_reason": "length"})
        ledger = budget(root, failstop=True)
        wrap = BudgetedModelClient(client("artifact_edit", cap=512, transport=transport), ledger)
        with self.assertRaises(AgencyModelClientError):
            wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=512)
        self.assertTrue(marker_path(root).is_file())
        marker = read_marker(root)
        self.assertEqual(set(marker), PERSISTED_KEYS)
        self.assertEqual(marker["stage"], "artifact_edit")
        self.assertEqual(marker["actual_cap"], 512)
        self.assertEqual(marker["finish_reason"], "length")
        self.assertEqual(transport.calls, 1)
        with self.assertRaises(RequestBudgetError) as caught:
            wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=512)
        self.assertEqual(caught.exception.reason_code, REASON_FAILSTOP)
        self.assertEqual(transport.calls, 1)
        with self.assertRaises(RequestBudgetError):
            ledger.reserve("content")

        # 默认关闭：失败照常上抛，不建 marker、不附加诊断，下一次 reserve/transport 与旧路径一致。
        root = scratch()
        transport = ScriptTransport(failure="PROVIDER_OUTPUT_TRUNCATED",
                                    metadata={"finish_reason": "length"})
        ledger = budget(root, failstop=False)
        wrap = BudgetedModelClient(client("artifact_edit", cap=512, transport=transport), ledger)
        with self.assertRaises(AgencyModelClientError) as caught:
            wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=512)
        self.assertFalse(marker_path(root).exists())
        # 默认路径逐字不变：异常 metadata 仍是 transport 原样，没有新增 stage/actual_cap。
        self.assertEqual(caught.exception.metadata, {"finish_reason": "length"})
        ledger.reserve("content")
        with self.assertRaises(AgencyModelClientError):
            wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=512)
        self.assertEqual(transport.calls, 2)

        # 旧四键 marker：不补写、不迁移，但照旧识别并在 reserve/transport 之前阻断。
        root = scratch()
        old = {"run_id": "run:old", "reason_code": REASON_FAILSTOP,
               "failure_category": "OUTPUT_TRUNCATED",
               "at": "2026-11-01T00:00:00+00:00"}
        marker_path(root).write_text(json.dumps(old), encoding="utf-8")
        transport = ScriptTransport(failure="PROVIDER_OUTPUT_TRUNCATED",
                                    metadata={"finish_reason": "length"})
        wrap = BudgetedModelClient(client("artifact_edit", cap=512, transport=transport),
                                   budget(root))
        with self.assertRaises(RequestBudgetError) as caught:
            wrap.complete(system_prompt="s", user_prompt="u", max_output_tokens=512)
        self.assertEqual(caught.exception.reason_code, REASON_FAILSTOP)
        self.assertEqual(transport.calls, 0)
        self.assertEqual(read_marker(root), old)

        # 非模型 deadline stop：marker 保持四键原形状，不带三项诊断。
        root = scratch()
        ledger = LifeRequestBudget(Path(root), utc_day_max=20, run_window_max=20,
                                   run_id="run:deadline", failstop=True,
                                   request_deadline="2026-11-01T00:00:00+00:00")
        with self.assertRaises(RequestBudgetError):
            ledger.check_failstop()
        marker = read_marker(root)
        self.assertEqual(set(marker), MARKER_KEYS)
        self.assertEqual(marker["failure_category"], "DEADLINE_REACHED")


if __name__ == "__main__":
    unittest.main()
