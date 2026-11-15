"""阅读响应真正发出的输出上限：显式值优先，否则继承 client 自己的配置。

2026-11-09 父级批准的覆盖修复：正式装配建的是
``AgencyModelClient(..., purpose="reading_response", max_output_tokens=2048)``，外面再包
``BudgetedModelClient``；但 ``ModelReadingResponse(client)`` 的旧默认 512 会在 ``respond()``
里作为显式实参传下去，真正把 2048 覆盖掉。这里验证的是**发送边界**上到底送出了什么。

覆盖四件事：
1. 正式装配链（``autonomous_life_assembly._model_reading_response``，真实
   ``AgencyModelClient`` + 真实 ``BudgetedModelClient``）在只有最后一段 transport 被替身化时，
   实际发给 transport 的 ``max_output_tokens`` 是 2048，不是 512。
2. 显式 override（含更小的 512）继续优先，并同样到达发送边界。
3. 没有该属性的旧替身沿用 512 回落；bool / 非整数（显式或 client 声明）不被猜成有效上限。
4. 合法 ``no_change`` 仍然被解析；本测试不声称「修了 cap 就不会再被截断」。

离线：复用既有 g1 守卫（AF_INET/AF_INET6/DNS/create_connection 硬拒，AF_UNIX 保留）。
scratch 由 mkdtemp 创建并保留，不删除。不发真实请求，不读任何私文。
"""
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
for entry in (REPO, REPO / "scripts", REPO / "components",
              REPO / "components/life/scripts", REPO / "tests"):
    if str(entry) not in sys.path:
        sys.path.insert(0, str(entry))

import autonomous_life_assembly as assembly  # noqa: E402
import subject_reading_response as srr  # noqa: E402
from life_request_budget import LifeRequestBudget  # noqa: E402

CONTEXT = {
    "source_id": "src:offline-cap", "source_revision": "rev:offline-cap", "page": 1,
    "text": "离线的一页正文，不来自任何真实材料。", "previous_page": None, "total_pages": 3,
}
OBSERVED_AT = "2026-11-09T00:00:00+00:00"

#: The synthetic provider facts the assembly resolves into a real AgencyModelClient. No key
#: leaves this process; the transport is replaced below before anything could be sent.
ENV = {
    "NYA02_MODEL_PROVIDER": "custom",
    "NYA02_MODEL_BASE_URL": "https://offline.invalid/v1",
    "NYA02_MODEL_NAME": "scripted-offline",
    "NYA02_MODEL_API_KEY": "offline-placeholder",
    "CHIYO_AUTONOMOUS_RUN_ID": "run:reading-cap",
    "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_UTC_DAY": "300",
    "CHIYO_AUTONOMOUS_MAX_TOTAL_REQUESTS_PER_RUN_WINDOW": "1000",
}


def scratch() -> Path:
    return Path(tempfile.mkdtemp(prefix="reading-response-cap-"))


def budget(root: Path) -> LifeRequestBudget:
    return LifeRequestBudget(root, utc_day_max=20, run_window_max=20,
                             run_id="run:reading-cap", failstop=False)


class RecordingTransport:
    """只替最后一段 transport；正式 client、预算包装器与阅读 writer 都真实执行。"""

    send_split_capable = True
    provider = "offline"
    model = "offline"
    instances: list["RecordingTransport"] = []

    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)
        self.caps: list = []
        self.calls = 0
        type(self).instances.append(self)

    def complete(self, *, system_prompt, user_prompt, timeout_s=None, max_output_tokens=None):
        self.calls += 1
        self.caps.append(max_output_tokens)
        return SimpleNamespace(text='{"kind":"no_change"}', latency_ms=1)


class NoCapClient:
    """旧替身：没有 ``max_output_tokens`` 属性。"""

    def __init__(self):
        self.calls: list = []
        self.cap_values: list = []

    def complete(self, *, system_prompt, user_prompt, timeout_s=None, max_output_tokens=None):
        self.calls.append(True)
        self.cap_values.append(max_output_tokens)
        return SimpleNamespace(text='{"kind":"no_change"}')


class ReadingResponseTokenCapTests(unittest.TestCase):
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

    def _assembled_response(self, root: Path):
        RecordingTransport.instances = []
        with mock.patch("profile_model_transport.OpenAICompatibleTransport",
                        RecordingTransport):
            response = assembly._model_reading_response(ENV, requests=budget(root))
        self.assertIsNotNone(response)
        self.assertEqual(len(RecordingTransport.instances), 1)
        return response, RecordingTransport.instances[0]

    def test_01_formal_assembly_sends_the_client_configured_2048(self):
        response, transport = self._assembled_response(scratch())
        # 构造处观测：继承的是真实 AgencyModelClient 的 2048（BudgetedModelClient 透明透传）。
        self.assertEqual(response.max_output_tokens, 2048)
        out = response.respond(CONTEXT, observed_at=OBSERVED_AT)
        # 发送边界观测：真正传给 transport 的也是 2048，不是旧默认 512。
        self.assertEqual(transport.caps, [2048])
        self.assertEqual(out["kind"], "no_change")

    def test_02_explicit_override_reaches_the_send_boundary(self):
        response, transport = self._assembled_response(scratch())
        # 显式 512（比 2048 小）仍然优先，并走同一条正式 client 链。
        explicit = srr.ModelReadingResponse(response.client, max_output_tokens=512)
        self.assertEqual(explicit.max_output_tokens, 512)
        explicit.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertEqual(transport.caps, [512])

    def test_03_missing_attribute_falls_back_and_illegal_values_are_refused(self):
        # 旧替身没有该属性：沿用 512 回落，并真的送出 512。
        client = NoCapClient()
        response = srr.ModelReadingResponse(client)
        self.assertEqual(response.max_output_tokens, srr.DEFAULT_MAX_OUTPUT_TOKENS)
        response.respond(CONTEXT, observed_at=OBSERVED_AT)
        self.assertEqual(client.cap_values, [512])

        class BoolCapClient(NoCapClient):
            max_output_tokens = True

        class StringCapClient(NoCapClient):
            max_output_tokens = "2048"

        for bad_client in (BoolCapClient(), StringCapClient()):
            with self.assertRaises(srr.ReadingResponseError) as caught:
                srr.ModelReadingResponse(bad_client)
            self.assertIn("MAX_OUTPUT_TOKENS_INVALID", str(caught.exception))

        for value in (True, 0, -1, "512", 512.0):
            with self.assertRaises(srr.ReadingResponseError) as caught:
                srr.ModelReadingResponse(NoCapClient(), max_output_tokens=value)
            self.assertIn("MAX_OUTPUT_TOKENS_INVALID", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
