"""v0.4 C: 连续生活入口（``scripts/live_day.py`` / ``components.phone.live_day``）的测试。

两部分，分工明确：

* ``LiveDaySummaryTest`` / ``SegmentCapTest`` / ``LiveDayRefusalTest``：不依赖 POSIX 的纯读与算术
  —— 分段请求上限只能压低、报告只从既有记录重建、``status`` 真的不写盘、有界输入被拒。
* ``LiveDayContinuousSegmentTest``（仅 POSIX）：**同一个真实手机实例**上的连续几段生活。owner 是
  真的（``demo.prepare_demo`` 建实例、``demo.serve_instance`` 起 Supply/World 子进程），循环是真的
  （``phone.life.life_run`` 里的 ``AutonomousLifeResident`` / cycle / runner / ar0），模型只是本机
  loopback 的**脚本化原始 JSON 替身**：它证明的是路线可重复，**不是**"她自己过了一天"。

夹具目录一律保留在磁盘上（不删、不清理、不用 ``TemporaryDirectory`` 自动清理）。
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for entry in (str(ROOT), str(ROOT / "components"), str(ROOT / "components" / "life" / "scripts"),
              str(ROOT / "components" / "world" / "scripts"),
              str(ROOT / "components" / "world" / "service"),
              str(ROOT / "components" / "world" / "service" / "bridge"),
              str(ROOT / "components" / "supply")):
    if entry not in sys.path:
        sys.path.insert(0, entry)

from components.phone import life as phone_life  # noqa: E402
from components.phone import live_day  # noqa: E402

posix_only = unittest.skipIf(
    os.name == "nt", "需要 POSIX flock 与 AF_UNIX（在 WSL/Linux 上运行）")

#: A private-looking marker that must never appear in the operator summary.
PRIVATE_TEXT = "她写给自己的私密段落-SHOULD-NOT-APPEAR"


def _fixture_root(prefix: str) -> Path:
    return Path(tempfile.mkdtemp(prefix=prefix))


def _write(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


def _jsonl(rows) -> str:
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


class _FakeBudget:
    """The budget object's surface that the segment cap reads (nothing else)."""

    def __init__(self, *, day_used=0, window_used=0, day_max=96, window_max=96,
                 state="ok", run_id="run:test") -> None:
        self.utc_day_max = day_max
        self.run_window_max = window_max
        self._view = {"state": state, "utc_day_calls": day_used,
                      "run_window_calls": window_used, "run_id": run_id}

    def view(self):
        return dict(self._view)


class SegmentCapTest(unittest.TestCase):
    """``segment_request_cap`` 只能把这一段的请求上限压低，绝不提高。"""

    def _resident(self, cap):
        module = phone_life._life_modules()["resident"]
        return module.AutonomousLifeResident(
            state_root=_fixture_root("live-day-cap-"), environment={},
            poll_seconds=60, act=False, segment_request_cap=cap)

    def test_the_cap_is_used_plus_a_lower_ceiling_and_never_a_raise(self):
        resident = self._resident(6)
        budget = _FakeBudget(day_used=10, window_used=10, day_max=96, window_max=96)
        self.assertIsNone(resident._apply_segment_cap(budget))
        self.assertEqual(budget.utc_day_max, 16)
        self.assertEqual(budget.run_window_max, 16)
        applied = resident.segment_cap
        self.assertTrue(applied["applied"])
        self.assertFalse(applied["raised_any_ceiling"])
        self.assertEqual(applied["utc_day"]["used_at_start"], 10)
        self.assertEqual(applied["run_window"]["applied_max"], 16)
        # A ceiling the instance already had that is *tighter* than used+cap must not be raised.
        tight = self._resident(6)
        tight_budget = _FakeBudget(day_used=10, window_used=10, day_max=3, window_max=3)
        self.assertIsNone(tight._apply_segment_cap(tight_budget))
        self.assertEqual(tight_budget.utc_day_max, 3)
        self.assertEqual(tight_budget.run_window_max, 3)

    def test_no_cap_is_a_no_op(self):
        resident = self._resident(None)
        budget = _FakeBudget(day_max=7, window_max=9)
        self.assertIsNone(resident._apply_segment_cap(budget))
        self.assertEqual((budget.utc_day_max, budget.run_window_max), (7, 9))

    def test_an_unreadable_ledger_refuses_the_segment(self):
        resident = self._resident(4)
        corrupt = _FakeBudget(day_max=96, window_max=96, state="corrupt")
        corrupt._view["utc_day_calls"] = None
        refused = resident._apply_segment_cap(corrupt)
        self.assertIsNotNone(refused)
        self.assertEqual(refused["reason"], "SEGMENT_REQUEST_CAP_UNVERIFIED")
        #  Corrupt means "cannot promise", so the ceilings must be left exactly as they were.
        self.assertEqual(corrupt.utc_day_max, 96)

    def test_a_missing_budget_refuses_the_segment(self):
        resident = self._resident(4)
        refused = resident._apply_segment_cap(None)
        self.assertIsNotNone(refused)
        self.assertEqual(refused["reason"], "SEGMENT_REQUEST_CAP_UNVERIFIED")


