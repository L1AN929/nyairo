#!/usr/bin/env python3
"""v0.2 跨模态真读图配色、删除读屏障、零压力试色窗与未懂阅读书签专项单测（32 条）。"""

from __future__ import annotations

import base64
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
import zlib

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from components.life.scripts import v02_crossmodal_perception as cm_engine
from components.phone import v02_crossmodal_view as cm_view


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(chunk_type + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + chunk_type + data + struct.pack(">I", crc)


def _synth_rgb_png(
    width: int,
    height: int,
    pixel_fn,
    *,
    row_filter_fn=None,
) -> bytes:
    """用纯标准库 ``zlib`` + ``struct`` 动态合成真实 8-bit RGB PNG（支持自定义每行滤波器 0..4）。"""
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    stride = width * 3
    prev_raw = bytearray(stride)
    scanlines = bytearray()

    for y in range(height):
        row = bytearray(stride)
        for x in range(width):
            r, g, b = pixel_fn(x, y)
            idx = x * 3
            row[idx] = r & 0xFF
            row[idx + 1] = g & 0xFF
            row[idx + 2] = b & 0xFF

        ftype = int(row_filter_fn(y)) if row_filter_fn is not None else 0
        filtered = bytearray(stride)
        if ftype == 0:  # None
            filtered[:] = row
        elif ftype == 1:  # Sub
            for i in range(stride):
                left = row[i - 3] if i >= 3 else 0
                filtered[i] = (row[i] - left) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                filtered[i] = (row[i] - prev_raw[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = row[i - 3] if i >= 3 else 0
                filtered[i] = (row[i] - ((left + prev_raw[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = row[i - 3] if i >= 3 else 0
                b = prev_raw[i]
                c = prev_raw[i - 3] if i >= 3 else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                filtered[i] = (row[i] - pr) & 0xFF
        else:
            raise ValueError(f"Unsupported filter {ftype}")

        scanlines.append(ftype)
        scanlines.extend(filtered)
        prev_raw = row

    idat = zlib.compress(bytes(scanlines), level=6)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", idat)
        + _png_chunk(b"IEND", b"")
    )


def _synth_rgba_png(width: int, height: int, pixel_rgba_fn) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(width):
            r, g, b, a = pixel_rgba_fn(x, y)
            scanlines.extend((r & 0xFF, g & 0xFF, b & 0xFF, a & 0xFF))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines)))
        + _png_chunk(b"IEND", b"")
    )


def _synth_palette_png(
    width: int,
    height: int,
    palette_rgb: list[tuple[int, int, int]],
    index_fn,
) -> bytes:
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0)
    plte = b"".join(bytes((r, g, b)) for r, g, b in palette_rgb)
    scanlines = bytearray()
    for y in range(height):
        scanlines.append(0)
        for x in range(width):
            scanlines.append(int(index_fn(x, y)) & 0xFF)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", ihdr)
        + _png_chunk(b"PLTE", plte)
        + _png_chunk(b"IDAT", zlib.compress(bytes(scanlines)))
        + _png_chunk(b"IEND", b"")
    )


