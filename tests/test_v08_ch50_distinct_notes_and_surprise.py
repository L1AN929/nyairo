#!/usr/bin/env python3
"""v0.8 Ch.50 §五 distinct attention, Ch.50 §三 imagined reply, Ch.53 §二 surprise readiness."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for _p in (str(ROOT), str(ROOT / "components" / "life" / "scripts")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from components.life.scripts import v04_elastic_play_learning as ep_engine  # noqa: E402
from components.phone import v04_elastic_play_view as ep_view  # noqa: E402


def _snap(root: Path) -> dict[str, tuple[int, int, str]]:
    if not root.exists():
        return {}
    out: dict[str, tuple[int, int, str]] = {}
    for p in sorted(root.rglob("*")):
        rel = str(p.relative_to(root))
        st = p.lstat()
        if p.is_file() and not p.is_symlink():
            out[rel] = (
                int(st.st_size),
                int(st.st_mtime_ns),
                hashlib.sha256(p.read_bytes()).hexdigest(),
            )
        else:
            out[rel] = (0, int(st.st_mtime_ns), "dir")
    return out


class Ch50DistinctNotesAndSurpriseTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="v08-ch50-distinct-"))
        self.day_root = self.tmp / "day"

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    # ---- helpers -----------------------------------------------------------------
    def _mutate(self, payload: dict, *, actor_side: str, now_iso: str | None = None):
        return ep_view.mutate_v04_elastic_play_view(
            self.day_root, payload, actor_side=actor_side, now_iso=now_iso
        )

    def _read(self, *, has_subject_capability: bool):
        return ep_view.read_v04_elastic_play_view(
            self.day_root, has_subject_capability=has_subject_capability
        )

    def _share(self, *, actor_side: str, detail: str, page: int = 42, **extra):
        payload = {
            "action": "share_distinct_annotation",
            "material_title": "海堤漫步录",
            "page": page,
            "noticed_detail_cn": detail,
        }
        payload.update(extra)
        return self._mutate(
            payload,
            actor_side=actor_side,
            now_iso="2026-11-16T20:00:00+08:00",
        )

    # ---- 1. Two people, same page, different attention ---------------------------
    def test_01_distinct_attention_pairs_without_scores(self) -> None:
        code, _ = self._share(
            actor_side="subject",
            detail="她注意到潮水退去后留在堤缝里的一层白盐",
            quote_excerpt_cn="潮退了，缝里亮着一点点白",
        )
        self.assertEqual(code, 200)
        code, _ = self._share(
            actor_side="partner",
            detail="他记住的是结尾那句没说完的话",
            quote_excerpt_cn="有些话，说完之前路就走完了",
        )
        self.assertEqual(code, 200)

        code, body = self._read(has_subject_capability=False)
        self.assertEqual(code, 200)
        data = body["data"]
        self.assertEqual(len(data["distinct_annotations"]), 2)
        self.assertEqual(len(data["distinct_annotation_pairs"]), 1)
        pair = data["distinct_annotation_pairs"][0]
        self.assertEqual(pair["page"], 42)
        self.assertEqual(pair["subject_noticed_detail_cn"], "她注意到潮水退去后留在堤缝里的一层白盐")
        self.assertEqual(pair["partner_noticed_detail_cn"], "他记住的是结尾那句没说完的话")
        self.assertIs(pair["same_spot_resonance"], False)
        self.assertIs(pair["comprehension_score_exists"], False)
        self.assertIs(pair["forced_divergence"], False)
        for row in data["distinct_annotations"]:
            self.assertIs(row["comprehension_score_exists"], False)
            self.assertIs(row["forced_divergence"], False)
        self.assertEqual(data["llm_calls"], 0)

    # ---- 2. Happening to land on the same detail is reported honestly ------------
    def test_02_same_spot_resonance_is_reported(self) -> None:
        self._share(actor_side="subject", detail="两个人都停在结尾那句留白上", page=7)
        self._share(actor_side="partner", detail="两个人都停在结尾那句留白上", page=7)

        _code, body = self._read(has_subject_capability=True)
        pairs = body["data"]["distinct_annotation_pairs"]
        self.assertEqual(len(pairs), 1)
        self.assertIs(pairs[0]["same_spot_resonance"], True)
        self.assertIs(pairs[0]["comprehension_score_exists"], False)
        self.assertIs(pairs[0]["forced_divergence"], False)

    # ---- 3. Unshared annotation + imagined reply stay private --------------------
    def test_03_private_annotation_and_imagined_reply_invisible_to_partner(self) -> None:
        secret = "只有我知道的那个页脚小标记"
        code, _ = self._share(
            actor_side="subject",
            detail=secret,
            shared_with_partner=False,
        )
        self.assertEqual(code, 200)
        code, _ = self._mutate(
            {
                "action": "save_two_phrasings",
                "context_note": "想先说第一稿",
                "phrasing_a": "我今天想先把作品写完。",
                "phrasing_b": "今天让我先把手上的东西收个尾，好吗？",
                "chosen": "undecided",
                "imagined_reply_cn": "TA 大概会回一句：好，你慢慢来。",
            },
            actor_side="subject",
            now_iso="2026-11-16T20:10:00+08:00",
        )
        self.assertEqual(code, 200)

        # Partner perspective: nothing of the private annotation or draft leaks.
        _code, partner_body = self._read(has_subject_capability=False)
        partner_data = partner_body["data"]
        self.assertEqual(partner_data["distinct_annotations"], [])
        self.assertEqual(partner_data["distinct_annotation_pairs"], [])
        self.assertEqual(partner_data["private_two_phrasings"], [])
        self.assertIs(partner_data["private_two_phrasings_withheld"], True)
        dump = json.dumps(partner_data, ensure_ascii=False)
        self.assertNotIn(secret, dump)
        self.assertNotIn("hypothetical_only", dump)
        self.assertNotIn("imagined_reply_cn", dump)
        self.assertNotIn("distinct_annotations_withheld", partner_data)

        # Subject perspective: the hypothetical reply is clearly marked as imagined.
        _code, subject_body = self._read(has_subject_capability=True)
        subject_data = subject_body["data"]
        self.assertEqual(len(subject_data["distinct_annotations"]), 1)
        draft = subject_data["private_two_phrasings"][0]
        self.assertIs(draft["hypothetical_only"], True)
        self.assertIs(draft["writes_partner_stance"], False)
        self.assertIs(draft["enters_relationship_memory"], False)
        self.assertEqual(draft["imagined_reply_cn"], "TA 大概会回一句：好，你慢慢来。")
        self.assertEqual(subject_data["experiments"], [])
        self.assertEqual(subject_data["llm_calls"], 0)

    # ---- 4. Surprise readiness is subject-side, read-only and spoiler-free ------
    def test_04_surprise_readiness_is_zero_write_and_not_spoiled(self) -> None:
        missing_day = self.tmp / "no-such-day"
        before = _snap(self.tmp)
        readiness = ep_engine.project_surprise_readiness_zero_write(missing_day)
        self.assertIs(readiness["has_prepared_waiting"], False)
        self.assertIs(readiness["has_offered_unopened"], False)
        self.assertIs(readiness["subject_side_only"], True)
        self.assertIs(readiness["reveals_content"], False)
        self.assertEqual(before, _snap(self.tmp), "readiness read on missing dir must create nothing")

        title = "夹在书里的银杏书签"
        code, _ = self._mutate(
            {
                "action": "prepare_or_offer_surprise",
                "stage": "prepare",
                "surprise_id": "surp-ginkgo",
                "kind": "pressed_leaf_card",
                "title": title,
                "content": "等你不忙的时候再看",
            },
            actor_side="subject",
            now_iso="2026-11-16T20:20:00+08:00",
        )
        self.assertEqual(code, 200)

        before = _snap(self.tmp)
        readiness = ep_engine.project_surprise_readiness_zero_write(self.day_root)
        self.assertEqual(before, _snap(self.tmp), "readiness scan must be zero-write")
        self.assertIs(readiness["has_prepared_waiting"], True)
        self.assertEqual(readiness["prepared_waiting_ids"], ["surp-ginkgo"])
        self.assertIs(readiness["has_offered_unopened"], False)
        self.assertIs(readiness["reveals_content"], False)
        dump = json.dumps(readiness, ensure_ascii=False)
        self.assertNotIn(title, dump)
        self.assertNotIn("等你不忙的时候再看", dump)

        # Partner projection still hides the prepared surprise entirely.
        _code, partner_body = self._read(has_subject_capability=False)
        partner_dump = json.dumps(partner_body["data"], ensure_ascii=False)
        self.assertNotIn(title, partner_dump)
        self.assertNotIn("surp-ginkgo", partner_dump)
        self.assertEqual(partner_body["data"]["prepared_surprises"], [])

        # Once offered it counts as "handed over, not yet opened" for the subject.
        code, _ = self._mutate(
            {
                "action": "prepare_or_offer_surprise",
                "stage": "offer",
                "surprise_id": "surp-ginkgo",
            },
            actor_side="subject",
            now_iso="2026-11-16T20:25:00+08:00",
        )
        self.assertEqual(code, 200)
        readiness = ep_engine.project_surprise_readiness_zero_write(self.day_root)
        self.assertIs(readiness["has_prepared_waiting"], False)
        self.assertIs(readiness["has_offered_unopened"], True)
        self.assertEqual(readiness["offered_unopened_ids"], ["surp-ginkgo"])

    # ---- 5. Whitelist and RBAC stay strict --------------------------------------
    def test_05_unknown_score_identity_fields_still_rejected(self) -> None:
        bad_payloads = [
            {
                "action": "share_distinct_annotation",
                "material_title": "海堤漫步录",
                "page": 1,
                "noticed_detail_cn": "堤缝里的盐",
                "understanding_score": 88,
            },
            {
                "action": "share_distinct_annotation",
                "material_title": "海堤漫步录",
                "page": 1,
                "noticed_detail_cn": "堤缝里的盐",
                "uid": "root",
            },
            {
                "action": "share_distinct_annotation",
                "material_title": "海堤漫步录",
                "page": 1,
                "noticed_detail_cn": "堤缝里的盐",
                "fictional_page_ref": "p-1",
            },
            {
                "action": "save_two_phrasings",
                "phrasing_a": "一稿",
                "phrasing_b": "二稿",
                "intimacy_score": 10,
            },
        ]
        for payload in bad_payloads:
            code, resp = self._mutate(payload, actor_side="subject")
            self.assertEqual(code, 400, msg=f"payload should be rejected: {payload}")
            self.assertEqual(resp["reason_code"], "INVALID_FIELDS")

        # Missing detail, too-short detail and out-of-range page are rejected.
        for payload in (
            {"action": "share_distinct_annotation", "material_title": "海堤漫步录", "page": 1},
            {
                "action": "share_distinct_annotation",
                "material_title": "海堤漫步录",
                "page": 1,
                "noticed_detail_cn": "盐",
            },
            {
                "action": "share_distinct_annotation",
                "material_title": "海堤漫步录",
                "page": 0,
                "noticed_detail_cn": "堤缝里的盐",
            },
        ):
            code, resp = self._mutate(payload, actor_side="subject")
            self.assertEqual(code, 400, msg=f"payload should be rejected: {payload}")

        # Subject-only action remains forbidden to the partner.
        code, resp = self._mutate(
            {"action": "save_two_phrasings", "phrasing_a": "一稿", "phrasing_b": "二稿"},
            actor_side="partner",
        )
        self.assertEqual(code, 403)
        self.assertEqual(resp["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        # A clean partner annotation still applies with zero LLM calls.
        code, body = self._share(actor_side="partner", detail="堤缝里还留着盐的白边")
        self.assertEqual(code, 200)
        self.assertEqual(body["data"]["llm_calls"], 0)


if __name__ == "__main__":
    unittest.main()