class LiveDaySummaryTest(unittest.TestCase):
    """The operator summary is rebuilt from records and keeps "unknown" honest."""

    def setUp(self) -> None:
        self.state_root = _fixture_root("live-day-summary-")
        _write(self.state_root / "resident_summary.json", {
            "kind": "autonomous_life_resident", "running": False,
            "started_at": "2026-10-19T10:00:00+00:00",
            "this_process_started_at": "2026-10-19T10:00:00+00:00",
            "stopped_at": "2026-10-19T10:05:00+00:00", "starts": 2, "ticks": 4,
            "interval_minutes": 30,
            "baseline": {
                "foreground_activity_ref": "activity:7",
                "work": {"artifact_id": "artifact:abc", "title": "她自己的日常记录",
                         "head_version_id": "artifact-version:9", "version_number": 3,
                         "content_chars": 120, "resumable": True},
                "work_selection": {"selected_by": "BOUND_WORK"},
            },
            "counters": {"owner_confirmed_versions": 1,
                         "owner_version_refs": {"artifact-version:9": 1},
                         "owner_version_unknown": 0},
            "budget": {"requests": {"state": "ok", "run_id": "run:one-day",
                                    "utc_day": "2026-10-19", "utc_day_calls": 3,
                                    "utc_day_max": 9, "run_window_calls": 3,
                                    "run_window_max": 9, "windows": {"run:one-day": 3}},
                       "cognition_used_today": 2},
            "model_calls_detail": {"total_requests": 3, "total_source": "PERSISTED_REQUEST_LEDGER",
                                   "cognition_calls": 2, "content_generation_calls": 1},
            "segment_request_cap": {"requested": 6, "applied": True, "raised_any_ceiling": False},
        })
        _write(self.state_root / "autonomous_events.json", {
            "last_check_at": "2026-10-19T10:04:00+00:00", "last_event_at": None})
        _write(self.state_root / "autonomous_switch.json", {"enabled": True})
        _write(self.state_root / "resident_journal.jsonl", _jsonl([
            {"at": "2026-10-19T10:04:00+00:00", "tick": 3, "events": [], "due": {"status": "NO_ACTION"}},
            {"at": "2026-10-19T10:05:00+00:00", "tick": 4, "events": [],
             "due": {"status": "NOT_DUE"}, "journey": {"journey_status": "none"}},
        ]))
        _write(self.state_root / "autonomous_wakes.jsonl", _jsonl([
            {"at": "2026-10-19T10:03:00+00:00", "kind": "CHECK_WINDOW", "status": "NOT_DUE",
             "status_class": "silence"},
            {"at": "2026-10-19T10:04:00+00:00", "kind": "CHECK_WINDOW", "status": "NO_ACTION",
             "status_class": "choice", "outcome": "NO_ACTION", "detail": PRIVATE_TEXT},
            {"at": "2026-10-19T10:04:30+00:00", "kind": "EVENT", "status": "ACTED",
             "status_class": "acted", "outcome": "ACTED", "llm_calls": 1,
             "activity_ref": "activity:7", "action_id": "action:1", "action_status": "ACTED"},
            {"at": "2026-10-19T10:04:50+00:00", "kind": "EVENT", "status": "QUIET",
             "status_class": "silence"},
            {"at": "2026-10-19T10:05:00+00:00", "kind": "CHECK_WINDOW", "status": "NOT_DUE",
             "status_class": "silence"},
        ]))

    def _summary(self, **kwargs):
        return live_day.segment_summary(
            ROOT, phone={}, nested={}, state_root=self.state_root, environment={},
            segment_started_at="2026-10-19T10:02:00+00:00", limit=8, **kwargs)

    def test_records_are_rebuilt_and_unknown_is_named(self):
        summary = self._summary()
        self.assertEqual(summary["schema"], live_day.SCHEMA_SUMMARY)
        # no live owner read at all -> an honest unknown with a reason, never an invented place
        self.assertEqual(summary["where"]["status"], "UNKNOWN")
        self.assertEqual(summary["where"]["source"], "UNKNOWN")
        self.assertTrue(summary["where"]["unknowns"])
        self.assertEqual(summary["hands"]["status"], "UNKNOWN")
        # silence and a real consideration are counted apart; a fault never lands in "silence"
        self.assertEqual(summary["categories"]["silence"], 3)
        self.assertEqual(summary["categories"]["her_choice"], 1)
        self.assertEqual(summary["categories"]["acted"], 1)
        self.assertEqual(summary["categories"]["fault"], 0)
        # "doing" ignores the trailing NOT_DUE/QUIET silence and reports the considered wake
        self.assertEqual(summary["doing"]["status"], "READY")
        self.assertEqual(summary["doing"]["status_word"], "ACTED")
        self.assertEqual(summary["doing"]["activity_ref"], "activity:7")
        self.assertEqual(summary["doing"]["last_silence"]["status"], "NOT_DUE")
        # the stop point comes from the owner's own head/baseline, never from memory
        self.assertEqual(summary["stop_point"]["artifact_id"], "artifact:abc")
        self.assertEqual(summary["stop_point"]["head_version_id"], "artifact-version:9")
        self.assertEqual(summary["stop_point"]["selected_by"], "BOUND_WORK")
        # the next consideration is the recorded window, and earlier only for a real change
        self.assertEqual(summary["next"]["status"], "READY")
        self.assertEqual(summary["next"]["check_window_at"], "2026-10-19T10:34:00+00:00")
        self.assertIn("WORLD_OPPORTUNITY_CHANGED", summary["next"]["earlier_if"])
        # this segment's spend / the same run window / the cap that was really applied
        self.assertEqual(summary["changed"]["llm_calls"], 1)
        self.assertEqual(summary["changed"]["budget"]["run_window_calls"], 3)
        self.assertEqual(summary["changed"]["budget"]["run_window_max"], 9)
        self.assertEqual(summary["changed"]["budget"]["windows"], {"run:one-day": 3})
        self.assertEqual(summary["changed"]["segment_request_cap"]["requested"], 6)
        self.assertEqual(summary["changed"]["owner_versions"], 1)
        self.assertEqual([row["tick"] for row in summary["changed"]["ticks"]], [3, 4])
        self.assertFalse(summary["limits"]["private_text_included"])
        # private text never travels: only reason/detail *codes* are kept, and this one is clipped
        blob = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn(PRIVATE_TEXT, blob)
        self.assertNotIn('"content"', json.dumps(summary["stop_point"], ensure_ascii=False))

    def test_a_live_owner_context_is_used_as_data(self):
        summary = self._summary(live_context={
            "status": "available", "source": "provider_daily_life_context",
            "observed_at": "2026-10-19T10:05:00+00:00",
            "location": {"place_id": "cafe", "area_id": "counter", "name": "海边咖啡馆",
                         "area_name": "吧台", "kind": "shop", "here": True},
            "body": {"pose": "sitting", "orientation": "north",
                     "hands": {"left_hand": {"object_id": "item-1"}, "right_hand": None},
                     "free_slots": ["right_hand"], "hands_usable": True},
            "nearby": [{"object_id": "item-1", "label": "饭团", "in_hand": True,
                        "slot_id": "left_hand", "takeable": False}],
            "opportunities": [{"verb": "MOVE", "target_ref": "place:street_1",
                               "label": "去街上"}],
            "unknowns": [{"code": "places_not_observed", "detail": "远处没看"}],
        })
        self.assertEqual(summary["where"]["status"], "READY")
        self.assertEqual(summary["where"]["place_name"], "海边咖啡馆")
        self.assertEqual(summary["where"]["area_name"], "吧台")
        self.assertEqual(summary["where"]["source"], "provider_daily_life_context")
        self.assertIn("places_not_observed", summary["where"]["unknowns"])
        self.assertEqual(summary["hands"]["status"], "READY")
        self.assertEqual(summary["hands"]["items"], {"left_hand": "饭团", "right_hand": None})
        self.assertEqual(summary["hands"]["holding"], ["饭团"])
        self.assertEqual(summary["opportunities"][0]["label"], "去街上")

    def test_the_bounded_tail_reader_never_reads_the_whole_file(self):
        path = self.state_root / "big.jsonl"
        _write(path, _jsonl([{"i": index} for index in range(3000)]))
        rows = live_day._read_jsonl_tail(path, 3)
        self.assertEqual([row["i"] for row in rows], [2997, 2998, 2999])

    def test_a_status_read_creates_nothing(self):
        empty = _fixture_root("live-day-empty-")
        before = sorted(item.name for item in empty.iterdir())
        report = live_day.status(empty)
        self.assertFalse(report["ok"])
        self.assertEqual(report["exit_code"], phone_life.EXIT_REFUSED)
        self.assertEqual(report["services_started"], 0)
        self.assertTrue(report["read_only"])
        self.assertEqual(sorted(item.name for item in empty.iterdir()), before)