def _synth_bmp(width: int, height: int, rgb: tuple[int, int, int], *, bpp: int = 24) -> bytes:
    r, g, b = rgb
    bytes_per_px = bpp // 8
    row_stride = ((width * bytes_per_px + 3) // 4) * 4
    pixel_data = bytearray()
    for _y in range(height):
        row = bytearray()
        for _x in range(width):
            if bpp == 24:
                row.extend((b, g, r))
            else:
                row.extend((b, g, r, 255))
        while len(row) < row_stride:
            row.append(0)
        pixel_data.extend(row)
    pixel_offset = 14 + 40
    file_size = pixel_offset + len(pixel_data)
    file_hdr = struct.pack("<2sIHHI", b"BM", file_size, 0, 0, pixel_offset)
    dib_hdr = struct.pack(
        "<IiiHHIIiiII",
        40,
        width,
        height,
        1,
        bpp,
        0,
        len(pixel_data),
        2835,
        2835,
        0,
        0,
    )
    return file_hdr + dib_hdr + bytes(pixel_data)


def _snapshot_tree(root: Path) -> dict[str, tuple[bytes, int]]:
    if not root.exists():
        return {}
    snap: dict[str, tuple[bytes, int]] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            st = p.stat()
            snap[p.relative_to(root).as_posix()] = (p.read_bytes(), st.st_mtime_ns)
        elif p.is_dir():
            snap[p.relative_to(root).as_posix() + "/"] = (b"", 0)
    return snap


class TestV02CrossmodalPerception(unittest.TestCase):
    """覆盖像素真解码、四层跨模态边界、删原图读屏障、零压力试色窗、未懂书签与隐私零泄漏。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.day_root = Path(self._tmp.name) / "day_root"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ------------------------------------------------------------------
    # Group A: 真实本地像素解码与冷暖/明暗/主色量化（Tests 01–12）
    # ------------------------------------------------------------------

    def test_01_pure_sunset_orange_png_palette_warm_and_exact_hex(self) -> None:
        png = _synth_rgb_png(24, 24, lambda _x, _y: (255, 88, 34))
        pal = cm_view.extract_real_image_palette(png)
        self.assertGreaterEqual(len(pal["dominant_hex"]), 3)
        self.assertLessEqual(len(pal["dominant_hex"]), 5)
        self.assertEqual(pal["dominant_hex"][0], "#FF5822")
        self.assertEqual(pal["warmth_band"], "warm")
        self.assertEqual(pal["warmth_label_cn"], "暖调微光")
        self.assertEqual(pal["brightness_band"], "balanced")
        self.assertEqual(pal["brightness_label_cn"], "明暗适中")
        self.assertEqual(pal["width"], 24)
        self.assertEqual(pal["height"], 24)
        self.assertEqual(len(pal["sha256"]), 64)
        self.assertTrue(pal["not_lived_event_claim"])
        self.assertEqual(pal["llm_calls"], 0)

    def test_02_pure_deep_sea_blue_png_palette_cool_and_dim(self) -> None:
        orange_png = _synth_rgb_png(20, 20, lambda _x, _y: (255, 88, 34))
        deep_blue_png = _synth_rgb_png(20, 20, lambda _x, _y: (14, 42, 92))
        pal_orange = cm_view.extract_real_image_palette(orange_png)
        pal_blue = cm_view.extract_real_image_palette(deep_blue_png)

        self.assertEqual(pal_blue["dominant_hex"][0], "#0E2A5C")
        self.assertEqual(pal_blue["warmth_band"], "cool")
        self.assertEqual(pal_blue["warmth_label_cn"], "清冷蓝灰")
        self.assertEqual(pal_blue["brightness_band"], "dim")
        self.assertEqual(pal_blue["brightness_label_cn"], "静谧偏暗")
        self.assertTrue(set(pal_orange["dominant_hex"]).isdisjoint(set(pal_blue["dominant_hex"])))

    def test_03_half_bright_half_dark_bicolor_png_contrast_and_clusters(self) -> None:
        bicolor_png = _synth_rgb_png(
            20,
            20,
            lambda _x, y: (245, 240, 230) if y < 10 else (24, 30, 42),
        )
        pal = cm_view.extract_real_image_palette(bicolor_png)
        self.assertIn("#F5F0E6", pal["dominant_hex"])
        self.assertIn("#181E2A", pal["dominant_hex"])
        self.assertEqual(pal["brightness_band"], "balanced")
        self.assertGreater(pal["contrast_span"], 150.0)
        self.assertIn("反差", pal["mood_summary_cn"])

    def test_04_bright_neutral_daylight_png_band(self) -> None:
        bright_png = _synth_rgb_png(16, 16, lambda _x, _y: (232, 232, 232))
        pal = cm_view.extract_real_image_palette(bright_png)
        self.assertEqual(pal["dominant_hex"][0], "#E8E8E8")
        self.assertEqual(pal["warmth_band"], "neutral")
        self.assertEqual(pal["warmth_label_cn"], "柔和中性")
        self.assertEqual(pal["brightness_band"], "bright")
        self.assertEqual(pal["brightness_label_cn"], "明朗通透")

    def test_05_stdlib_png_all_five_row_filters_exact_decode(self) -> None:
        def px(x: int, y: int) -> tuple[int, int, int]:
            if y < 5:
                return (240, 96, 48)
            if y < 10:
                return (230, 180, 60)
            return (200, 72, 92)

        five_filter_png = _synth_rgb_png(15, 15, px, row_filter_fn=lambda y: y % 5)
        w, h, mime, decoded_px = cm_engine.decode_image_pixels_stdlib(five_filter_png)
        self.assertEqual((w, h, mime), (15, 15, "image/png"))
        expected_px = [px(x, y) for y in range(15) for x in range(15)]
        self.assertEqual(decoded_px, expected_px)

        pal = cm_engine.extract_real_image_palette(five_filter_png, force_stdlib=True)
        self.assertEqual(pal["warmth_band"], "warm")
        self.assertGreaterEqual(len(pal["dominant_hex"]), 3)

    def test_06_stdlib_png_rgba_and_indexed_palette_plte_decode(self) -> None:
        rgba_png = _synth_rgba_png(
            12,
            12,
            lambda x, _y: (0, 0, 0, 0) if x < 6 else (250, 100, 70, 255),
        )
        pal_rgba = cm_engine.extract_real_image_palette(rgba_png, force_stdlib=True)
        self.assertEqual(pal_rgba["dominant_hex"][0], "#FA6446")
        self.assertEqual(pal_rgba["warmth_band"], "warm")

        plte_colors = [(18, 52, 108), (32, 84, 148), (68, 128, 186), (180, 212, 238)]
        plte_png = _synth_palette_png(16, 16, plte_colors, lambda x, _y: x % 4)
        pal_plte = cm_engine.extract_real_image_palette(plte_png, force_stdlib=True)
        self.assertEqual(pal_plte["warmth_band"], "cool")
        self.assertGreaterEqual(len(pal_plte["dominant_hex"]), 4)
        self.assertIn("#12346C", pal_plte["dominant_hex"])

    def test_07_stdlib_bmp_24bit_and_32bit_decode(self) -> None:
        bmp24 = _synth_bmp(11, 9, (220, 85, 45), bpp=24)
        bmp32 = _synth_bmp(10, 10, (25, 65, 130), bpp=32)
        p24 = cm_engine.extract_real_image_palette(bmp24, force_stdlib=True)
        p32 = cm_engine.extract_real_image_palette(bmp32, force_stdlib=True)
        self.assertEqual(p24["dominant_hex"][0], "#DC552D")
        self.assertEqual(p24["warmth_band"], "warm")
        self.assertEqual(p32["dominant_hex"][0], "#194182")
        self.assertEqual(p32["warmth_band"], "cool")

    def test_08_stdlib_ppm_p6_binary_and_p3_ascii_decode(self) -> None:
        p6_bytes = b"P6\n# warm binary ppm\n4 4\n255\n" + bytes([244, 120, 40]) * 16
        p3_bytes = (
            "P3\n# cool ascii ppm\n3 3\n255\n"
            + " ".join(["20 55 125"] * 9)
            + "\n"
        ).encode("ascii")
        pal_p6 = cm_engine.extract_real_image_palette(p6_bytes, force_stdlib=True)
        pal_p3 = cm_engine.extract_real_image_palette(p3_bytes, force_stdlib=True)
        self.assertEqual(pal_p6["dominant_hex"][0], "#F47828")
        self.assertEqual(pal_p6["warmth_band"], "warm")
        self.assertEqual(pal_p3["dominant_hex"][0], "#14377D")
        self.assertEqual(pal_p3["warmth_band"], "cool")

    def test_09_data_uri_base64_prefix_stripping(self) -> None:
        png = _synth_rgb_png(10, 10, lambda _x, _y: (242, 140, 56))
        data_uri = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
        code, resp = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "傍晚云层局部",
                "image_base64": data_uri,
            },
            actor_side="partner",
            now_iso="2026-11-13T12:00:00Z",
        )
        self.assertEqual(code, 200)
        study = resp["palette_studies"][0]
        self.assertEqual(study["dominant_hex"][0], "#F28C38")
        self.assertEqual(study["warmth_band"], "warm")
        self.assertTrue(study["shared"])

    def test_10_corrupted_png_crc_and_truncated_bytes_rejected_400(self) -> None:
        valid_png = bytearray(_synth_rgb_png(12, 12, lambda _x, _y: (200, 100, 50)))
        corrupted_crc_png = bytes(valid_png[:-8] + b"\xFF\x00" + valid_png[-6:])
        truncated_png = bytes(valid_png[:28])

        bad_inputs = [
            b"",
            b"not-an-image-at-all",
            b"<html><body>error</body></html>",
            corrupted_crc_png,
            truncated_png,
            b"BM\x00\x00broken",
            b"P6\n10 10\n255\n\x01\x02",
        ]
        for bad in bad_inputs:
            with self.assertRaises(cm_engine.V02ValidationError) as ctx:
                cm_view.extract_real_image_palette(bad)
            self.assertEqual(ctx.exception.reason_code, "INVALID_IMAGE_BYTES")

        bad_b64 = base64.b64encode(corrupted_crc_png).decode("ascii")
        code, err = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "坏图测试",
                "image_base64": bad_b64,
            },
            actor_side="partner",
        )
        self.assertEqual(code, 400)
        self.assertEqual(err["reason_code"], "INVALID_IMAGE_BYTES")

    def test_11_misleading_filename_never_fakes_color(self) -> None:
        album_dir = self.day_root / "phone" / "album"
        album_dir.mkdir(parents=True, exist_ok=True)
        deep_blue_png = _synth_rgb_png(14, 14, lambda _x, _y: (12, 38, 88))
        # 文件名故意写成暖橙落日，内容却是纯深海蓝像素
        (album_dir / "sunset_warm_orange_fire.png").write_bytes(deep_blue_png)

        code, resp = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "名字具有迷惑性的照片",
                "album_id": "sunset_warm_orange_fire.png",
                "share_with_room": True,
            },
            actor_side="subject",
        )
        self.assertEqual(code, 200)
        st = resp["palette_studies"][0]
        self.assertEqual(st["dominant_hex"][0], "#0C2658")
        self.assertEqual(st["warmth_band"], "cool")
        self.assertEqual(st["brightness_band"], "dim")

    def test_12_works_when_pillow_unavailable_fallback(self) -> None:
        png = _synth_rgb_png(16, 16, lambda _x, _y: (248, 92, 42), row_filter_fn=lambda y: (y + 2) % 5)
        b64 = base64.b64encode(png).decode("ascii")
        with mock.patch.object(cm_engine, "_PILImage", None):
            code, resp = cm_view.mutate_v02_crossmodal_view(
                self.day_root,
                {
                    "action": "analyze_image_palette",
                    "title": "无Pillow纯标准库解码",
                    "image_base64": b64,
                },
                actor_side="partner",
            )
        self.assertEqual(code, 200)
        self.assertEqual(resp["palette_studies"][0]["dominant_hex"][0], "#F85C2A")

    # ------------------------------------------------------------------
    # Group B: 四层跨模态边界、删原图实时读屏障与隐私隔离（Tests 13–19）
    # ------------------------------------------------------------------

    def test_13_four_layer_provenance_and_not_lived_event_claim(self) -> None:
        png = _synth_rgb_png(16, 16, lambda _x, _y: (235, 118, 52))
        b64 = base64.b64encode(png).decode("ascii")
        c1, r1 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "街角旧灯照片",
                "image_base64": b64,
                "share_with_room": True,
            },
            actor_side="subject",
        )
        self.assertEqual(c1, 200)
        study = r1["palette_studies"][0]
        self.assertTrue(study["not_lived_event_claim"])
        self.assertEqual(study["four_layer_boundary"]["perception"]["scope"], "local_color_light_only")

        c2, r2 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "apply_palette_to_work",
                "study_id": study["study_id"],
                "work_id": "artifact:window_booklet",
                "work_title": "十二扇窗·第3页",
                "version_hint": "v2",
                "usage_note_cn": "只取暖橘色做边框底纹",
                "share_with_room": True,
            },
            actor_side="subject",
        )
        self.assertEqual(c2, 200)
        app = r2["work_palette_applications"][0]
        self.assertTrue(app["not_lived_event_claim"])
        self.assertEqual(app["applied_hex"][0], "#EB7634")
        self.assertEqual(r2["palette_studies"][0]["four_layer_boundary"]["lineage"]["applied_work_count"], 1)

    def test_14_deleted_album_file_triggers_immediate_zero_write_read_barrier(self) -> None:
        album_dir = self.day_root / "phone" / "album"
        album_dir.mkdir(parents=True, exist_ok=True)
        img_file = album_dir / "harbor_dusk.png"
        img_file.write_bytes(_synth_rgb_png(16, 16, lambda _x, _y: (240, 95, 45)))

        c1, r1 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "港口暮色参考",
                "album_id": "harbor_dusk.png",
                "share_with_room": True,
            },
            actor_side="subject",
        )
        self.assertEqual(c1, 200)
        self.assertTrue(r1["palette_studies"][0]["source_available"])
        self.assertEqual(r1["palette_studies"][0]["dominant_hex"][0], "#F05F2D")

        # 外部直接删除相册原图
        img_file.unlink()
        before_snap = _snapshot_tree(self.day_root)

        g_code, g_resp = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        after_snap = _snapshot_tree(self.day_root)
        self.assertEqual(before_snap, after_snap, "GET must be 100% zero disk write even when read barrier fires")

        self.assertEqual(g_code, 200)
        st_after = g_resp["palette_studies"][0]
        self.assertFalse(st_after["source_available"])
        self.assertEqual(st_after["dominant_hex"], [])
        self.assertEqual(st_after["palette_withheld_reason"], "SOURCE_MEDIA_UNAVAILABLE")

    def test_15_invalidate_deleted_media_source_mutation_cascades_to_work_applications(self) -> None:
        png = _synth_rgb_png(12, 12, lambda _x, _y: (30, 80, 150))
        b64 = base64.b64encode(png).decode("ascii")
        _, r1 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "analyze_image_palette", "title": "夜雨玻璃", "image_base64": b64, "share_with_room": True},
            actor_side="subject",
        )
        sid = r1["palette_studies"][0]["study_id"]
        _, r2 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "apply_palette_to_work", "study_id": sid, "work_title": "雨声小卡", "share_with_room": True},
            actor_side="subject",
        )
        self.assertTrue(r2["work_palette_applications"][0]["applied_hex"])

        c3, r3 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "invalidate_deleted_media_source", "study_id": sid, "reason_cn": "原图撤回"},
            actor_side="subject",
        )
        self.assertEqual(c3, 200)
        self.assertFalse(r3["palette_studies"][0]["source_available"])
        self.assertEqual(r3["palette_studies"][0]["dominant_hex"], [])
        self.assertFalse(r3["work_palette_applications"][0]["source_available"])
        self.assertEqual(r3["work_palette_applications"][0]["applied_hex"], [])

    def test_16_apply_palette_refuses_deleted_source_with_409(self) -> None:
        album_dir = self.day_root / "phone" / "album"
        album_dir.mkdir(parents=True, exist_ok=True)
        img_file = album_dir / "temp_ref.png"
        img_file.write_bytes(_synth_rgb_png(10, 10, lambda _x, _y: (180, 90, 60)))

        _, r1 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "analyze_image_palette", "title": "临时参考", "album_id": "temp_ref.png"},
            actor_side="subject",
        )
        sid = r1["palette_studies"][0]["study_id"]
        img_file.unlink()

        c2, err = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "apply_palette_to_work", "study_id": sid, "work_title": "新画稿"},
            actor_side="subject",
        )
        self.assertEqual(c2, 409)
        self.assertEqual(err["reason_code"], "SOURCE_MEDIA_UNAVAILABLE")

    def test_17_album_manifest_v1_lookup_and_path_traversal_guard(self) -> None:
        album_dir = self.day_root / "phone" / "album"
        media_dir = self.day_root / "media"
        album_dir.mkdir(parents=True, exist_ok=True)
        media_dir.mkdir(parents=True, exist_ok=True)
        png = _synth_rgb_png(10, 10, lambda _x, _y: (242, 102, 48))
        (media_dir / "blob_01.png").write_bytes(png)
        (album_dir / "artifact_alb_01.json").write_text(
            '{"kind":"album.image.v1","caption":"c","content_type":"image/png",'
            '"media":{"name":"blob_01.png","sha256":"abc"},'
            '"source":{"origin":"user_import","declared":"self_declared"}}\n',
            encoding="utf-8",
        )

        c_ok, r_ok = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "analyze_image_palette", "title": "清单映射图", "album_id": "artifact:alb_01"},
            actor_side="subject",
        )
        self.assertEqual(c_ok, 200)
        self.assertEqual(r_ok["palette_studies"][0]["dominant_hex"][0], "#F26630")

        c_trav, e_trav = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "analyze_image_palette", "title": "越界", "album_id": "../secret.png"},
            actor_side="subject",
        )
        self.assertEqual(c_trav, 400)
        self.assertEqual(e_trav["reason_code"], "INVALID_FIELDS")

        c_miss, e_miss = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "analyze_image_palette", "title": "不存在", "album_id": "missing_999"},
            actor_side="subject",
        )
        self.assertEqual(c_miss, 404)
        self.assertEqual(e_miss["reason_code"], "ALBUM_MEDIA_NOT_FOUND")

    def test_18_partner_view_privacy_zero_leakage(self) -> None:
        album_dir = self.day_root / "phone" / "album"
        album_dir.mkdir(parents=True, exist_ok=True)
        (album_dir / "priv_alb_77.png").write_bytes(_synth_rgb_png(10, 10, lambda _x, _y: (250, 110, 45)))
        (album_dir / "shared_alb_88.png").write_bytes(_synth_rgb_png(10, 10, lambda _x, _y: (20, 50, 110)))

        # 私密配色卡（默认 share_with_room=False）
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "analyze_image_palette", "title": "私密草稿配色_绝不可见", "album_id": "priv_alb_77.png"},
            actor_side="subject",
        )
        # 公开配色卡但绑定相册 album_id（伴侣可看色板但必须剥离 album_id 与路径）
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "公开海色参考",
                "album_id": "shared_alb_88.png",
                "share_with_room": True,
            },
            actor_side="subject",
        )
        # 私密未懂书签
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "mark_reading_uncertain",
                "book_title": "私密日记读本",
                "page": 42,
                "passage_hint": "私密批注不公开",
            },
            actor_side="subject",
        )

        p_code, p_view = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=False)
        self.assertEqual(p_code, 200)
        self.assertEqual(p_view["private_studies_withheld_count"], 1)
        self.assertEqual(len(p_view["palette_studies"]), 1)
        pub_study = p_view["palette_studies"][0]
        self.assertEqual(pub_study["title"], "公开海色参考")
        self.assertNotIn("album_id", pub_study)
        self.assertNotIn("source_rel_path", pub_study)
        self.assertEqual(p_view["reading_uncertainty_marks"], [])
        raw_dump = str(p_view)
        self.assertNotIn("私密草稿配色_绝不可见", raw_dump)
        self.assertNotIn("priv_alb_77", raw_dump)
        self.assertNotIn("shared_alb_88", raw_dump)
        self.assertNotIn("私密批注不公开", raw_dump)

        s_code, s_view = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        self.assertEqual(s_code, 200)
        self.assertEqual(len(s_view["palette_studies"]), 2)
        self.assertEqual(len(s_view["reading_uncertainty_marks"]), 1)

    def test_19_partner_post_response_never_leaks_subject_private_records(self) -> None:
        png = _synth_rgb_png(8, 8, lambda _x, _y: (120, 40, 90))
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "内部未公开紫色练习",
                "image_base64": base64.b64encode(png).decode("ascii"),
                "share_with_room": False,
            },
            actor_side="subject",
        )
        c_part, r_part = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "try_color_swatches", "swatches": ["#AABBCC"]},
            actor_side="partner",
        )
        self.assertEqual(c_part, 200)
        self.assertEqual(r_part["palette_studies"], [])
        self.assertEqual(r_part["private_studies_withheld_count"], 1)
        self.assertNotIn("内部未公开紫色练习", str(r_part))

    # ------------------------------------------------------------------
    # Group C: 零压力「不交作品的试色窗口」（Tests 20–23）
    # ------------------------------------------------------------------

    def test_20_dual_actor_color_trial_pad_coexistence_and_zero_score(self) -> None:
        c1, _ = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "try_color_swatches",
                "swatches": ["#ff6b4a", "#FFD166", "#FAF3DD"],
                "note_cn": "试试暖杏色配封面？",
            },
            actor_side="partner",
        )
        self.assertEqual(c1, 200)

        c2, r2 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "try_color_swatches",
                "swatches": ["#1D3557", "#457B9D"],
                "note_cn": "放两块深蓝在旁边比一下",
            },
            actor_side="subject",
        )
        self.assertEqual(c2, 200)
        pad = r2["color_trial_pad"]
        self.assertTrue(pad["active"])
        self.assertEqual(pad["partner_swatches"], ["#FF6B4A", "#FFD166", "#FAF3DD"])
        self.assertEqual(pad["subject_swatches"], ["#1D3557", "#457B9D"])
        self.assertEqual(len(pad["combined_swatches"]), 5)
        self.assertTrue(pad["zero_score_pressure"])
        self.assertTrue(pad["not_permanent_preference"])
        self.assertFalse(pad["requires_finished_work"])

    def test_21_clear_color_trial_pad_resets_cleanly(self) -> None:
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "try_color_swatches", "swatches": ["#336699"], "note_cn": "随手试一下"},
            actor_side="partner",
        )
        code, resp = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "clear_color_trial_pad"},
            actor_side="partner",
            now_iso="2026-11-13T15:30:00Z",
        )
        self.assertEqual(code, 200)
        pad = resp["color_trial_pad"]
        self.assertFalse(pad["active"])
        self.assertEqual(pad["partner_swatches"], [])
        self.assertEqual(pad["subject_swatches"], [])
        self.assertEqual(pad["combined_swatches"], [])
        self.assertEqual(pad["last_cleared_at"], "2026-11-13T15:30:00Z")
        self.assertTrue(pad["zero_score_pressure"])
        self.assertTrue(pad["not_permanent_preference"])

    def test_22_invalid_hex_color_rejected_400(self) -> None:
        invalid_cases = [
            ["FF00AA"],
            ["#FFF"],
            ["#GGGGGG"],
            ["#1234567"],
            [""],
            [123456],
        ]
        for bad_swatches in invalid_cases:
            code, err = cm_view.mutate_v02_crossmodal_view(
                self.day_root,
                {"action": "try_color_swatches", "swatches": bad_swatches},
                actor_side="partner",
            )
            self.assertEqual(code, 400)
            self.assertEqual(err["reason_code"], "INVALID_HEX_COLOR")

    def test_23_swatches_count_bounds_enforced(self) -> None:
        for bad_list in ([], ["#111111", "#222222", "#333333", "#444444"], "not-a-list"):
            code, err = cm_view.mutate_v02_crossmodal_view(
                self.day_root,
                {"action": "try_color_swatches", "swatches": bad_list},
                actor_side="partner",
            )
            self.assertEqual(code, 400)
            self.assertEqual(err["reason_code"], "INVALID_FIELDS")

    # ------------------------------------------------------------------
    # Group D: 「看过但不确定」阅读书签、收藏新鲜度与权限门（Tests 24–28）
    # ------------------------------------------------------------------

    def test_24_reading_uncertainty_lifecycle_reread_keeps_open_and_no_percent(self) -> None:
        c1, r1 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "mark_reading_uncertain",
                "book_title": "色彩与空间知觉",
                "page": 37,
                "passage_hint": "同时对比在弱光边缘为什么反而减弱这一段没看太懂",
                "share_with_room": True,
            },
            actor_side="subject",
        )
        self.assertEqual(c1, 200)
        mk = r1["reading_uncertainty_marks"][0]
        mark_id = mk["mark_id"]
        self.assertEqual(mk["state"], "seen_not_understood")
        self.assertFalse(mk["page_equals_understood"])
        self.assertIsNone(mk["auto_progress_percent"])

        # 往回重读两页（35页），确认问题更复杂，仍保留不确定
        c2, r2 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "mark_reading_uncertain",
                "mark_id": mark_id,
                "book_title": "色彩与空间知觉",
                "page": 35,
                "passage_hint": "回翻35页发现牵涉视杆细胞暗适应阈值，还得再琢磨",
                "state": "rechecked_still_open",
            },
            actor_side="subject",
        )
        self.assertEqual(c2, 200)
        mk2 = r2["reading_uncertainty_marks"][0]
        self.assertEqual(mk2["state"], "rechecked_still_open")
        self.assertEqual(mk2["visited_pages"], [37, 35])
        self.assertEqual(mk2["revision_count"], 2)
        self.assertIsNone(mk2["auto_progress_percent"])

        # 后来终于想通
        c3, r3 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "mark_reading_uncertain",
                "mark_id": mark_id,
                "book_title": "色彩与空间知觉",
                "page": 38,
                "passage_hint": "对照图例看明白了",
                "state": "clarified",
            },
            actor_side="subject",
        )
        self.assertEqual(c3, 200)
        self.assertEqual(r3["reading_uncertainty_marks"][0]["state"], "clarified")

    def test_25_partner_cannot_overwrite_subject_reading_mark(self) -> None:
        _, r1 = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "mark_reading_uncertain",
                "book_title": "建筑光影手记",
                "page": 19,
                "passage_hint": "北向天窗漫射比算式存疑",
                "share_with_room": True,
            },
            actor_side="subject",
        )
        mid = r1["reading_uncertainty_marks"][0]["mark_id"]
        code, err = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "mark_reading_uncertain",
                "mark_id": mid,
                "book_title": "建筑光影手记",
                "page": 19,
                "passage_hint": "替改状态",
                "state": "clarified",
            },
            actor_side="partner",
        )
        self.assertEqual(code, 403)
        self.assertEqual(err["reason_code"], "FORBIDDEN_ACTOR_SIDE")

    def test_26_collection_staleness_statuses_and_disk_recheck(self) -> None:
        album_dir = self.day_root / "phone" / "album"
        album_dir.mkdir(parents=True, exist_ok=True)
        saved_pic = album_dir / "old_poster.png"
        saved_pic.write_bytes(_synth_rgb_png(8, 8, lambda _x, _y: (90, 130, 110)))

        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "check_collection_staleness",
                "title": "旧展览海报截图",
                "source_kind": "album_media",
                "album_id": "old_poster.png",
                "status": "fresh",
            },
            actor_side="subject",
        )
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "check_collection_staleness",
                "title": "雨天木刻教程",
                "source_kind": "article",
                "status": "resting",
                "note_cn": "先收着不急着看",
            },
            actor_side="subject",
        )

        _, view1 = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        ch1 = view1["collection_health"]
        self.assertFalse(ch1["saved_equals_read"])
        self.assertFalse(ch1["saved_equals_permanent_like"])
        self.assertEqual(ch1["counts_by_status"]["fresh"], 1)
        self.assertEqual(ch1["counts_by_status"]["resting"], 1)

        # 删除关联图片文件后，读视图实时感知来源过时失效
        saved_pic.unlink()
        _, view2 = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        ch2 = view2["collection_health"]
        self.assertEqual(ch2["counts_by_status"]["stale_source"], 1)
        self.assertEqual(ch2["counts_by_status"]["fresh"], 0)

    def test_27_subject_only_actions_forbidden_for_partner_403(self) -> None:
        subject_only_payloads = [
            {"action": "apply_palette_to_work", "study_id": "pal_0001", "work_title": "x"},
            {"action": "check_collection_staleness", "title": "x"},
            {"action": "invalidate_deleted_media_source", "album_id": "alb_1"},
        ]
        for pl in subject_only_payloads:
            code, err = cm_view.mutate_v02_crossmodal_view(self.day_root, pl, actor_side="partner")
            self.assertEqual(code, 403)
            self.assertEqual(err["reason_code"], "FORBIDDEN_ACTOR_SIDE")

        c_bad_actor, e_bad_actor = cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "clear_color_trial_pad"},
            actor_side="admin_spoof",
        )
        self.assertEqual(c_bad_actor, 403)
        self.assertEqual(e_bad_actor["reason_code"], "FORBIDDEN_ACTOR_SIDE")

    def test_28_strict_whitelist_foreign_blank_and_score_fields_rejected_400(self) -> None:
        bad_payloads: list[object] = [
            {},
            [],
            None,
            "raw_string",
            {"unknown_only": 1},
            {"action": "bogus_action"},
            {"action": "clear_color_trial_pad", "foreign_empty": ""},
            {"action": "clear_color_trial_pad", "foreign_null": None},
            {"action": "try_color_swatches", "swatches": ["#112233"], "intimacy_score": 10},
            {"action": "try_color_swatches", "swatches": ["#112233"], "actor_side": "subject"},
            {"action": "mark_reading_uncertain", "book_title": "书", "page": True, "passage_hint": "段"},
            {"action": "mark_reading_uncertain", "book_title": "书", "page": 5, "passage_hint": "段", "progress_percent": 50},
            {"action": "try_color_swatches", "swatches": ["#112233"], " ": "padded"},
        ]
        for bad in bad_payloads:
            code, err = cm_view.mutate_v02_crossmodal_view(self.day_root, bad, actor_side="subject")
            self.assertEqual(code, 400, f"Expected 400 for {bad!r}, got {code}")
            self.assertEqual(err["reason_code"], "INVALID_FIELDS")

    # ------------------------------------------------------------------
    # Group E: GET 100% 零写、幂等重放与上下文桥接（Tests 29–32）
    # ------------------------------------------------------------------

    def test_29_get_and_summarize_100_percent_zero_disk_writes_empty_and_populated(self) -> None:
        # 1) 空目录尚未创建时，读与摘要均不创建任何目录或文件
        self.assertFalse(self.day_root.exists())
        c0, v0 = cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=False)
        s0 = cm_view.summarize_crossmodal_for_context(self.day_root)
        self.assertEqual(c0, 200)
        self.assertFalse(v0["state_exists"])
        self.assertEqual(v0["llm_calls"], 0)
        self.assertEqual(s0["llm_calls"], 0)
        self.assertFalse(self.day_root.exists(), "day_root must remain non-existent after read/summarize")

        # 2) 有落盘状态后，多次 GET 与 summarize 前后字节与 mtime_ns 完全零变化
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "try_color_swatches", "swatches": ["#E07A5F", "#81B29A"]},
            actor_side="partner",
        )
        snap_before = _snapshot_tree(self.day_root)
        cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=False)
        cm_view.read_v02_crossmodal_view(self.day_root, has_subject_capability=True)
        cm_view.summarize_crossmodal_for_context(self.day_root)
        snap_after = _snapshot_tree(self.day_root)
        self.assertEqual(snap_before, snap_after)

    def test_30_operation_key_idempotent_replay_and_conflict_409(self) -> None:
        png = _synth_rgb_png(8, 8, lambda _x, _y: (220, 110, 50))
        payload = {
            "action": "analyze_image_palette",
            "title": "幂等测试图",
            "image_base64": base64.b64encode(png).decode("ascii"),
            "operation_key": "op_idem_001",
        }
        c1, r1 = cm_view.mutate_v02_crossmodal_view(self.day_root, payload, actor_side="partner")
        self.assertEqual(c1, 200)
        self.assertEqual(r1["command_status"], "APPLIED")
        self.assertEqual(r1["revision"], 1)

        c2, r2 = cm_view.mutate_v02_crossmodal_view(self.day_root, payload, actor_side="partner")
        self.assertEqual(c2, 200)
        self.assertEqual(r2["command_status"], "DUPLICATE")
        self.assertEqual(r2["status"], "already_applied")
        self.assertEqual(r2["revision"], 1)
        self.assertEqual(len(r2["palette_studies"]), 1)

        conflict_payload = dict(payload)
        conflict_payload["title"] = "篡改标题重用同一个幂等键"
        c3, e3 = cm_view.mutate_v02_crossmodal_view(self.day_root, conflict_payload, actor_side="partner")
        self.assertEqual(c3, 409)
        self.assertEqual(e3["reason_code"], "OPERATION_KEY_CONFLICT")

    def test_31_summarize_crossmodal_for_context_privacy_tags_and_deleted_filter(self) -> None:
        album_dir = self.day_root / "phone" / "album"
        album_dir.mkdir(parents=True, exist_ok=True)
        gone_pic = album_dir / "gone.png"
        gone_pic.write_bytes(_synth_rgb_png(8, 8, lambda _x, _y: (20, 40, 90)))
        live_pic = _synth_rgb_png(8, 8, lambda _x, _y: (240, 110, 45))

        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "analyze_image_palette", "title": "已删旧海图", "album_id": "gone.png", "share_with_room": True},
            actor_side="subject",
        )
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "analyze_image_palette",
                "title": "私密暖橘速写",
                "image_base64": base64.b64encode(live_pic).decode("ascii"),
                "share_with_room": False,
            },
            actor_side="subject",
        )
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {"action": "try_color_swatches", "swatches": ["#F4A261", "#2A9D8F"], "note_cn": "试色"},
            actor_side="partner",
        )
        cm_view.mutate_v02_crossmodal_view(
            self.day_root,
            {
                "action": "mark_reading_uncertain",
                "book_title": "版式留白笔记",
                "page": 21,
                "passage_hint": "网格基线对齐例外规则未吃透",
                "share_with_room": False,
            },
            actor_side="subject",
        )

        # 删除旧海图原文件，确认聊天摘要不会把已删图的色调带回上下文
        gone_pic.unlink()

        ctx_sub = cm_view.summarize_crossmodal_for_context(self.day_root, include_private=True)
        self.assertTrue(ctx_sub["has_active_trial_pad"])
        self.assertEqual(ctx_sub["trial_swatches"], ["#F4A261", "#2A9D8F"])
        self.assertEqual(len(ctx_sub["recent_palettes"]), 1)
        self.assertEqual(ctx_sub["recent_palettes"][0]["title"], "私密暖橘速写")
        self.assertEqual(ctx_sub["recent_palettes"][0]["privacy_tag"], "[未向伴侣公开·勿顺嘴说漏]")
        self.assertIn("[未向伴侣公开·勿顺嘴说漏]", ctx_sub["context_hint_cn"])
        self.assertNotIn("已删旧海图", ctx_sub["context_hint_cn"])

        ctx_pub = cm_view.summarize_crossmodal_for_context(self.day_root, include_private=False)
        self.assertEqual(ctx_pub["recent_palettes"], [])
        self.assertEqual(ctx_pub["open_reading_uncertainties"], [])

    def test_32_source_files_strictly_lf_newlines(self) -> None:
        targets = (
            REPO_ROOT / "components" / "life" / "scripts" / "v02_crossmodal_perception.py",
            REPO_ROOT / "components" / "phone" / "v02_crossmodal_view.py",
            REPO_ROOT / "tests" / "test_v02_crossmodal_perception.py",
        )
        for path in targets:
            raw = path.read_bytes()
            self.assertNotIn(b"\r", raw, f"CR byte found in {path}; must use pure LF (\\n)")


if __name__ == "__main__":
    unittest.main()