class LiveDayRefusalTest(unittest.TestCase):
    """A segment is always finite, and a bad bound is refused before anything starts."""

    def test_unbounded_is_refused(self):
        report = live_day.run_segment(Path(tempfile.mkdtemp(prefix="live-day-none-")),
                                      duration_seconds=None, max_ticks=None)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "FINITE_BOUND_REQUIRED")
        self.assertFalse(report["bounds"]["unbounded"])

    def test_out_of_range_values_are_refused(self):
        root = Path(tempfile.mkdtemp(prefix="live-day-range-"))
        self.assertEqual(live_day.run_segment(root, duration_seconds=0)["reason"],
                         "DURATION_OUT_OF_RANGE")
        self.assertEqual(
            live_day.run_segment(root, duration_seconds=live_day.MAX_DURATION_SECONDS + 1)["reason"],
            "DURATION_OUT_OF_RANGE")
        self.assertEqual(live_day.run_segment(root, duration_seconds=60, max_ticks=0)["reason"],
                         "MAX_TICKS_INVALID")
        self.assertEqual(
            live_day.run_segment(root, duration_seconds=60, max_new_requests=0)["reason"],
            "REQUEST_CAP_INVALID")
        self.assertEqual(
            live_day.run_segment(root, duration_seconds=60, poll_seconds=0)["reason"],
            "POLL_INVALID")
        self.assertEqual(
            live_day.run_segment(root, duration_seconds=60, start_services="sometimes")["reason"],
            "SERVICE_MODE_UNKNOWN")

    def test_a_missing_instance_is_refused_without_starting_a_service(self):
        report = live_day.run_segment(Path(tempfile.mkdtemp(prefix="live-day-noinstance-")),
                                      duration_seconds=60)
        self.assertFalse(report["ok"])
        self.assertEqual(report["services"]["mode"], "auto")
        self.assertFalse(report["services"]["started"])
        self.assertEqual(report["processes_started"], 0)


class _StubModel:
    """A loopback OpenAI-compatible stand-in that answers the two purposes with raw JSON.

    It is deliberately explicit: a *scripted* decision, not her judgement.  It reads only the
    same fields a real model reads (``candidates`` / ``legal_decision_enums`` /
    ``allowed_reason_codes``) so the chain it exercises is the real one.
    """

    def __init__(self) -> None:
        self.requests: list[dict] = []
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def do_POST(self):  # noqa: N802 - http.server naming
                length = int(self.headers.get("Content-Length") or 0)
                try:
                    body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
                except ValueError:
                    body = {}
                payload = json.dumps(stub.reply(body), ensure_ascii=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *_args):
                return

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    @property
    def base_url(self) -> str:
        return "http://127.0.0.1:%d/v1" % self.port

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()

    def reply(self, body: dict) -> dict:
        user = "".join(str(message.get("content") or "") for message in body.get("messages") or []
                       if message.get("role") == "user")
        try:
            prompt = json.loads(user)
        except ValueError:
            prompt = None
        self.requests.append({"is_prompt": isinstance(prompt, dict)})
        if isinstance(prompt, dict) and "candidates" in prompt:
            allowed = [str(code) for code in (prompt.get("allowed_reason_codes") or [])]
            legal = [str(item) for item in (prompt.get("legal_decision_enums") or [])]
            decision = "NO_ACTION" if (not legal or "NO_ACTION" in legal) else "DEFER"
            text = json.dumps({"decision": decision, "selected_candidate_id": None,
                               "reason_codes": allowed[:1] or ["REST_OK"],
                               "short_rationale": "stub: nothing worth doing",
                               "deferred_candidate_ids": []}, ensure_ascii=False)
        else:
            text = json.dumps({"kind": "append_paragraph", "text": "（替身一小段）",
                               "self_note": "stub"}, ensure_ascii=False)
        return {"choices": [{"message": {"role": "assistant", "content": text}}],
                "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8}}


@posix_only
class LiveDayContinuousSegmentTest(unittest.TestCase):
    """One real instance, several consecutive segments, one scripted model stand-in.

    What is real: the phone instance and its world package, the Supply/World owner children
    (``demo.serve_instance``), the Activity/AR-0 owners, the resident, the runner, the cycle and
    the persisted request ledger.  What is scripted: the model's answers.  So this proves the
    *route* is continuous and repeatable; it is not evidence that she lived an autonomous day.
    """

    @classmethod
    def setUpClass(cls) -> None:
        from components.phone import demo

        cls.saved_env = dict(os.environ)
        os.environ["WORLD_BODY_INTENT_MUTATION_GATE"] = "canonical"
        cls.model = _StubModel()
        os.environ.update({"NYA02_MODEL_BASE_URL": cls.model.base_url,
                           "NYA02_MODEL_NAME": "stub-model", "NYA02_MODEL_API_KEY": "sk-stub",
                           "NYA02_MODEL_PROVIDER": "custom"})
        cls.base = Path(tempfile.mkdtemp(prefix="live-day-continuous-"))
        cls.root = cls.base / "phone"
        cls.prepared = demo.prepare_demo(cls.root, with_world=True)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.model.stop()
        # The fixture directory is intentionally left on disk (no cleanup, no TemporaryDirectory).
        os.environ.clear()
        os.environ.update(cls.saved_env)

    # -- helpers ---------------------------------------------------------------------
    @property
    def state_root(self) -> Path:
        return phone_life.life_state_root(self.root, phone_life.read_nested_life(self.root))

    def _ledger(self) -> dict:
        return json.loads((self.state_root / "life_request_budget.json").read_text(encoding="utf-8"))

    def _run(self, **kwargs) -> dict:
        kwargs.setdefault("duration_seconds", 180)
        kwargs.setdefault("poll_seconds", 1)
        kwargs.setdefault("max_new_requests", 4)
        return live_day.run_segment(self.root, **kwargs)

    # -- the one continuous flow -----------------------------------------------------
    def test_1_one_segment_ticks_releases_and_reports(self):
        before = len(self.model.requests)
        report = self._run(max_ticks=2)
        self.assertTrue(report["ok"], json.dumps(report.get("reason"), ensure_ascii=False))
        self.assertEqual(report["status"], "READY")
        # the owners this segment started are its own children, and they really started
        self.assertTrue(report["services"]["started"])
        self.assertEqual(report["processes_started"], 2)
        self.assertEqual(report["lease"]["status"], "FREE")
        self.assertTrue(report["services_stopped"]["stopped"])
        self.assertEqual(report["segment_ownership"]["resident_factory"],
                         "genuine_class_with_segment_cap")
        self.assertFalse(report["segment_ownership"]["new_run_window_opened"])
        self.assertEqual(report["bounds"]["ticks_already_recorded"], 0)
        summary = report["summary"]
        # where / hands come from the owner before the children are stopped
        self.assertEqual(summary["where"]["status"], "READY", json.dumps(summary["where"]))
        self.assertTrue(summary["where"]["place_id"])
        self.assertEqual(summary["hands"]["status"], "READY", json.dumps(summary["hands"]))
        self.assertIn(summary["hands"]["pose"], {"standing", "sitting", "lying", None})
        # stopping the segment really stopped it: no claim of a live process afterwards
        self.assertIs(summary["segment"]["running"], False)
        self.assertTrue(summary["segment"]["stopped_at"])
        # the loop ticked, the window opened once, and the second tick was silence
        ticks = summary["changed"]["ticks"]
        self.assertEqual(len(ticks), 2, json.dumps(ticks))
        self.assertEqual(summary["categories"]["silence"], 1)
        # polling did not buy extra requests: at most the one due window could spend
        self.assertLessEqual(len(self.model.requests) - before, 1)
        # the encoding bug that made every world-opportunity event "blocked" is gone
        blob = json.dumps(summary, ensure_ascii=False)
        self.assertNotIn("must be a structured reference", blob)
        # the segment cap was applied as a lowering, and it is reported as such
        cap = summary["changed"]["segment_request_cap"]
        self.assertTrue(cap["applied"])
        self.assertFalse(cap["raised_any_ceiling"])
        self.assertEqual(cap["requested"], 4)
        # a resumable stop point with the owner's own head
        self.assertEqual(summary["stop_point"]["status"], "READY")
        self.assertTrue(summary["stop_point"]["artifact_id"])
        self.assertTrue(summary["stop_point"]["head_version_id"])
        # the next consideration is a real timestamp, and earlier only on a real change
        self.assertEqual(summary["next"]["status"], "READY")
        self.assertTrue(summary["next"]["check_window_at"])
        self.assertEqual(summary["limits"]["private_text_included"], False)

    def test_2_a_second_segment_continues_window_ticks_and_budget(self):
        ledger_before = self._ledger()
        windows_before = dict(ledger_before["windows"])
        calls_before = int(list(windows_before.values())[0]["calls"])
        requests_before = len(self.model.requests)
        report = self._run(max_ticks=1)
        self.assertTrue(report["ok"], json.dumps(report.get("reason"), ensure_ascii=False))
        ledger_after = self._ledger()
        # the same run window is continued -- no new window, no reset
        self.assertEqual(sorted(ledger_after["windows"]), sorted(windows_before))
        self.assertEqual(ledger_after["run_id"], ledger_before["run_id"])
        calls_after = int(list(ledger_after["windows"].values())[0]["calls"])
        self.assertGreaterEqual(calls_after, calls_before)
        # this segment's ticks are its own, even though the resident counts cumulatively
        self.assertGreater(report["bounds"]["ticks_already_recorded"], 0)
        self.assertEqual(report["bounds"]["max_ticks_absolute"],
                         report["bounds"]["ticks_already_recorded"] + 1)
        self.assertEqual(len(report["summary"]["changed"]["ticks"]), 1)
        # the check window was closed, so this segment spent no request at all
        self.assertEqual(len(self.model.requests), requests_before)
        self.assertEqual(report["summary"]["categories"]["blocked"], 0)
        self.assertEqual(report["summary"]["where"]["status"], "READY")
        # the stop point is the same work: a segment boundary is not a re-roll
        self.assertTrue(report["summary"]["stop_point"]["artifact_id"])

    def test_3_an_error_inside_the_resident_keeps_state_and_releases_the_lease(self):
        genuine = phone_life._life_modules()["resident"].AutonomousLifeResident
        requests_before = len(self.model.requests)

        class Exploding(genuine):
            def run(self, **_kwargs):
                raise RuntimeError("injected segment failure")

        report = self._run(max_ticks=1, resident_factory=Exploding)
        self.assertFalse(report["ok"])
        self.assertEqual(report["reason"], "RUN_FAILED")
        self.assertEqual(report["exit_code"], phone_life.EXIT_BLOCKED)
        self.assertEqual(report["lease"]["status"], "FREE")
        self.assertTrue(report["services_stopped"]["stopped"])
        # the record is honest: not running, with a stop time; and the status read agrees
        summary = json.loads((self.state_root / "resident_summary.json").read_text(encoding="utf-8"))
        self.assertIs(summary["running"], False)
        self.assertTrue(summary["stopped_at"])
        status = live_day.status(self.root)
        self.assertTrue(status["ok"])
        self.assertIs(status["running_recorded"], False)
        self.assertEqual(status["lease"]["status"], "FREE")
        # a failed segment did not spend anything
        self.assertEqual(len(self.model.requests), requests_before)

    def test_4_a_cancelled_segment_still_stops_everything(self):
        genuine = phone_life._life_modules()["resident"].AutonomousLifeResident

        class Interrupted(genuine):
            def run(self, **_kwargs):
                raise KeyboardInterrupt

        report = self._run(max_ticks=1, resident_factory=Interrupted)
        self.assertFalse(report["ok"])
        self.assertEqual(report["status"], "CANCELLED")
        self.assertEqual(report["exit_code"], 130)
        self.assertEqual(report["lease"]["status"], "FREE")
        self.assertTrue(report["services_stopped"]["stopped"])
        self.assertIn("summary", report)
        status = live_day.status(self.root)
        self.assertEqual(status["lease"]["status"], "FREE")

    def test_5_the_summary_text_answers_the_five_questions(self):
        text = live_day.summary_text(live_day.status(self.root)["summary"])
        for label in ("现在在哪", "手上什么", "正在做什么", "停在哪", "下一次可考虑"):
            self.assertIn(label, text)
        # the status summary is the recorded history, so the last line says so honestly
        self.assertIn("已记录改变", text)
        self.assertLessEqual(len(text.splitlines()), 6)


if __name__ == "__main__":
    unittest.main()
