#!/usr/bin/env python3
"""v0.2 跨模态局部真读图配色、删除读屏障、零压力试色窗与未懂阅读书签核心引擎。

对标设计蓝图：
- 第 13 章 §3–4 & §7：跨模态材料四层分离（取得 / 局部理解 / 历史关联 / 对外披露）、
  存图或拿图做配色材料绝不晋升为亲历事件、读到页码不等于读懂、外部删除原图立即阻止派生色板复活。
- 第 15 章 §3 & §7（D01/D03/D07）：有限局部感知、有履历物件源删除传播、生活收藏不等于读完或永久喜欢。
- 第 16 章 §15.2（#11 看过但不确定书签、#14 不交作品的试色窗口）：
  页码与未懂片段分开记录且不伪造日更百分比；双人试色零积分、不自动固化为永久审美偏好。

红线保证：
- 全程本地真实字节与像素统计，零外部付费大模型/视觉 API 调用（``llm_calls == 0``）；
- 内置纯 Python 标准库（``zlib`` + ``struct``）PNG（8-bit RGB/RGBA/Palette/Gray 含全部 5 种行滤波器
  ``None/Sub/Up/Average/Paeth``）、BMP 与 PPM 像素解码器，若装有 ``Pillow`` 则优先协同解码；
- 读视图与聊天背景摘要 100% 零磁盘写入，每次读取实时复核相册原图是否仍在磁盘上。
"""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import io
import json
import math
import os
import re
import struct
import tempfile
import zlib
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

try:
    from PIL import Image as _PILImage  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional runtime dependency
    _PILImage = None

VIEW_SCHEMA = "nyairo.v02.crossmodal.v1"
STATE_REL_PATH = ("phone", "v02_crossmodal.json")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_IMAGE_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 16_777_216
MAX_SAMPLE_PIXELS = 16_384

HEX_COLOR_RE = re.compile(r"^#[0-9A-Fa-f]{6}$")
DATA_URI_IMAGE_RE = re.compile(r"^data:image/[a-zA-Z0-9.+-]+;base64,", re.IGNORECASE)
SAFE_ALBUM_ID_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")

WARMTH_LABELS_CN: dict[str, str] = {
    "warm": "暖调微光",
    "neutral": "柔和中性",
    "cool": "清冷蓝灰",
}

BRIGHTNESS_LABELS_CN: dict[str, str] = {
    "bright": "明朗通透",
    "balanced": "明暗适中",
    "dim": "静谧偏暗",
}

READING_UNCERTAINTY_STATES: frozenset[str] = frozenset({
    "seen_not_understood",
    "rechecked_still_open",
    "clarified",
})

READING_STATE_LABELS_CN: dict[str, str] = {
    "seen_not_understood": "看过但还没吃透",
    "rechecked_still_open": "重读后问题仍敞着",
    "clarified": "后来想通了",
}

COLLECTION_STATUSES: frozenset[str] = frozenset({
    "fresh",
    "stale_source",
    "revisited",
    "resting",
    "archived",
})

COLLECTION_STATUS_LABELS_CN: dict[str, str] = {
    "fresh": "来源可用·暂存备看",
    "stale_source": "原来源已失效",
    "revisited": "已回访重看",
    "resting": "安静搁置中",
    "archived": "已温和收起",
}

COLLECTION_SOURCE_KINDS: frozenset[str] = frozenset({
    "link",
    "article",
    "image",
    "song",
    "album_media",
    "note",
})

BOX_CURATION_STATES: frozenset[str] = frozenset({
    "kept",
    "reused_fragment",
    "shelved",
    "dropped",
    "selected_to_show",
})

BOX_CURATION_STATE_LABELS_CN: dict[str, str] = {
    "kept": "暂时留存",
    "reused_fragment": "拆出局部复用",
    "shelved": "先搁置一边",
    "dropped": "收起弃用",
    "selected_to_show": "选出来准备给你看",
}

BOX_REUSE_MODES: frozenset[str] = frozenset({
    "none",
    "quote",
    "adapt",
    "structure_ref",
    "direct_reuse",
})

AUDIO_NOTE_MAX_ENTRIES = 32
AUDIO_FORMATS: frozenset[str] = frozenset({
    "wav",
    "ogg",
    "mp3",
    "m4a",
    "webm",
    "voice_memo",
})
AUDIO_SOURCE_KINDS: frozenset[str] = frozenset({
    "mic_capture",
    "local_file",
    "ambient_memo",
})
AUDIO_TRANSCRIPT_STATUS_USER_SUPPLIED = "USER_SUPPLIED"
AUDIO_TRANSCRIPT_STATUS_UNAVAILABLE = "UNAVAILABLE_LOCAL_ASR"
AUDIO_TRANSCRIPT_STATUSES: frozenset[str] = frozenset({
    AUDIO_TRANSCRIPT_STATUS_USER_SUPPLIED,
    AUDIO_TRANSCRIPT_STATUS_UNAVAILABLE,
})
AUDIO_SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")

RECALL_PROJECTION_REL_PATHS: tuple[tuple[str, ...], ...] = (
    ("phone", "v02_recall_projection.json"),
    ("memory", "v02_recall_projection_state.json"),
)

FORBIDDEN_FOREIGN_AND_SCORE_KEYS: frozenset[str] = frozenset({
    "actor_side",
    "has_subject_capability",
    "capability_token",
    "subject_id",
    "grant_id",
    "intimacy_score",
    "affection_score",
    "love_points",
    "progress_percent",
    "reading_percent",
    "daily_streak",
    "debt_score",
    "lived_event_claim",
    "permanent_preference",
    "__proto__",
    "constructor",
    "prototype",
})


class V02CrossmodalError(Exception):
    """v0.2 跨模态模块结构化业务异常。"""

    def __init__(self, status_code: int, reason_code: str, message: str = "") -> None:
        self.status_code = int(status_code)
        self.reason_code = str(reason_code)
        self.message = str(message or reason_code)
        super().__init__(f"{self.reason_code}: {self.message}")


class V02ValidationError(V02CrossmodalError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(400, reason_code, message)


class V02PermissionError(V02CrossmodalError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(403, reason_code, message)


class V02NotFoundError(V02CrossmodalError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(404, reason_code, message)


class V02ConflictError(V02CrossmodalError):
    def __init__(self, reason_code: str, message: str = "") -> None:
        super().__init__(409, reason_code, message)


def _now_iso_str(now_iso: Optional[str] = None) -> str:
    if isinstance(now_iso, str) and now_iso.strip():
        return now_iso.strip()
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _is_strict_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def validate_hex_color(value: Any) -> str:
    """校验并规范化 ``#RRGGBB`` 颜色字符串，非法值抛出 ``400 INVALID_HEX_COLOR``。"""
    if not isinstance(value, str):
        raise V02ValidationError("INVALID_HEX_COLOR", f"Hex color must be a string, got {type(value).__name__}")
    trimmed = value.strip()
    if trimmed != value or not HEX_COLOR_RE.match(trimmed):
        raise V02ValidationError("INVALID_HEX_COLOR", f"Invalid #RRGGBB color: {value!r}")
    return trimmed.upper()


def _require_clean_str(value: Any, field_name: str, *, max_len: int = 200) -> str:
    if not isinstance(value, str):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if not trimmed:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} must not be blank")
    if len(trimmed) > max_len:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}")
    return trimmed


def _optional_clean_str(value: Any, field_name: str, *, max_len: int = 240) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} must be a string")
    if "\x00" in value:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} contains NUL byte")
    trimmed = value.strip()
    if len(trimmed) > max_len:
        raise V02ValidationError("INVALID_FIELDS", f"Field {field_name!r} exceeds max length {max_len}")
    return trimmed


def strip_and_decode_image_base64(image_base64: Any) -> bytes:
    """剥离可选 ``data:image/...;base64,`` 前缀并严格解码真实图片字节。"""
    if not isinstance(image_base64, str) or not image_base64.strip():
        raise V02ValidationError("INVALID_IMAGE_BYTES", "image_base64 must be a non-empty string")
    raw_text = image_base64.strip()
    if raw_text.lower().startswith("data:"):
        match = DATA_URI_IMAGE_RE.match(raw_text)
        if not match:
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Unsupported or malformed data URI prefix")
        raw_text = raw_text[match.end():]
    compact = re.sub(r"\s+", "", raw_text)
    if not compact:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Empty base64 image payload")
    if len(compact) > (MAX_IMAGE_BYTES * 4 // 3) + 128:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Encoded image exceeds maximum byte limit")
    try:
        decoded = base64.b64decode(compact.encode("ascii"), validate=True)
    except (UnicodeError, binascii.Error, ValueError) as exc:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid base64 image stream") from exc
    if not decoded or len(decoded) > MAX_IMAGE_BYTES:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Decoded image is empty or exceeds limit")
    return decoded


# ---------------------------------------------------------------------------
# 纯 Python 标准库像素解码器（PNG 全 5 类行滤波器 + RGB/RGBA/Palette/Gray，BMP，PPM）
# ---------------------------------------------------------------------------


def _paeth_predictor(a: int, b: int, c: int) -> int:
    p = a + b - c
    pa = abs(p - a)
    pb = abs(p - b)
    pc = abs(p - c)
    if pa <= pb and pa <= pc:
        return a
    if pb <= pc:
        return b
    return c


def _unfilter_png_scanlines(
    decompressed: bytes,
    *,
    width: int,
    height: int,
    bpp: int,
    stride: int,
) -> list[bytearray]:
    expected_len = height * (1 + stride)
    if len(decompressed) != expected_len:
        raise V02ValidationError(
            "INVALID_IMAGE_BYTES",
            f"PNG scanline byte count mismatch: got {len(decompressed)}, expected {expected_len}",
        )
    rows: list[bytearray] = []
    prev = bytearray(stride)
    pos = 0
    for _ in range(height):
        ftype = decompressed[pos]
        pos += 1
        raw = decompressed[pos : pos + stride]
        pos += stride
        recon = bytearray(stride)
        if ftype == 0:  # None
            recon[:] = raw
        elif ftype == 1:  # Sub
            for i in range(stride):
                left = recon[i - bpp] if i >= bpp else 0
                recon[i] = (raw[i] + left) & 0xFF
        elif ftype == 2:  # Up
            for i in range(stride):
                recon[i] = (raw[i] + prev[i]) & 0xFF
        elif ftype == 3:  # Average
            for i in range(stride):
                left = recon[i - bpp] if i >= bpp else 0
                recon[i] = (raw[i] + ((left + prev[i]) >> 1)) & 0xFF
        elif ftype == 4:  # Paeth
            for i in range(stride):
                a = recon[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                recon[i] = (raw[i] + _paeth_predictor(a, b, c)) & 0xFF
        else:
            raise V02ValidationError("INVALID_IMAGE_BYTES", f"Unsupported PNG row filter: {ftype}")
        rows.append(recon)
        prev = recon
    return rows


def _parse_and_validate_png_chunks(raw_bytes: bytes) -> dict[str, Any]:
    if len(raw_bytes) < 33 or not raw_bytes.startswith(PNG_SIGNATURE):
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Missing valid PNG signature")
    pos = 8
    ihdr: Optional[tuple[int, int, int, int, int, int, int]] = None
    palette: Optional[list[tuple[int, int, int]]] = None
    trns: Optional[bytes] = None
    idat_parts: list[bytes] = []
    seen_iend = False
    chunk_index = 0

    while pos < len(raw_bytes):
        if pos + 8 > len(raw_bytes):
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Truncated PNG chunk header")
        length, chunk_type = struct.unpack(">I4s", raw_bytes[pos : pos + 8])
        pos += 8
        if pos + length + 4 > len(raw_bytes):
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Truncated PNG chunk payload")
        chunk_data = raw_bytes[pos : pos + length]
        pos += length
        (expected_crc,) = struct.unpack(">I", raw_bytes[pos : pos + 4])
        pos += 4

        actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
        if actual_crc != expected_crc:
            raise V02ValidationError("INVALID_IMAGE_BYTES", f"PNG CRC mismatch in {chunk_type!r}")

        if chunk_index == 0 and chunk_type != b"IHDR":
            raise V02ValidationError("INVALID_IMAGE_BYTES", "First PNG chunk must be IHDR")
        chunk_index += 1

        if chunk_type == b"IHDR":
            if ihdr is not None or length != 13:
                raise V02ValidationError("INVALID_IMAGE_BYTES", "Malformed PNG IHDR chunk")
            width, height, bit_depth, color_type, comp, filt, interlace = struct.unpack(">IIBBBBB", chunk_data)
            if width <= 0 or height <= 0 or (width * height) > MAX_IMAGE_PIXELS:
                raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid PNG dimensions")
            if comp != 0 or filt != 0 or interlace not in (0, 1):
                raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid PNG compression/filter/interlace")
            ihdr = (width, height, bit_depth, color_type, comp, filt, interlace)
        elif chunk_type == b"PLTE":
            if length == 0 or length % 3 != 0 or (length // 3) > 256:
                raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid PNG PLTE chunk")
            palette = [
                (chunk_data[i], chunk_data[i + 1], chunk_data[i + 2])
                for i in range(0, length, 3)
            ]
        elif chunk_type == b"tRNS":
            trns = chunk_data
        elif chunk_type == b"IDAT":
            idat_parts.append(chunk_data)
        elif chunk_type == b"IEND":
            if length != 0:
                raise V02ValidationError("INVALID_IMAGE_BYTES", "Non-empty PNG IEND chunk")
            seen_iend = True
            break

    if ihdr is None or not idat_parts or not seen_iend:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Incomplete PNG stream (missing IHDR/IDAT/IEND)")
    return {
        "ihdr": ihdr,
        "palette": palette,
        "trns": trns,
        "idat": b"".join(idat_parts),
    }


def _decode_png_stdlib(raw_bytes: bytes) -> tuple[int, int, str, list[tuple[int, int, int]]]:
    parsed = _parse_and_validate_png_chunks(raw_bytes)
    width, height, bit_depth, color_type, _comp, _filt, interlace = parsed["ihdr"]
    palette: Optional[list[tuple[int, int, int]]] = parsed["palette"]
    trns: Optional[bytes] = parsed["trns"]

    if interlace != 0:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Interlaced PNG requires non-interlaced scanlines in stdlib path")

    if color_type == 2 and bit_depth == 8:  # RGB
        bpp = 3
        stride = width * 3
    elif color_type == 6 and bit_depth == 8:  # RGBA
        bpp = 4
        stride = width * 4
    elif color_type == 3 and bit_depth in (1, 2, 4, 8):  # Palette
        if not palette:
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Palette PNG missing PLTE chunk")
        bpp = 1
        stride = (width * bit_depth + 7) // 8
    elif color_type == 0 and bit_depth == 8:  # Grayscale
        bpp = 1
        stride = width
    elif color_type == 4 and bit_depth == 8:  # Grayscale + Alpha
        bpp = 2
        stride = width * 2
    else:
        raise V02ValidationError(
            "INVALID_IMAGE_BYTES",
            f"Unsupported PNG color_type={color_type} bit_depth={bit_depth}",
        )

    expected_len = height * (1 + stride)
    try:
        decomp = zlib.decompressobj()
        decompressed = decomp.decompress(parsed["idat"], expected_len + 64)
        decompressed += decomp.flush()
        if not decomp.eof or decomp.unused_data:
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Corrupted or trailing PNG zlib stream")
    except zlib.error as exc:
        raise V02ValidationError("INVALID_IMAGE_BYTES", f"PNG zlib decompression failed: {exc}") from exc

    rows = _unfilter_png_scanlines(
        decompressed,
        width=width,
        height=height,
        bpp=bpp,
        stride=stride,
    )

    total_px = width * height
    step = max(1, int(math.sqrt(total_px / MAX_SAMPLE_PIXELS))) if total_px > MAX_SAMPLE_PIXELS else 1

    opaque_pixels: list[tuple[int, int, int]] = []
    fallback_pixels: list[tuple[int, int, int]] = []

    for y in range(0, height, step):
        row = rows[y]
        if color_type == 2:
            for x in range(0, width, step):
                idx = x * 3
                opaque_pixels.append((row[idx], row[idx + 1], row[idx + 2]))
        elif color_type == 6:
            for x in range(0, width, step):
                idx = x * 4
                r, g, b, a = row[idx], row[idx + 1], row[idx + 2], row[idx + 3]
                fallback_pixels.append((r, g, b))
                if a > 0:
                    opaque_pixels.append((r, g, b))
        elif color_type == 3:
            assert palette is not None
            mask = (1 << bit_depth) - 1
            pixels_per_byte = 8 // bit_depth
            for x in range(0, width, step):
                if bit_depth == 8:
                    p_idx = row[x]
                else:
                    byte_val = row[x // pixels_per_byte]
                    shift = 8 - bit_depth * ((x % pixels_per_byte) + 1)
                    p_idx = (byte_val >> shift) & mask
                if p_idx >= len(palette):
                    raise V02ValidationError("INVALID_IMAGE_BYTES", f"Palette index {p_idx} out of bounds")
                rgb = palette[p_idx]
                alpha = trns[p_idx] if (trns is not None and p_idx < len(trns)) else 255
                fallback_pixels.append(rgb)
                if alpha > 0:
                    opaque_pixels.append(rgb)
        elif color_type == 0:
            for x in range(0, width, step):
                v = row[x]
                opaque_pixels.append((v, v, v))
        elif color_type == 4:
            for x in range(0, width, step):
                idx = x * 2
                v, a = row[idx], row[idx + 1]
                fallback_pixels.append((v, v, v))
                if a > 0:
                    opaque_pixels.append((v, v, v))

    chosen = opaque_pixels if opaque_pixels else fallback_pixels
    if not chosen:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "PNG contains zero pixels")
    return width, height, "image/png", chosen


def _decode_bmp_stdlib(raw_bytes: bytes) -> tuple[int, int, str, list[tuple[int, int, int]]]:
    if len(raw_bytes) < 54 or raw_bytes[:2] != b"BM":
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid BMP header")
    _magic, _file_size, _r1, _r2, pixel_offset = struct.unpack("<2sIHHI", raw_bytes[:14])
    dib_size = struct.unpack("<I", raw_bytes[14:18])[0]
    if dib_size < 40 or len(raw_bytes) < 14 + dib_size:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Unsupported BMP DIB header")
    width, height_signed, planes, bpp, compression = struct.unpack("<iiHHI", raw_bytes[18:34])
    height = abs(height_signed)
    if width <= 0 or height <= 0 or (width * height) > MAX_IMAGE_PIXELS:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid BMP dimensions")
    if planes != 1 or compression != 0 or bpp not in (24, 32):
        raise V02ValidationError("INVALID_IMAGE_BYTES", f"Unsupported BMP bpp={bpp} compression={compression}")
    bytes_per_px = bpp // 8
    row_stride = ((width * bytes_per_px + 3) // 4) * 4
    if pixel_offset < 14 + dib_size or pixel_offset + row_stride * height > len(raw_bytes):
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Truncated BMP pixel array")

    total_px = width * height
    step = max(1, int(math.sqrt(total_px / MAX_SAMPLE_PIXELS))) if total_px > MAX_SAMPLE_PIXELS else 1
    top_down = height_signed < 0
    pixels: list[tuple[int, int, int]] = []
    fallback: list[tuple[int, int, int]] = []

    for y in range(0, height, step):
        disk_row = y if top_down else (height - 1 - y)
        row_base = pixel_offset + disk_row * row_stride
        for x in range(0, width, step):
            px_pos = row_base + x * bytes_per_px
            b_val = raw_bytes[px_pos]
            g_val = raw_bytes[px_pos + 1]
            r_val = raw_bytes[px_pos + 2]
            if bpp == 32:
                a_val = raw_bytes[px_pos + 3]
                fallback.append((r_val, g_val, b_val))
                if a_val > 0:
                    pixels.append((r_val, g_val, b_val))
            else:
                pixels.append((r_val, g_val, b_val))

    chosen = pixels if pixels else fallback
    if not chosen:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "BMP contains zero pixels")
    return width, height, "image/bmp", chosen


def _read_ppm_tokens(raw_bytes: bytes, count: int) -> tuple[list[bytes], int]:
    tokens: list[bytes] = []
    n = len(raw_bytes)
    i = 0
    while i < n and len(tokens) < count:
        ch = raw_bytes[i]
        if ch in b" \t\r\n\f\v":
            i += 1
            continue
        if ch == ord("#"):
            while i < n and raw_bytes[i] not in b"\r\n":
                i += 1
            continue
        start = i
        while i < n and raw_bytes[i] not in b" \t\r\n\f\v#":
            i += 1
        tokens.append(raw_bytes[start:i])
    return tokens, i


def _decode_ppm_stdlib(raw_bytes: bytes) -> tuple[int, int, str, list[tuple[int, int, int]]]:
    if len(raw_bytes) < 7 or raw_bytes[:2] not in (b"P6", b"P3"):
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid PPM magic")
    header_tokens, pos = _read_ppm_tokens(raw_bytes, 4)
    if len(header_tokens) < 4:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Incomplete PPM header")
    magic = header_tokens[0]
    try:
        width = int(header_tokens[1].decode("ascii"))
        height = int(header_tokens[2].decode("ascii"))
        maxval = int(header_tokens[3].decode("ascii"))
    except (UnicodeError, ValueError) as exc:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Malformed PPM header numbers") from exc

    if width <= 0 or height <= 0 or (width * height) > MAX_IMAGE_PIXELS or not (1 <= maxval <= 65535):
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid PPM dimensions or maxval")

    total_px = width * height
    step = max(1, int(math.sqrt(total_px / MAX_SAMPLE_PIXELS))) if total_px > MAX_SAMPLE_PIXELS else 1
    pixels: list[tuple[int, int, int]] = []

    if magic == b"P6":
        if pos >= len(raw_bytes) or raw_bytes[pos] not in b" \t\r\n\f\v":
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Missing whitespace delimiter after P6 maxval")
        pos += 1
        sample_bytes = 1 if maxval <= 255 else 2
        expected_body = total_px * 3 * sample_bytes
        if len(raw_bytes) - pos < expected_body:
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Truncated P6 PPM pixel payload")
        body = raw_bytes[pos : pos + expected_body]
        for y in range(0, height, step):
            for x in range(0, width, step):
                idx = (y * width + x) * 3 * sample_bytes
                if sample_bytes == 1:
                    r_raw, g_raw, b_raw = body[idx], body[idx + 1], body[idx + 2]
                else:
                    r_raw, g_raw, b_raw = struct.unpack(">HHH", body[idx : idx + 6])
                if r_raw > maxval or g_raw > maxval or b_raw > maxval:
                    raise V02ValidationError("INVALID_IMAGE_BYTES", "PPM channel value exceeds maxval")
                if maxval == 255:
                    pixels.append((r_raw, g_raw, b_raw))
                else:
                    pixels.append((
                        round(r_raw * 255 / maxval),
                        round(g_raw * 255 / maxval),
                        round(b_raw * 255 / maxval),
                    ))
    else:  # P3 ASCII PPM
        sample_tokens, _ = _read_ppm_tokens(raw_bytes[pos:], total_px * 3)
        if len(sample_tokens) < total_px * 3:
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Truncated P3 PPM pixel values")
        try:
            vals = [int(tok.decode("ascii")) for tok in sample_tokens[: total_px * 3]]
        except (UnicodeError, ValueError) as exc:
            raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid integer in P3 PPM") from exc
        for y in range(0, height, step):
            for x in range(0, width, step):
                base = (y * width + x) * 3
                r_raw, g_raw, b_raw = vals[base], vals[base + 1], vals[base + 2]
                if not (0 <= r_raw <= maxval and 0 <= g_raw <= maxval and 0 <= b_raw <= maxval):
                    raise V02ValidationError("INVALID_IMAGE_BYTES", "P3 PPM sample out of range")
                pixels.append((
                    round(r_raw * 255 / maxval),
                    round(g_raw * 255 / maxval),
                    round(b_raw * 255 / maxval),
                ))

    if not pixels:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "PPM contains zero pixels")
    return width, height, "image/x-portable-pixmap", pixels


def decode_image_pixels_stdlib(raw_bytes: bytes) -> tuple[int, int, str, list[tuple[int, int, int]]]:
    """纯 Python 标准库（``zlib`` + ``struct``）真实像素解码入口（PNG / BMP / PPM）。"""
    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) == 0:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Image bytes must be non-empty")
    data = bytes(raw_bytes)
    if len(data) > MAX_IMAGE_BYTES:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Image exceeds maximum allowed byte size")
    if data.startswith(PNG_SIGNATURE):
        return _decode_png_stdlib(data)
    if data[:2] == b"BM":
        return _decode_bmp_stdlib(data)
    if data[:2] in (b"P6", b"P3") and len(data) > 3 and data[2] in b" \t\r\n#":
        return _decode_ppm_stdlib(data)
    raise V02ValidationError("INVALID_IMAGE_BYTES", "Unrecognized or corrupted image byte format")


def decode_image_pixels(
    raw_bytes: bytes,
    *,
    force_stdlib: bool = False,
) -> tuple[int, int, str, list[tuple[int, int, int]]]:
    """解码真实像素 ``(width, height, content_type, [(R,G,B), ...])``。

    若安装了 ``PIL`` (``Pillow``) 且 ``force_stdlib=False``，优先用 ``Pillow`` 解码（同时对 PNG
    预先执行严格 CRC/Chunk 完整性校验，杜绝截断或损坏流蒙混过关）；否则回退到内置标准库解码器。
    """
    if not isinstance(raw_bytes, (bytes, bytearray)) or len(raw_bytes) == 0:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Image bytes must be non-empty")
    data = bytes(raw_bytes)
    if len(data) > MAX_IMAGE_BYTES:
        raise V02ValidationError("INVALID_IMAGE_BYTES", "Image exceeds maximum allowed byte size")

    if data.startswith(PNG_SIGNATURE):
        _parse_and_validate_png_chunks(data)

    if not force_stdlib and _PILImage is not None:
        # ASCII P3 PPM 在部分精简 Pillow 构建中可能未启用，失败时安全回退 stdlib PPM
        try:
            with _PILImage.open(io.BytesIO(data)) as verify_im:
                verify_im.verify()
            with _PILImage.open(io.BytesIO(data)) as im:
                im.load()
                width, height = int(im.width), int(im.height)
                if width <= 0 or height <= 0 or (width * height) > MAX_IMAGE_PIXELS:
                    raise V02ValidationError("INVALID_IMAGE_BYTES", "Invalid image dimensions")
                fmt = (im.format or "").upper()
                mime_map = {
                    "PNG": "image/png",
                    "JPEG": "image/jpeg",
                    "BMP": "image/bmp",
                    "PPM": "image/x-portable-pixmap",
                    "WEBP": "image/webp",
                    "GIF": "image/gif",
                }
                content_type = mime_map.get(fmt, "image/octet-stream")
                rgba = im.convert("RGBA")
                total_px = width * height
                step = max(1, int(math.sqrt(total_px / MAX_SAMPLE_PIXELS))) if total_px > MAX_SAMPLE_PIXELS else 1
                opaque: list[tuple[int, int, int]] = []
                fallback: list[tuple[int, int, int]] = []
                for y in range(0, height, step):
                    for x in range(0, width, step):
                        r, g, b, a = rgba.getpixel((x, y))
                        fallback.append((int(r), int(g), int(b)))
                        if a > 0:
                            opaque.append((int(r), int(g), int(b)))
                chosen = opaque if opaque else fallback
                if not chosen:
                    raise V02ValidationError("INVALID_IMAGE_BYTES", "Decoded image has zero pixels")
                return width, height, content_type, chosen
        except V02CrossmodalError:
            raise
        except Exception as exc:
            if data[:2] in (b"P6", b"P3") or data[:2] == b"BM" or data.startswith(PNG_SIGNATURE):
                return decode_image_pixels_stdlib(data)
            raise V02ValidationError("INVALID_IMAGE_BYTES", f"Corrupted or unsupported image bytes: {exc}") from exc

    return decode_image_pixels_stdlib(data)


# ---------------------------------------------------------------------------
# 真实像素统计：主色调桶聚类（3~5 个 #RRGGBB）、冷暖带、明暗带与中文氛围摘要
# ---------------------------------------------------------------------------


def _rgb_to_hex(r: int, g: int, b: int) -> str:
    rc = max(0, min(255, int(r)))
    gc = max(0, min(255, int(g)))
    bc = max(0, min(255, int(b)))
    return f"#{rc:02X}{gc:02X}{bc:02X}"


def _derive_tonal_companions(clusters_rgb: list[tuple[int, int, int]]) -> list[str]:
    """当画面纯度极高（仅 1~2 个离散色桶，如纯色或双色色块图）时，基于真实测得主色生成明暗阶伴色，
    确保返回 3~5 个 ``#RRGGBB`` 且第 1 主色 100% 等于原始像素色值、绝不掺入虚假色相。"""
    hex_out: list[str] = []
    for r, g, b in clusters_rgb:
        hx = _rgb_to_hex(r, g, b)
        if hx not in hex_out:
            hex_out.append(hx)
    if len(hex_out) >= 3:
        return hex_out[:5]

    if len(clusters_rgb) == 2:
        (r0, g0, b0), (r1, g1, b1) = clusters_rgb[0], clusters_rgb[1]
        mid = _rgb_to_hex((r0 + r1) // 2, (g0 + g1) // 2, (b0 + b1) // 2)
        if mid not in hex_out:
            hex_out.append(mid)

    r0, g0, b0 = clusters_rgb[0]
    candidate_Adjustments = (
        (round(r0 * 0.84), round(g0 * 0.84), round(b0 * 0.84)),
        (min(255, round(r0 * 1.12) + 12), min(255, round(g0 * 1.12) + 12), min(255, round(b0 * 1.12) + 12)),
        (max(0, r0 - 18), max(0, g0 - 18), max(0, b0 - 18)),
        (min(255, r0 + 22), min(255, g0 + 22), min(255, b0 + 22)),
    )
    for rc, gc, bc in candidate_Adjustments:
        if len(hex_out) >= 3:
            break
        hx = _rgb_to_hex(rc, gc, bc)
        if hx not in hex_out:
            hex_out.append(hx)
    return hex_out[:5]


def _quantize_dominant_palette(pixels: list[tuple[int, int, int]]) -> tuple[list[str], int]:
    buckets: dict[tuple[int, int, int], list[int]] = {}
    for r, g, b in pixels:
        key = (r >> 4, g >> 4, b >> 4)
        acc = buckets.get(key)
        if acc is None:
            buckets[key] = [1, r, g, b]
        else:
            acc[0] += 1
            acc[1] += r
            acc[2] += g
            acc[3] += b

    sorted_bins = sorted(
        buckets.values(),
        key=lambda item: (item[0], item[1], item[2], item[3]),
        reverse=True,
    )

    # 合并距离过近的相邻量化桶（欧氏距离 < 22），保留真实像素加权均值
    merged: list[list[float]] = []  # [count, sum_r, sum_g, sum_b]
    for count, sr, sg, sb in sorted_bins:
        mr = sr / count
        mg = sg / count
        mb = sb / count
        placed = False
        for cluster in merged:
            cr = cluster[1] / cluster[0]
            cg = cluster[2] / cluster[0]
            cb = cluster[3] / cluster[0]
            dist = math.sqrt((mr - cr) ** 2 + (mg - cg) ** 2 + (mb - cb) ** 2)
            if dist < 22.0:
                cluster[0] += count
                cluster[1] += sr
                cluster[2] += sg
                cluster[3] += sb
                placed = True
                break
        if not placed:
            merged.append([float(count), float(sr), float(sg), float(sb)])
            if len(merged) >= 8:
                break

    merged.sort(key=lambda c: (c[0], c[1], c[2], c[3]), reverse=True)
    raw_cluster_count = len(merged)
    top_rgb: list[tuple[int, int, int]] = [
        (round(c[1] / c[0]), round(c[2] / c[0]), round(c[3] / c[0]))
        for c in merged[:5]
    ]
    dominant_hex = _derive_tonal_companions(top_rgb)
    return dominant_hex, raw_cluster_count


def resolve_album_media_bytes(day_root: Path | str, album_id: str) -> Optional[tuple[bytes, str]]:
    """从 ``<day_root>`` 相册目录只读解析 ``album_id`` 对应的真实图片字节。

    - 严格校验路径边界，禁止 ``..`` 或符号链接逃逸 ``day_root``；
    - 100% 零磁盘写入；若文件不存在或已被外部删除，返回 ``None``。
    """
    if not isinstance(album_id, str) or not album_id.strip() or "\x00" in album_id:
        raise V02ValidationError("INVALID_FIELDS", "Invalid album_id")
    aid = album_id.strip()
    if "/" in aid or "\\" in aid or ".." in aid or not SAFE_ALBUM_ID_RE.match(aid):
        raise V02ValidationError("INVALID_FIELDS", f"Forbidden characters in album_id: {aid!r}")

    root_path = Path(day_root)
    if not root_path.exists() or not root_path.is_dir():
        return None
    resolved_root = root_path.resolve()

    def _safe_read_file(candidate: Path) -> Optional[tuple[bytes, str]]:
        try:
            if not candidate.exists() or not candidate.is_file():
                return None
            real_cand = candidate.resolve()
            rel = real_cand.relative_to(resolved_root)
            raw = real_cand.read_bytes()
            return raw, rel.as_posix()
        except (OSError, ValueError):
            return None

    safe_stem = aid.replace(":", "_")
    search_dirs = (
        root_path / "phone" / "album",
        root_path / "album",
        root_path / "media",
        root_path / "life" / "media",
        root_path / "supply" / "media",
    )
    img_exts = ("", ".png", ".jpg", ".jpeg", ".bmp", ".ppm", ".webp")

    for folder in search_dirs:
        if not folder.exists() or not folder.is_dir():
            continue
        for stem in (aid, safe_stem):
            for ext in img_exts:
                cand = folder / f"{stem}{ext}"
                if cand.suffix.lower() == ".json":
                    continue
                hit = _safe_read_file(cand)
                if hit is not None:
                    return hit

        # 支持 album.image.v1 或 index.json 映射
        for manifest_cand in (
            folder / f"{aid}.json",
            folder / f"{safe_stem}.json",
            folder / "manifest.json",
            folder / "index.json",
        ):
            m_hit = _safe_read_file(manifest_cand)
            if m_hit is None:
                continue
            try:
                doc = json.loads(m_hit[0].decode("utf-8"))
            except (UnicodeError, ValueError):
                continue
            if not isinstance(doc, Mapping):
                continue
            media_name: Optional[str] = None
            if doc.get("kind") == "album.image.v1" and isinstance(doc.get("media"), Mapping):
                raw_name = doc["media"].get("name")
                if isinstance(raw_name, str) and SAFE_ALBUM_ID_RE.match(raw_name):
                    media_name = raw_name
            elif isinstance(doc.get("items"), Mapping) and isinstance(doc["items"].get(aid), str):
                raw_rel = doc["items"][aid]
                hit = _safe_read_file(folder / raw_rel)
                if hit is not None:
                    return hit
            if media_name:
                for media_dir in (folder, folder / "media", root_path / "media", root_path / "life" / "media"):
                    hit = _safe_read_file(media_dir / media_name)
                    if hit is not None:
                        return hit

    return None


def extract_real_image_palette(
    image_bytes: Optional[bytes] = None,
    *,
    image_base64: Optional[str] = None,
    day_root: Optional[Path | str] = None,
    album_id: Optional[str] = None,
    force_stdlib: bool = False,
) -> dict[str, Any]:
    """真实本地图片像素解码与主色调/明暗/冷暖提取器。

    - 支持 ``image_bytes``、``image_base64``（自动剥离 ``data:image/...;base64,``）或 ``(day_root, album_id)``；
    - 损坏字节或非图片数据一律抛出 ``400 INVALID_IMAGE_BYTES``，绝不拿文件名或默认值伪造色板。
    """
    provided_sources = sum(
        1 for x in (image_bytes is not None, image_base64 is not None, album_id is not None) if x
    )
    if provided_sources != 1:
        raise V02ValidationError(
            "INVALID_FIELDS",
            "Provide exactly one of image_bytes, image_base64, or album_id",
        )

    source_rel_path = ""
    if image_bytes is not None:
        if not isinstance(image_bytes, (bytes, bytearray)):
            raise V02ValidationError("INVALID_IMAGE_BYTES", "image_bytes must be raw bytes")
        raw = bytes(image_bytes)
    elif image_base64 is not None:
        raw = strip_and_decode_image_base64(image_base64)
    else:
        assert album_id is not None
        if day_root is None:
            raise V02ValidationError("INVALID_FIELDS", "day_root is required when reading album_id")
        resolved = resolve_album_media_bytes(day_root, album_id)
        if resolved is None:
            raise V02NotFoundError("ALBUM_MEDIA_NOT_FOUND", f"Album image not found on disk: {album_id!r}")
        raw, source_rel_path = resolved

    sha256_hex = hashlib.sha256(raw).hexdigest()
    width, height, content_type, pixels = decode_image_pixels(raw, force_stdlib=force_stdlib)
    dominant_hex, raw_cluster_count = _quantize_dominant_palette(pixels)

    n_px = len(pixels)
    luma_list: list[float] = []
    warmth_sum = 0.0
    luma_sum = 0.0
    for r, g, b in pixels:
        luma = 0.299 * r + 0.587 * g + 0.114 * b
        luma_list.append(luma)
        luma_sum += luma
        warmth_sum += 0.80 * r + 0.20 * g - 1.00 * b

    mean_luma = luma_sum / n_px
    mean_warmth = warmth_sum / n_px
    luma_list.sort()
    p10_luma = luma_list[int((n_px - 1) * 0.10)]
    p90_luma = luma_list[int((n_px - 1) * 0.90)]
    contrast_span = p90_luma - p10_luma

    if mean_warmth >= 18.0:
        warmth_band = "warm"
    elif mean_warmth <= -18.0:
        warmth_band = "cool"
    else:
        warmth_band = "neutral"
    warmth_label_cn = WARMTH_LABELS_CN[warmth_band]

    if mean_luma >= 165.0:
        brightness_band = "bright"
    elif mean_luma >= 85.0:
        brightness_band = "balanced"
    else:
        brightness_band = "dim"
    brightness_label_cn = BRIGHTNESS_LABELS_CN[brightness_band]

    if contrast_span >= 95.0 and len(dominant_hex) >= 2:
        mood_summary_cn = (
            f"画面呈{warmth_label_cn}、{brightness_label_cn}（明暗层次反差明显），"
            f"主色跨越 {dominant_hex[0]} 与 {dominant_hex[1]}，仅作局部配色参考。"
        )
    else:
        mood_summary_cn = (
            f"画面呈{warmth_label_cn}、{brightness_label_cn}，"
            f"主色集中在 {dominant_hex[0]} 一带，仅作局部配色参考。"
        )

    return {
        "dominant_hex": dominant_hex,
        "distinct_cluster_count": raw_cluster_count,
        "warmth_band": warmth_band,
        "warmth_label_cn": warmth_label_cn,
        "brightness_band": brightness_band,
        "brightness_label_cn": brightness_label_cn,
        "mean_luma": round(mean_luma, 2),
        "mean_warmth_index": round(mean_warmth, 2),
        "contrast_span": round(contrast_span, 2),
        "width": width,
        "height": height,
        "sha256": sha256_hex,
        "content_type": content_type,
        "mood_summary_cn": mood_summary_cn,
        "not_lived_event_claim": True,
        "source_rel_path": source_rel_path,
        "llm_calls": 0,
    }


# ---------------------------------------------------------------------------
# 状态持久化、实时删除读屏障投影与写动作状态机
# ---------------------------------------------------------------------------


def _state_file_path(day_root: Path | str) -> Path:
    return Path(day_root).joinpath(*STATE_REL_PATH)


def _empty_trial_pad() -> dict[str, Any]:
    return {
        "active": False,
        "partner_swatches": [],
        "partner_note_cn": "",
        "subject_swatches": [],
        "subject_note_cn": "",
        "combined_swatches": [],
        "entries": [],
        "zero_score_pressure": True,
        "not_permanent_preference": True,
        "requires_finished_work": False,
        "updated_at": None,
        "last_cleared_at": None,
        "last_cleared_by": None,
    }


def _empty_raw_state() -> dict[str, Any]:
    return {
        "schema": VIEW_SCHEMA,
        "revision": 0,
        "updated_at": None,
        "next_seq": 1,
        "palette_studies": [],
        "work_palette_applications": [],
        "color_trial_pad": _empty_trial_pad(),
        "reading_uncertainty_marks": [],
        "collection_items": [],
        "unshown_work_box": [],
        "audio_notes": [],
        "invalidated_box_artifact_ids": [],
        "invalidated_album_ids": [],
        "invalidated_sha256s": [],
        "operation_ledger": {},
    }


def _load_state_readonly(day_root: Path | str) -> tuple[bool, dict[str, Any]]:
    """100% 零磁盘写入加载状态。"""
    path = _state_file_path(day_root)
    if not path.exists():
        return False, _empty_raw_state()
    if not path.is_file():
        raise V02CrossmodalError(503, "CORRUPT_STATE_FILE", "State path is not a regular file")
    try:
        raw_text = path.read_text(encoding="utf-8")
        doc = json.loads(raw_text)
    except (OSError, UnicodeError, ValueError) as exc:
        raise V02CrossmodalError(503, "CORRUPT_STATE_FILE", f"Corrupted crossmodal state JSON: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != VIEW_SCHEMA:
        raise V02CrossmodalError(503, "CORRUPT_STATE_FILE", "Invalid schema in crossmodal state file")
    merged = _empty_raw_state()
    merged.update(copy.deepcopy(doc))
    return True, merged


def _atomic_write_state(day_root: Path | str, state: Mapping[str, Any]) -> None:
    target = _state_file_path(day_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    serialized = json.dumps(dict(state), ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(prefix=".v02_crossmodal_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(serialized)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, target)
    except Exception:
        try:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _is_study_source_live(day_root: Path | str, study: Mapping[str, Any], raw_state: Mapping[str, Any]) -> bool:
    """原图删除/撤回实时读屏障（100% 零写检查）。"""
    if not bool(study.get("source_available", True)):
        return False
    sha = study.get("sha256")
    if isinstance(sha, str) and sha in set(raw_state.get("invalidated_sha256s") or ()):
        return False
    aid = study.get("album_id")
    if isinstance(aid, str) and aid:
        if aid in set(raw_state.get("invalidated_album_ids") or ()):
            return False
        if resolve_album_media_bytes(day_root, aid) is None:
            return False
    return True


def _project_single_study(
    study: Mapping[str, Any],
    *,
    source_live: bool,
    has_subject_capability: bool,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "study_id": str(study.get("study_id") or ""),
        "title": str(study.get("title") or ""),
        "created_by": str(study.get("created_by") or "partner"),
        "created_at": study.get("created_at"),
        "shared": bool(study.get("shared", False)),
        "source_kind": str(study.get("source_kind") or "inline_upload"),
        "source_available": bool(source_live),
        "source_status": "available" if source_live else "deleted_or_unavailable",
        "not_lived_event_claim": True,
        "width": int(study.get("width") or 0),
        "height": int(study.get("height") or 0),
        "content_type": str(study.get("content_type") or ""),
    }
    if study.get("linked_work_id"):
        out["linked_work_id"] = str(study["linked_work_id"])

    if has_subject_capability:
        out["album_id"] = study.get("album_id")
        if study.get("source_rel_path"):
            out["source_rel_path"] = str(study["source_rel_path"])

    if source_live:
        out["dominant_hex"] = list(study.get("dominant_hex") or [])
        out["warmth_band"] = study.get("warmth_band")
        out["warmth_label_cn"] = str(study.get("warmth_label_cn") or "")
        out["brightness_band"] = study.get("brightness_band")
        out["brightness_label_cn"] = str(study.get("brightness_label_cn") or "")
        out["sha256"] = str(study.get("sha256") or "")
        out["mood_summary_cn"] = str(study.get("mood_summary_cn") or "")
        out["palette_withheld_reason"] = None
    else:
        # 铁律 2：一旦原图被删或失效，立即扣留色板，绝不通过旧缓存复活已删图片的颜色
        out["dominant_hex"] = []
        out["warmth_band"] = None
        out["warmth_label_cn"] = ""
        out["brightness_band"] = None
        out["brightness_label_cn"] = ""
        out["sha256"] = ""
        out["mood_summary_cn"] = "原图已删除或不可用，派生配色已扣留。"
        out["palette_withheld_reason"] = "SOURCE_MEDIA_UNAVAILABLE"

    applied_list: list[dict[str, Any]] = []
    for app in study.get("applied_works") or []:
        if not isinstance(app, Mapping):
            continue
        if not has_subject_capability and not bool(app.get("shared", False)):
            continue
        applied_list.append({
            "application_id": str(app.get("application_id") or ""),
            "work_id": str(app.get("work_id") or ""),
            "work_title": str(app.get("work_title") or ""),
            "version_hint": str(app.get("version_hint") or ""),
            "applied_hex": list(app.get("applied_hex") or []) if source_live else [],
            "usage_note_cn": str(app.get("usage_note_cn") or ""),
            "source_available": bool(source_live),
            "not_lived_event_claim": True,
            "shared": bool(app.get("shared", False)),
            "applied_at": app.get("applied_at"),
        })
    out["applied_works"] = applied_list
    out["four_layer_boundary"] = {
        "acquired": {
            "source_kind": out["source_kind"],
            "source_available": bool(source_live),
        },
        "perception": {
            "scope": "local_color_light_only",
            "not_scene_truth": True,
        },
        "lineage": {
            "applied_work_count": len(applied_list),
            "not_lived_event_claim": True,
        },
        "disclosure": {
            "shared": bool(study.get("shared", False)),
        },
    }
    return out


def _read_recall_tombstoned_ids_readonly(day_root: Path | str) -> frozenset[str]:
    """零写读取 recall_projection 墓碑与已删除条目 ID（支持 phone 与 memory 路径）。"""
    root = Path(day_root)
    if not root.exists() or not root.is_dir():
        return frozenset()

    tomb_ids: set[str] = set()

    def _collect_str(val: Any) -> None:
        if isinstance(val, str) and val.strip():
            tomb_ids.add(val.strip())

    def _collect_container(node: Any) -> None:
        if isinstance(node, Mapping):
            for key, val in node.items():
                if val is False:
                    continue
                if isinstance(val, Mapping):
                    if val.get("tombstoned") is False or (
                        val.get("deleted") is False and val.get("tombstoned") is not True
                    ):
                        continue
                    _collect_str(key)
                    for id_key in ("memory_id", "id", "artifact_id", "box_item_id"):
                        _collect_str(val.get(id_key))
                else:
                    _collect_str(key)
        elif isinstance(node, (list, tuple, set, frozenset)):
            for item in node:
                if isinstance(item, str):
                    _collect_str(item)
                elif isinstance(item, Mapping):
                    if item.get("tombstoned") is False or (
                        item.get("deleted") is False and item.get("tombstoned") is not True
                    ):
                        continue
                    for id_key in ("memory_id", "id", "artifact_id", "box_item_id"):
                        _collect_str(item.get(id_key))

    for rel in RECALL_PROJECTION_REL_PATHS:
        cand = root.joinpath(*rel)
        try:
            if cand.is_symlink() or not cand.is_file():
                continue
            doc = json.loads(cand.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if not isinstance(doc, Mapping):
            continue

        for key in ("tombstoned_ids", "deleted_ids", "deleted_artifact_ids", "tombstones", "tombstone_ledger"):
            if key in doc:
                _collect_container(doc.get(key))

        for col_key in ("memories", "items", "direct_recall_items", "active_items"):
            rows = doc.get(col_key)
            iter_rows = rows.values() if isinstance(rows, Mapping) else (rows if isinstance(rows, list) else ())
            for row in iter_rows:
                if not isinstance(row, Mapping):
                    continue
                is_del = bool(
                    row.get("deleted")
                    or row.get("tombstoned")
                    or str(row.get("status") or "").strip().lower() in ("deleted", "tombstoned")
                    or str(row.get("decay_mode") or "").strip() == "GOVERNANCE_TOMBSTONE"
                )
                if is_del:
                    for id_key in ("memory_id", "id", "artifact_id", "box_item_id"):
                        _collect_str(row.get(id_key))

    return frozenset(tomb_ids)


def _is_id_in_tombstones(identifier: str, tomb_ids: frozenset[str] | set[str]) -> bool:
    clean = str(identifier or "").strip()
    if not clean or not tomb_ids:
        return False
    if clean in tomb_ids:
        return True
    if clean.startswith("artifact:"):
        return clean.split(":", 1)[1] in tomb_ids
    return f"artifact:{clean}" in tomb_ids


def _project_unshown_work_box_readonly(
    day_root: Path | str,
    raw_state: Mapping[str, Any],
    *,
    has_subject_capability: bool,
) -> dict[str, Any]:
    """第 15 章 §10.A：还没给你看的盒子零写投影（伴侣侧仅见 visible_items，绝不漏出其他状态或数量）。"""
    tomb_ids = _read_recall_tombstoned_ids_readonly(day_root)
    invalidated_artifacts = {
        str(x).strip()
        for x in (raw_state.get("invalidated_box_artifact_ids") or ())
        if str(x).strip()
    }

    visible_items: list[dict[str, Any]] = []
    all_items: list[dict[str, Any]] = []
    by_state: dict[str, list[dict[str, Any]]] = {st: [] for st in BOX_CURATION_STATES}
    invalidated_items: list[dict[str, Any]] = []

    for raw_item in raw_state.get("unshown_work_box") or []:
        if not isinstance(raw_item, Mapping):
            continue
        box_item_id = str(raw_item.get("box_item_id") or "").strip()
        artifact_id = str(raw_item.get("artifact_id") or "").strip()
        version_id = str(raw_item.get("version_id") or "").strip()
        title_cn = str(raw_item.get("title_cn") or "").strip()
        raw_state_val = str(raw_item.get("curation_state") or "kept").strip()
        if raw_state_val not in BOX_CURATION_STATES:
            raw_state_val = "kept"
        reuse_mode = str(raw_item.get("reuse_mode") or "none").strip()
        if reuse_mode not in BOX_REUSE_MODES:
            reuse_mode = "none"
        note_cn = str(raw_item.get("note_cn") or "").strip()

        tombstoned = _is_id_in_tombstones(artifact_id, tomb_ids) or _is_id_in_tombstones(
            box_item_id, tomb_ids
        )
        src_deleted = bool(raw_item.get("source_deleted", False)) or _is_id_in_tombstones(
            artifact_id, invalidated_artifacts
        )
        is_invalidated = bool(raw_item.get("candidate_invalidated", False)) or src_deleted or tombstoned

        if is_invalidated:
            eff_state = "dropped" if raw_state_val == "selected_to_show" else raw_state_val
            selected_eligible = False
            work_intact = False if (src_deleted or tombstoned) else bool(raw_item.get("work_intact", True))
        else:
            eff_state = raw_state_val
            selected_eligible = True
            work_intact = bool(raw_item.get("work_intact", True))

        if eff_state == "selected_to_show" and not is_invalidated:
            visible_items.append({
                "box_item_id": box_item_id,
                "artifact_id": artifact_id,
                "version_id": version_id,
                "title_cn": title_cn,
                "curation_state": "selected_to_show",
                "curation_state_label_cn": BOX_CURATION_STATE_LABELS_CN["selected_to_show"],
                "note_cn": note_cn,
                "candidate_invalidated": False,
                "work_intact": work_intact,
                "updated_at": raw_item.get("updated_at"),
            })

        if has_subject_capability:
            subj_entry = {
                "box_item_id": box_item_id,
                "artifact_id": artifact_id,
                "version_id": version_id,
                "title_cn": title_cn,
                "curation_state": eff_state,
                "curation_state_label_cn": BOX_CURATION_STATE_LABELS_CN[eff_state],
                "reuse_mode": reuse_mode,
                "note_cn": note_cn,
                "source_deleted": src_deleted,
                "tombstoned_in_recall": tombstoned,
                "candidate_invalidated": is_invalidated,
                "selected_to_show_eligible": selected_eligible,
                "work_intact": work_intact,
                "created_at": raw_item.get("created_at"),
                "updated_at": raw_item.get("updated_at"),
            }
            all_items.append(subj_entry)
            by_state[eff_state].append(subj_entry)
            if is_invalidated:
                invalidated_items.append(subj_entry)

    if not has_subject_capability:
        # 严格伴侣隐私边界：仅投影当前主动选出展示的 visible_items，不暴露其他分类或任何数量统计
        return {
            "visible_items": visible_items,
        }

    return {
        "visible_items": visible_items,
        "items": all_items,
        "kept": by_state["kept"],
        "reused_fragment": by_state["reused_fragment"],
        "shelved": by_state["shelved"],
        "dropped": by_state["dropped"],
        "selected_to_show": by_state["selected_to_show"],
        "invalidated_items": invalidated_items,
        "counts_by_state": {st: len(by_state[st]) for st in ("kept", "reused_fragment", "shelved", "dropped", "selected_to_show")},
        "total_count": len(all_items),
        "hidden_count": len(all_items) - len(visible_items),
    }


def project_v02_crossmodal(
    day_root: Path | str,
    *,
    has_subject_capability: bool = False,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """构建零写只读投影，自动执行伴侣侧隐私剥离与已删媒体读屏障。"""
    obs_iso = _now_iso_str(now_iso)
    exists, raw_state = _load_state_readonly(day_root)

    study_live_map: dict[str, bool] = {}
    visible_studies: list[dict[str, Any]] = []
    private_studies_withheld_count = 0

    for study in raw_state.get("palette_studies") or []:
        if not isinstance(study, Mapping):
            continue
        sid = str(study.get("study_id") or "")
        is_live = _is_study_source_live(day_root, study, raw_state)
        study_live_map[sid] = is_live
        is_shared = bool(study.get("shared", False))
        if not has_subject_capability and not is_shared:
            private_studies_withheld_count += 1
            continue
        visible_studies.append(
            _project_single_study(
                study,
                source_live=is_live,
                has_subject_capability=has_subject_capability,
            )
        )

    visible_work_apps: list[dict[str, Any]] = []
    for app in raw_state.get("work_palette_applications") or []:
        if not isinstance(app, Mapping):
            continue
        if not has_subject_capability and not bool(app.get("shared", False)):
            continue
        sid = str(app.get("study_id") or "")
        is_live = study_live_map.get(sid, bool(app.get("source_available", True)))
        visible_work_apps.append({
            "application_id": str(app.get("application_id") or ""),
            "study_id": sid,
            "work_id": str(app.get("work_id") or ""),
            "work_title": str(app.get("work_title") or ""),
            "version_hint": str(app.get("version_hint") or ""),
            "applied_hex": list(app.get("applied_hex") or []) if is_live else [],
            "usage_note_cn": str(app.get("usage_note_cn") or ""),
            "source_available": bool(is_live),
            "not_lived_event_claim": True,
            "work_verified": bool(app.get("work_verified", False)),
            "work_binding_source": str(app.get("work_binding_source") or "draft_reference"),
            "bound_version_id": str(app.get("bound_version_id") or ""),
            "bound_version_number": app.get("bound_version_number"),
            "tray_linked": bool(app.get("tray_linked", False)),
            "shared": bool(app.get("shared", False)),
            "applied_at": app.get("applied_at"),
        })

    raw_pad = raw_state.get("color_trial_pad")
    trial_pad = _empty_trial_pad()
    if isinstance(raw_pad, Mapping):
        trial_pad.update(copy.deepcopy(dict(raw_pad)))
    trial_pad["zero_score_pressure"] = True
    trial_pad["not_permanent_preference"] = True
    trial_pad["requires_finished_work"] = False

    visible_marks: list[dict[str, Any]] = []
    private_marks_withheld_count = 0
    for mark in raw_state.get("reading_uncertainty_marks") or []:
        if not isinstance(mark, Mapping):
            continue
        is_shared = bool(mark.get("shared", False))
        if not has_subject_capability and not is_shared:
            private_marks_withheld_count += 1
            continue
        m_copy = copy.deepcopy(dict(mark))
        m_copy["page_equals_understood"] = False
        m_copy["auto_progress_percent"] = None
        if not has_subject_capability:
            for priv_key in (
                "phone_reading_item_id",
                "phone_reading_source_id",
                "phone_reading_source_revision",
            ):
                m_copy.pop(priv_key, None)
        visible_marks.append(m_copy)

    visible_collections: list[dict[str, Any]] = []
    private_collections_withheld = 0
    counts_by_status: dict[str, int] = {k: 0 for k in COLLECTION_STATUSES}
    invalidated_albums = set(raw_state.get("invalidated_album_ids") or ())

    for item in raw_state.get("collection_items") or []:
        if not isinstance(item, Mapping):
            continue
        is_shared = bool(item.get("shared", False))
        if not has_subject_capability and not is_shared:
            private_collections_withheld += 1
            continue
        eff_status = str(item.get("status") or "fresh")
        aid = item.get("album_id")
        src_avail = bool(item.get("source_available", True))
        if isinstance(aid, str) and aid:
            if aid in invalidated_albums or resolve_album_media_bytes(day_root, aid) is None:
                eff_status = "stale_source"
                src_avail = False
        if eff_status not in COLLECTION_STATUSES:
            eff_status = "fresh"
        counts_by_status[eff_status] = counts_by_status.get(eff_status, 0) + 1
        projected_item: dict[str, Any] = {
            "item_id": str(item.get("item_id") or ""),
            "title": str(item.get("title") or ""),
            "source_kind": str(item.get("source_kind") or "link"),
            "status": eff_status,
            "status_label_cn": COLLECTION_STATUS_LABELS_CN[eff_status],
            "source_available": src_avail,
            "note_cn": str(item.get("note_cn") or ""),
            "saved_equals_read": False,
            "saved_equals_permanent_like": False,
            "shared": is_shared,
            "checked_at": item.get("checked_at"),
        }
        if has_subject_capability:
            projected_item["album_id"] = item.get("album_id")
            projected_item["source_ref"] = item.get("source_ref")
        elif is_shared and not item.get("album_id") and item.get("source_ref"):
            projected_item["source_ref"] = item.get("source_ref")
        if item.get("curated_by"):
            projected_item["curated_by"] = str(item.get("curated_by"))
        visible_collections.append(projected_item)

    collection_health: dict[str, Any] = {
        "items": visible_collections,
        "total_visible": len(visible_collections),
        "counts_by_status": counts_by_status,
        "private_items_withheld_count": private_collections_withheld if not has_subject_capability else 0,
        "saved_equals_read": False,
        "saved_equals_permanent_like": False,
    }

    unshown_work_box = _project_unshown_work_box_readonly(
        day_root,
        raw_state,
        has_subject_capability=has_subject_capability,
    )

    visible_audio_notes: list[dict[str, Any]] = []
    for raw_note in (raw_state.get("audio_notes") or [])[:AUDIO_NOTE_MAX_ENTRIES]:
        if not isinstance(raw_note, Mapping):
            continue
        is_shared = bool(raw_note.get("shared", False))
        if not has_subject_capability and not is_shared:
            continue
        t_cn = str(raw_note.get("transcript_cn") or "").strip()
        t_status = (
            AUDIO_TRANSCRIPT_STATUS_USER_SUPPLIED
            if t_cn
            else AUDIO_TRANSCRIPT_STATUS_UNAVAILABLE
        )
        visible_audio_notes.append({
            "audio_note_id": str(raw_note.get("audio_note_id") or ""),
            "title_cn": str(raw_note.get("title_cn") or ""),
            "duration_seconds": round(float(raw_note.get("duration_seconds") or 0.0), 2),
            "audio_format": str(raw_note.get("audio_format") or "voice_memo"),
            "source_kind": str(raw_note.get("source_kind") or "local_file"),
            "sha256_hex": str(raw_note.get("sha256_hex") or ""),
            "transcript_cn": t_cn,
            "transcript_status": t_status,
            "ambient_tag_cn": str(raw_note.get("ambient_tag_cn") or ""),
            "author_side": str(raw_note.get("author_side") or "partner"),
            "shared": is_shared,
            "not_lived_event_claim": True,
            "created_at": raw_note.get("created_at"),
        })

    return {
        "state_exists": bool(exists),
        "perspective": "subject" if has_subject_capability else "partner",
        "revision": int(raw_state.get("revision") or 0),
        "observed_at": obs_iso,
        "palette_studies": visible_studies,
        "work_palette_applications": visible_work_apps,
        "color_trial_pad": trial_pad,
        "reading_uncertainty_marks": visible_marks,
        "collection_health": collection_health,
        "unshown_work_box": unshown_work_box,
        "audio_notes": visible_audio_notes,
        "audio_note_count": len(visible_audio_notes),
        "private_studies_withheld_count": private_studies_withheld_count if not has_subject_capability else 0,
        "private_marks_withheld_count": private_marks_withheld_count if not has_subject_capability else 0,
        "llm_calls": 0,
    }


def _compute_payload_fingerprint(actor_side: str, payload: Mapping[str, Any]) -> str:
    filtered = {
        k: v
        for k, v in sorted(payload.items())
        if k not in ("operation_key", "expected_revision")
    }
    blob = json.dumps({"actor_side": actor_side, "payload": filtered}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _allocate_id(state: dict[str, Any], prefix: str) -> str:
    seq = int(state.get("next_seq") or 1)
    state["next_seq"] = seq + 1
    return f"{prefix}_{seq:04d}"


def _try_resolve_phone_reading_facade(day_root: Path | str, phone_reading: Optional[Any]) -> Optional[Any]:
    """优先使用显式注入的 ``PhoneReading``；否则零写检查 ``day_root`` 下的阅读绑定配置。"""
    if phone_reading is not None:
        return phone_reading
    root = Path(day_root)
    for rel in ("phone/reading_binding.json", "life/state/reading_binding.json"):
        cfg_path = root / rel
        try:
            if cfg_path.is_symlink() or not cfg_path.is_file():
                continue
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(cfg, Mapping):
            continue
        sock_raw = str(cfg.get("socket_path") or "").strip()
        subject_id = str(cfg.get("subject_id") or cfg.get("subject") or "").strip()
        reading_aid = str(cfg.get("reading_artifact_id") or "").strip()
        pdi_aid = str(cfg.get("private_items_artifact_id") or "").strip()
        if not (sock_raw and subject_id and reading_aid and pdi_aid):
            continue
        sock_path = Path(sock_raw) if Path(sock_raw).is_absolute() else (root / sock_raw)
        if not sock_path.exists():
            continue
        try:
            import life_artifact_port as lap
            import phone_reading as pr

            port = lap.LifeArtifactPort(str(sock_path), subject_id)
            return pr.PhoneReading(
                port,
                reading_artifact_id=reading_aid,
                private_items_artifact_id=pdi_aid,
                workspace_id=cfg.get("workspace_id"),
                grant_id=cfg.get("grant_id"),
            )
        except Exception:
            return None
    return None


def sync_reading_uncertainty_to_phone_reading(
    day_root: Path | str,
    *,
    mark_doc: Mapping[str, Any],
    actor_side: str,
    op_key: Optional[str] = None,
    phone_reading: Optional[Any] = None,
) -> dict[str, Any]:
    """将未懂阅读书签真实联动写入 ``PhoneReading.annotate("unclear", ...)``。"""
    mark_state = str(mark_doc.get("state") or "seen_not_understood")
    if mark_state == "clarified":
        return {
            "synced": False,
            "annotation_kind": None,
            "reason": "CLARIFIED_NO_UNCLEAR_ANNOTATION",
        }
    facade = _try_resolve_phone_reading_facade(day_root, phone_reading)
    if facade is None or not hasattr(facade, "annotate"):
        return {
            "synced": False,
            "annotation_kind": "unclear",
            "reason": "PHONE_READING_NOT_BOUND",
        }

    page_num = int(mark_doc.get("page") or 1)
    try:
        try:
            mat = facade.material(page_num)
        except TypeError:
            mat = facade.material()
    except Exception as exc:
        return {
            "synced": False,
            "annotation_kind": "unclear",
            "reason": f"MATERIAL_READ_ERROR:{type(exc).__name__}",
        }

    if not isinstance(mat, Mapping) or not mat.get("ok"):
        reason = str((mat or {}).get("reason_code") or "READING_MATERIAL_UNAVAILABLE") if isinstance(mat, Mapping) else "READING_MATERIAL_UNAVAILABLE"
        return {
            "synced": False,
            "annotation_kind": "unclear",
            "reason": reason,
        }

    mat_data = mat.get("data") if isinstance(mat.get("data"), Mapping) else {}
    if not mat_data.get("available", True):
        return {
            "synced": False,
            "annotation_kind": "unclear",
            "reason": str(mat.get("reason_code") or "READING_MATERIAL_UNAVAILABLE"),
        }

    source_revision = str(mat_data.get("source_revision") or "").strip()
    private_head = str(mat_data.get("private_head") or "").strip()
    if not source_revision or not private_head:
        return {
            "synced": False,
            "annotation_kind": "unclear",
            "reason": "READING_HEAD_UNAVAILABLE",
        }

    mark_id = str(mark_doc.get("mark_id") or "rmark")
    rev_cnt = int(mark_doc.get("revision_count") or 1)
    raw_token = f"{op_key or mark_id}-r{rev_cnt}-p{page_num}"
    safe_suffix = re.sub(r"[^A-Za-z0-9_.:-]+", "_", raw_token)[:140]
    ann_key = f"cm_unclear:{safe_suffix}"
    note_text = f"[{mark_doc.get('book_title', '')} P{page_num}] {mark_doc.get('passage_hint', '')}".strip()[:4000]

    try:
        ans = facade.annotate(
            "unclear",
            page_num,
            source_revision,
            private_head,
            ann_key,
            note=note_text,
        )
        if (
            isinstance(ans, Mapping)
            and not ans.get("ok")
            and ans.get("reason_code") == "PAGE_NOT_ACQUIRED"
            and actor_side == "subject"
            and hasattr(facade, "save_position")
        ):
            pos_key = f"cm_pos:{safe_suffix}"
            pos_ans = facade.save_position(page_num, source_revision, private_head, pos_key)
            if isinstance(pos_ans, Mapping) and pos_ans.get("ok"):
                pos_data = pos_ans.get("data") if isinstance(pos_ans.get("data"), Mapping) else {}
                next_head = str(pos_data.get("private_head") or private_head).strip()
                ans = facade.annotate(
                    "unclear",
                    page_num,
                    source_revision,
                    next_head,
                    ann_key,
                    note=note_text,
                )
    except Exception as exc:
        return {
            "synced": False,
            "annotation_kind": "unclear",
            "reason": f"ANNOTATE_ERROR:{type(exc).__name__}",
        }

    if isinstance(ans, Mapping) and ans.get("ok"):
        ans_data = ans.get("data") if isinstance(ans.get("data"), Mapping) else {}
        return {
            "synced": True,
            "annotation_kind": "unclear",
            "status": str(ans.get("status") or "applied"),
            "item_id": ans_data.get("item_id") or ans_data.get("id"),
            "source_id": ans_data.get("source_id"),
            "source_revision": ans_data.get("source_revision") or source_revision,
            "reason": None,
        }

    reason_code = str(ans.get("reason_code") or ans.get("status") or "ANNOTATE_REJECTED") if isinstance(ans, Mapping) else "ANNOTATE_REJECTED"
    return {
        "synced": False,
        "annotation_kind": "unclear",
        "reason": reason_code,
    }


def _read_workbench_tray_candidate_readonly(day_root: Path | str) -> tuple[Optional[Path], Optional[dict[str, Any]], list[dict[str, Any]]]:
    """零写读取 ``phone/workbench_tray.json``（回退兼容 ``phone/workbench_curio.json``）。"""
    root = Path(day_root)
    tray_file = root / "phone" / "workbench_tray.json"
    curio_file = root / "phone" / "workbench_curio.json"
    candidates: list[dict[str, Any]] = []
    valid_tray_doc: Optional[dict[str, Any]] = None
    target_tray_path: Optional[Path] = None

    for path, is_primary in ((tray_file, True), (curio_file, False)):
        try:
            if path.is_symlink() or not path.is_file():
                continue
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if not isinstance(doc, Mapping):
            continue
        if is_primary and doc.get("schema_version") == 1 and isinstance(doc.get("tray"), Mapping):
            valid_tray_doc = dict(doc)
            target_tray_path = path
        tray_obj = doc.get("tray")
        if isinstance(tray_obj, Mapping):
            work_obj = tray_obj.get("work")
            if isinstance(work_obj, Mapping):
                aid = str(work_obj.get("artifact_id") or work_obj.get("work_id") or "").strip()
                if aid:
                    candidates.append({
                        "artifact_id": aid,
                        "title": str(work_obj.get("title") or "").strip(),
                        "version_id": str(work_obj.get("baseline_version_id") or "").strip(),
                        "version_number": work_obj.get("baseline_version_number")
                        if _is_strict_int(work_obj.get("baseline_version_number"))
                        else None,
                        "source": "workbench_tray" if is_primary else "workbench_curio_fallback",
                    })
        for list_key in ("works", "items", "drafts"):
            rows = doc.get(list_key)
            if isinstance(rows, list):
                for row in rows:
                    if not isinstance(row, Mapping):
                        continue
                    aid = str(row.get("artifact_id") or row.get("work_id") or row.get("id") or "").strip()
                    title = str(row.get("title") or row.get("work_title") or "").strip()
                    if aid or title:
                        candidates.append({
                            "artifact_id": aid,
                            "title": title,
                            "version_id": str(row.get("baseline_version_id") or row.get("version_id") or "").strip(),
                            "version_number": row.get("version_number")
                            if _is_strict_int(row.get("version_number"))
                            else None,
                            "source": "workbench_tray" if is_primary else "workbench_curio_fallback",
                        })
    return target_tray_path, valid_tray_doc, candidates


def _lookup_supply_sqlite_work_readonly(
    day_root: Path | str,
    *,
    work_id_in: str,
    work_title_in: str,
) -> tuple[bool, Optional[dict[str, Any]]]:
    """零写只读查询 ``day_root`` 下真实 Supply SQLite（返回 ``(db_present, matched_work)``）。"""
    import sqlite3

    root = Path(day_root)
    db_candidates = (
        root / "data" / "life-supply.sqlite3",
        root / "supply" / "life-supply.sqlite3",
        root / "life-supply.sqlite3",
        root / "state" / "life-supply.sqlite3",
    )
    found_db: Optional[Path] = None
    for cand in db_candidates:
        try:
            if not cand.is_symlink() and cand.is_file():
                found_db = cand
                break
        except OSError:
            continue
    if found_db is None:
        return False, None

    try:
        uri = found_db.resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
    except Exception:
        return False, None

    try:
        tables = {
            str(r[0])
            for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        if "managed_artifacts" not in tables:
            return False, None
        rows = conn.execute(
            "SELECT artifact_id, title, lifecycle, head_version_id FROM managed_artifacts"
        ).fetchall()
        has_versions = "artifact_versions" in tables
        for row in rows:
            if str(row["lifecycle"] or "ACTIVE").upper() == "ARCHIVED":
                continue
            aid = str(row["artifact_id"] or "").strip()
            title = str(row["title"] or "").strip()
            head_vid = str(row["head_version_id"] or "").strip()
            id_match = bool(work_id_in) and (
                aid == work_id_in
                or f"artifact:{aid}" == work_id_in
                or (work_id_in.startswith("artifact:") and aid == work_id_in.split(":", 1)[1])
            )
            title_match = (not work_id_in) and bool(work_title_in) and title == work_title_in
            if not (id_match or title_match):
                continue
            ver_num: Optional[int] = None
            content_str = ""
            if has_versions and head_vid:
                vrow = conn.execute(
                    "SELECT version_number, content FROM artifact_versions WHERE version_id = ? LIMIT 1",
                    (head_vid,),
                ).fetchone()
                if vrow is not None:
                    if _is_strict_int(vrow["version_number"]):
                        ver_num = int(vrow["version_number"])
                    content_str = str(vrow["content"] or "")
            if '"album.image.v1"' in content_str:
                continue
            return True, {
                "artifact_id": aid,
                "title": title,
                "version_id": head_vid,
                "version_number": ver_num,
            }
        return True, None
    except Exception:
        return False, None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _atomic_write_json_file(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
    fd, tmp_path = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.chmod(tmp_path, 0o600)
        except OSError:
            pass
        os.replace(tmp_path, path)
        tmp_path = ""
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def resolve_and_bind_work_for_palette(
    day_root: Path | str,
    *,
    work_id_in: str,
    work_title_in: str,
    version_hint: str,
    fallback_app_id: str,
    obs_iso: str,
    project_service: Optional[Any] = None,
) -> dict[str, Any]:
    """真实校验并关联 ``PrivateProjectService`` 作品或 ``phone/workbench_tray.json`` 托盘作品。"""
    tray_path, valid_tray_doc, tray_candidates = _read_workbench_tray_candidate_readonly(day_root)

    matched_tray: Optional[dict[str, Any]] = None
    for cand in tray_candidates:
        cid = cand["artifact_id"]
        ctitle = cand["title"]
        if work_id_in and cid and (
            cid == work_id_in
            or f"artifact:{cid}" == work_id_in
            or (work_id_in.startswith("artifact:") and cid == work_id_in.split(":", 1)[1])
        ):
            matched_tray = cand
            break
        if not work_id_in and work_title_in and ctitle and ctitle == work_title_in:
            matched_tray = cand
            break
    if matched_tray is None and not work_id_in and len(tray_candidates) == 1 and tray_candidates[0]["artifact_id"]:
        matched_tray = tray_candidates[0]

    # 1. 显式 PrivateProjectService 校验与绑定
    if project_service is not None:
        matched_proj: Optional[dict[str, Any]] = None
        if work_id_in and hasattr(project_service, "read_project"):
            ans = project_service.read_project(work_id_in, include_history=False)
            if isinstance(ans, Mapping) and ans.get("ok") and isinstance(ans.get("project"), Mapping):
                proj = ans["project"]
                if str(proj.get("lifecycle") or "ACTIVE").upper() != "ARCHIVED":
                    matched_proj = dict(proj)
        if matched_proj is None and work_title_in and hasattr(project_service, "list_projects"):
            listed = project_service.list_projects()
            if isinstance(listed, Mapping) and listed.get("ok"):
                for proj in listed.get("projects") or []:
                    if isinstance(proj, Mapping) and str(proj.get("title") or "").strip() == work_title_in:
                        matched_proj = dict(proj)
                        break
        if matched_proj is not None:
            aid = str(matched_proj.get("artifact_id") or work_id_in)
            title = work_title_in or str(matched_proj.get("title") or aid)
            head_vid = str(matched_proj.get("head_version_id") or "")
            ver_num = (
                int(matched_proj["version_number"])
                if _is_strict_int(matched_proj.get("version_number"))
                else None
            )
            tray_linked = bool(matched_tray is not None)
            if tray_path is not None and valid_tray_doc is not None:
                tray_inner = dict(valid_tray_doc.get("tray") or {})
                if tray_inner.get("work") is None:
                    tray_inner["work"] = {
                        "artifact_id": aid,
                        "baseline_version_id": head_vid,
                        "baseline_version_number": ver_num,
                        "added_at": obs_iso[:40],
                    }
                    updated_tray = {
                        "schema_version": 1,
                        "revision": int(valid_tray_doc.get("revision") or 0) + 1,
                        "updated_at": obs_iso[:40],
                        "tray": tray_inner,
                    }
                    _atomic_write_json_file(tray_path, updated_tray)
                    tray_linked = True
            return {
                "work_id": aid,
                "work_title": title,
                "version_hint": version_hint or head_vid,
                "work_verified": True,
                "work_binding_source": "private_project_service",
                "bound_version_id": head_vid,
                "bound_version_number": ver_num,
                "tray_linked": tray_linked,
            }
        if matched_tray is None:
            raise V02NotFoundError(
                "WORK_NOT_FOUND",
                f"Target work {work_id_in or work_title_in!r} not found in PrivateProjectService or workbench tray",
            )

    # 2. 工作台托盘（phone/workbench_tray.json 或回退 workbench_curio.json）关联
    if matched_tray is not None:
        aid = matched_tray["artifact_id"] or work_id_in or f"work:{fallback_app_id}"
        title = work_title_in or matched_tray["title"] or aid
        vid = matched_tray["version_id"]
        vnum = matched_tray["version_number"]
        return {
            "work_id": aid,
            "work_title": title,
            "version_hint": version_hint or vid,
            "work_verified": True,
            "work_binding_source": str(matched_tray["source"]),
            "bound_version_id": vid,
            "bound_version_number": vnum,
            "tray_linked": True,
        }

    # 3. 真实 Supply SQLite 只读核验（当实例目录存在 Supply 库时）
    db_present, db_work = _lookup_supply_sqlite_work_readonly(
        day_root,
        work_id_in=work_id_in,
        work_title_in=work_title_in,
    )
    if db_work is not None:
        aid = db_work["artifact_id"]
        title = work_title_in or db_work["title"] or aid
        vid = db_work["version_id"]
        vnum = db_work["version_number"]
        tray_linked = False
        if tray_path is not None and valid_tray_doc is not None:
            tray_inner = dict(valid_tray_doc.get("tray") or {})
            if tray_inner.get("work") is None:
                tray_inner["work"] = {
                    "artifact_id": aid,
                    "baseline_version_id": vid,
                    "baseline_version_number": vnum,
                    "added_at": obs_iso[:40],
                }
                updated_tray = {
                    "schema_version": 1,
                    "revision": int(valid_tray_doc.get("revision") or 0) + 1,
                    "updated_at": obs_iso[:40],
                    "tray": tray_inner,
                }
                _atomic_write_json_file(tray_path, updated_tray)
                tray_linked = True
        return {
            "work_id": aid,
            "work_title": title,
            "version_hint": version_hint or vid,
            "work_verified": True,
            "work_binding_source": "supply_sqlite_ro",
            "bound_version_id": vid,
            "bound_version_number": vnum,
            "tray_linked": tray_linked,
        }
    if db_present and work_id_in:
        raise V02NotFoundError(
            "WORK_NOT_FOUND",
            f"Target work {work_id_in!r} not found in Supply artifacts or workbench tray",
        )

    # 4. 轻量未挂载 owner 时的草稿引用兼容回退
    final_work_id = work_id_in or f"work:{fallback_app_id}"
    final_work_title = work_title_in or final_work_id
    return {
        "work_id": final_work_id,
        "work_title": final_work_title,
        "version_hint": version_hint,
        "work_verified": False,
        "work_binding_source": "draft_reference",
        "bound_version_id": "",
        "bound_version_number": None,
        "tray_linked": False,
    }


def apply_crossmodal_mutation(
    day_root: Path | str,
    payload: Mapping[str, Any],
    *,
    actor_side: str = "partner",
    now_iso: Optional[str] = None,
    phone_reading: Optional[Any] = None,
    project_service: Optional[Any] = None,
) -> tuple[dict[str, Any], str]:
    """执行受控跨模态状态更新，支持 ``operation_key`` 幂等重放与真实阅读/作品桥接。"""
    if actor_side not in ("partner", "subject"):
        raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", f"Invalid actor_side: {actor_side!r}")

    obs_iso = _now_iso_str(now_iso)
    _exists, state = _load_state_readonly(day_root)
    has_cap = actor_side == "subject"

    expected_rev = payload.get("expected_revision")
    if expected_rev is not None:
        if not _is_strict_int(expected_rev) or expected_rev < 0:
            raise V02ValidationError("INVALID_FIELDS", "expected_revision must be a non-negative integer")
        if int(state.get("revision") or 0) != expected_rev:
            raise V02ConflictError("REVISION_MISMATCH", "State revision does not match expected_revision")

    op_key = _optional_clean_str(payload.get("operation_key"), "operation_key", max_len=128)
    fp = _compute_payload_fingerprint(actor_side, payload)
    ledger = state.setdefault("operation_ledger", {})
    if op_key:
        ledger_entry = ledger.get(op_key)
        if isinstance(ledger_entry, Mapping):
            if ledger_entry.get("fingerprint") != fp:
                raise V02ConflictError(
                    "OPERATION_KEY_CONFLICT",
                    f"operation_key {op_key!r} already used with different payload",
                )
            projected = project_v02_crossmodal(
                day_root,
                has_subject_capability=has_cap,
                now_iso=obs_iso,
            )
            cached_receipt = ledger_entry.get("action_receipt")
            if isinstance(cached_receipt, Mapping):
                projected["action_receipt"] = copy.deepcopy(dict(cached_receipt))
            return projected, "DUPLICATE"

    action = str(payload.get("action") or "").strip()
    action_receipt: Optional[dict[str, Any]] = None

    if action == "analyze_image_palette":
        title = _require_clean_str(payload.get("title"), "title", max_len=120)
        raw_b64 = payload.get("image_base64")
        raw_aid = payload.get("album_id")
        if (
            "image_base64" in payload
            and "album_id" not in payload
            and isinstance(raw_b64, str)
            and not raw_b64.strip()
        ):
            raise V02ValidationError("INVALID_IMAGE_BYTES", "image_base64 must not be empty")
        has_b64 = raw_b64 is not None and (not isinstance(raw_b64, str) or bool(raw_b64.strip()))
        has_aid = raw_aid is not None and (not isinstance(raw_aid, str) or bool(raw_aid.strip()))
        if has_b64 == has_aid:
            raise V02ValidationError(
                "INVALID_FIELDS",
                "Exactly one of 'image_base64' or 'album_id' must be provided",
            )
        raw_share = payload.get("share_with_room")
        if raw_share is None:
            share_with_room = True if actor_side == "partner" else False
        else:
            if not isinstance(raw_share, bool):
                raise V02ValidationError("INVALID_FIELDS", "Field 'share_with_room' must be a boolean")
            share_with_room = raw_share

        if has_b64:
            if not isinstance(raw_b64, str):
                raise V02ValidationError("INVALID_IMAGE_BYTES", "image_base64 must be a string")
            palette = extract_real_image_palette(image_base64=raw_b64)
            album_id_val: Optional[str] = None
            source_kind = "inline_upload"
        else:
            aid_clean = _require_clean_str(raw_aid, "album_id", max_len=120)
            if aid_clean in set(state.get("invalidated_album_ids") or ()):
                raise V02ConflictError("SOURCE_MEDIA_UNAVAILABLE", f"Album source {aid_clean!r} was invalidated")
            palette = extract_real_image_palette(day_root=day_root, album_id=aid_clean)
            album_id_val = aid_clean
            source_kind = "album_media"

        study_id = _allocate_id(state, "pal")
        linked_work_id = _optional_clean_str(payload.get("work_id"), "work_id", max_len=96)
        study_doc: dict[str, Any] = {
            "study_id": study_id,
            "title": title,
            "created_by": actor_side,
            "created_at": obs_iso,
            "shared": share_with_room,
            "source_kind": source_kind,
            "album_id": album_id_val,
            "source_rel_path": palette.get("source_rel_path") or "",
            "source_available": True,
            "not_lived_event_claim": True,
            "dominant_hex": palette["dominant_hex"],
            "warmth_band": palette["warmth_band"],
            "warmth_label_cn": palette["warmth_label_cn"],
            "brightness_band": palette["brightness_band"],
            "brightness_label_cn": palette["brightness_label_cn"],
            "width": palette["width"],
            "height": palette["height"],
            "sha256": palette["sha256"],
            "content_type": palette["content_type"],
            "mood_summary_cn": palette["mood_summary_cn"],
            "applied_works": [],
            **({"linked_work_id": linked_work_id} if linked_work_id else {}),
        }
        state.setdefault("palette_studies", []).insert(0, study_doc)

    elif action == "try_color_swatches":
        raw_swatches = payload.get("swatches")
        if not isinstance(raw_swatches, list):
            raise V02ValidationError("INVALID_FIELDS", "Field 'swatches' must be a list of 1..3 #RRGGBB strings")
        if len(raw_swatches) == 0 or len(raw_swatches) > 3:
            for candidate_hex in raw_swatches:
                validate_hex_color(candidate_hex)
            raise V02ValidationError("INVALID_FIELDS", "Field 'swatches' must contain 1 to 3 colors")
        clean_swatches = [validate_hex_color(item) for item in raw_swatches]
        note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=160)

        pad = state.get("color_trial_pad")
        if not isinstance(pad, dict):
            pad = _empty_trial_pad()
        if actor_side == "partner":
            pad["partner_swatches"] = clean_swatches
            pad["partner_note_cn"] = note_cn
        else:
            pad["subject_swatches"] = clean_swatches
            pad["subject_note_cn"] = note_cn
        entries = list(pad.get("entries") or [])
        entries.append({
            "by": actor_side,
            "swatches": clean_swatches,
            "note_cn": note_cn,
            "placed_at": obs_iso,
        })
        pad["entries"] = entries[-6:]
        combined: list[str] = []
        for hx in list(pad.get("subject_swatches") or []) + list(pad.get("partner_swatches") or []):
            if hx not in combined:
                combined.append(hx)
        pad["combined_swatches"] = combined
        pad["active"] = True
        pad["zero_score_pressure"] = True
        pad["not_permanent_preference"] = True
        pad["requires_finished_work"] = False
        pad["updated_at"] = obs_iso
        state["color_trial_pad"] = pad

    elif action == "clear_color_trial_pad":
        fresh_pad = _empty_trial_pad()
        fresh_pad["last_cleared_at"] = obs_iso
        fresh_pad["last_cleared_by"] = actor_side
        state["color_trial_pad"] = fresh_pad

    elif action == "mark_reading_uncertain":
        mark_id_in = _optional_clean_str(payload.get("mark_id"), "mark_id", max_len=64)
        book_title = _require_clean_str(payload.get("book_title"), "book_title", max_len=160)
        raw_page = payload.get("page")
        if not _is_strict_int(raw_page) or raw_page < 1 or raw_page > 50_000:
            raise V02ValidationError("INVALID_FIELDS", "Field 'page' must be an integer between 1 and 50000")
        page_num = int(raw_page)
        passage_hint = _require_clean_str(payload.get("passage_hint"), "passage_hint", max_len=240)

        raw_state_val = payload.get("state")
        if raw_state_val is None:
            mark_state = "seen_not_understood"
        else:
            mark_state = _require_clean_str(raw_state_val, "state", max_len=40)
            if mark_state not in READING_UNCERTAINTY_STATES:
                raise V02ValidationError("INVALID_FIELDS", f"Invalid reading uncertainty state: {mark_state!r}")

        raw_share = payload.get("share_with_room")
        if raw_share is not None and not isinstance(raw_share, bool):
            raise V02ValidationError("INVALID_FIELDS", "Field 'share_with_room' must be a boolean")

        marks: list[dict[str, Any]] = list(state.get("reading_uncertainty_marks") or [])
        active_mark_ref: dict[str, Any]
        if mark_id_in:
            target_mark: Optional[dict[str, Any]] = None
            for m in marks:
                if isinstance(m, dict) and m.get("mark_id") == mark_id_in:
                    target_mark = m
                    break
            if target_mark is None:
                raise V02NotFoundError("MARK_NOT_FOUND", f"Reading mark not found: {mark_id_in!r}")
            if actor_side == "partner" and target_mark.get("created_by") != "partner":
                raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Partner cannot overwrite subject's reading mark")
            prev_pages = list(target_mark.get("visited_pages") or [target_mark.get("page", page_num)])
            if page_num not in prev_pages:
                prev_pages.append(page_num)
            target_mark["book_title"] = book_title
            target_mark["page"] = page_num
            target_mark["visited_pages"] = prev_pages[-10:]
            target_mark["passage_hint"] = passage_hint
            target_mark["state"] = mark_state
            target_mark["state_label_cn"] = READING_STATE_LABELS_CN[mark_state]
            if raw_share is not None:
                target_mark["shared"] = raw_share
            target_mark["revision_count"] = int(target_mark.get("revision_count") or 1) + 1
            target_mark["updated_at"] = obs_iso
            active_mark_ref = target_mark
        else:
            default_shared = True if actor_side == "partner" else False
            shared_flag = default_shared if raw_share is None else raw_share
            new_mark: dict[str, Any] = {
                "mark_id": _allocate_id(state, "rmark"),
                "book_title": book_title,
                "page": page_num,
                "visited_pages": [page_num],
                "passage_hint": passage_hint,
                "state": mark_state,
                "state_label_cn": READING_STATE_LABELS_CN[mark_state],
                "page_equals_understood": False,
                "auto_progress_percent": None,
                "created_by": actor_side,
                "shared": shared_flag,
                "revision_count": 1,
                "created_at": obs_iso,
                "updated_at": obs_iso,
            }
            marks.insert(0, new_mark)
            active_mark_ref = new_mark

        sync_res = sync_reading_uncertainty_to_phone_reading(
            day_root,
            mark_doc=active_mark_ref,
            actor_side=actor_side,
            op_key=op_key,
            phone_reading=phone_reading,
        )
        active_mark_ref["phone_reading_synced"] = bool(sync_res.get("synced", False))
        active_mark_ref["phone_reading_annotation_kind"] = sync_res.get("annotation_kind")
        active_mark_ref["phone_reading_reason"] = sync_res.get("reason")
        if sync_res.get("item_id"):
            active_mark_ref["phone_reading_item_id"] = sync_res["item_id"]
        if sync_res.get("source_id"):
            active_mark_ref["phone_reading_source_id"] = sync_res["source_id"]
        if sync_res.get("source_revision"):
            active_mark_ref["phone_reading_source_revision"] = sync_res["source_revision"]
        state["reading_uncertainty_marks"] = marks

    elif action == "apply_palette_to_work":
        if actor_side != "subject":
            raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can apply palette to work")
        study_id_req = _require_clean_str(payload.get("study_id"), "study_id", max_len=64)
        work_id_in = _optional_clean_str(payload.get("work_id"), "work_id", max_len=120)
        work_title_in = _optional_clean_str(payload.get("work_title"), "work_title", max_len=160)
        if not work_id_in and not work_title_in:
            raise V02ValidationError("INVALID_FIELDS", "Provide at least 'work_title' or 'work_id'")
        version_hint = _optional_clean_str(payload.get("version_hint"), "version_hint", max_len=40)
        usage_note = _optional_clean_str(payload.get("usage_note_cn"), "usage_note_cn", max_len=200)

        matched_study: Optional[dict[str, Any]] = None
        for st in state.get("palette_studies") or []:
            if isinstance(st, dict) and st.get("study_id") == study_id_req:
                matched_study = st
                break
        if matched_study is None:
            raise V02NotFoundError("STUDY_NOT_FOUND", f"Palette study not found: {study_id_req!r}")
        if not _is_study_source_live(day_root, matched_study, state):
            raise V02ConflictError(
                "SOURCE_MEDIA_UNAVAILABLE",
                "Cannot apply palette from a deleted or unavailable media source",
            )

        raw_sel = payload.get("selected_hex")
        if raw_sel is None:
            chosen_hex = list(matched_study.get("dominant_hex") or [])[:3]
        else:
            if not isinstance(raw_sel, list) or len(raw_sel) == 0 or len(raw_sel) > 5:
                if isinstance(raw_sel, list):
                    for c in raw_sel:
                        validate_hex_color(c)
                raise V02ValidationError("INVALID_FIELDS", "selected_hex must be a list of 1..5 #RRGGBB colors")
            chosen_hex = [validate_hex_color(c) for c in raw_sel]

        raw_share = payload.get("share_with_room")
        if raw_share is None:
            app_shared = bool(matched_study.get("shared", False))
        else:
            if not isinstance(raw_share, bool):
                raise V02ValidationError("INVALID_FIELDS", "share_with_room must be a boolean")
            app_shared = raw_share

        app_id = _allocate_id(state, "wpal")
        binding = resolve_and_bind_work_for_palette(
            day_root,
            work_id_in=work_id_in,
            work_title_in=work_title_in,
            version_hint=version_hint,
            fallback_app_id=app_id,
            obs_iso=obs_iso,
            project_service=project_service,
        )
        app_record: dict[str, Any] = {
            "application_id": app_id,
            "study_id": study_id_req,
            "work_id": binding["work_id"],
            "work_title": binding["work_title"],
            "version_hint": binding["version_hint"],
            "applied_hex": chosen_hex,
            "usage_note_cn": usage_note,
            "source_available": True,
            "not_lived_event_claim": True,
            "provenance_kind": "photo_palette_material",
            "work_verified": binding["work_verified"],
            "work_binding_source": binding["work_binding_source"],
            "bound_version_id": binding["bound_version_id"],
            "bound_version_number": binding["bound_version_number"],
            "tray_linked": binding["tray_linked"],
            "shared": app_shared,
            "applied_at": obs_iso,
        }
        matched_study.setdefault("applied_works", []).append(copy.deepcopy(app_record))
        state.setdefault("work_palette_applications", []).insert(0, app_record)

    elif action == "check_collection_staleness":
        if actor_side != "subject":
            raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can manage collection staleness")
        item_id_in = _optional_clean_str(payload.get("item_id"), "item_id", max_len=64)
        title_in = _optional_clean_str(payload.get("title"), "title", max_len=160)
        raw_kind = payload.get("source_kind")
        source_kind = _optional_clean_str(raw_kind, "source_kind", max_len=40) or "link"
        if source_kind not in COLLECTION_SOURCE_KINDS:
            raise V02ValidationError("INVALID_FIELDS", f"Unsupported collection source_kind: {source_kind!r}")
        album_id_ref = _optional_clean_str(payload.get("album_id"), "album_id", max_len=120)
        if album_id_ref and not SAFE_ALBUM_ID_RE.match(album_id_ref):
            raise V02ValidationError("INVALID_FIELDS", "Invalid album_id in collection item")
        source_ref = _optional_clean_str(payload.get("source_ref"), "source_ref", max_len=240)
        note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=200)

        raw_alive = payload.get("source_alive")
        if raw_alive is not None and not isinstance(raw_alive, bool):
            raise V02ValidationError("INVALID_FIELDS", "source_alive must be a boolean")

        raw_status = _optional_clean_str(payload.get("status"), "status", max_len=40)
        if raw_status and raw_status not in COLLECTION_STATUSES:
            raise V02ValidationError("INVALID_FIELDS", f"Invalid collection status: {raw_status!r}")

        raw_share = payload.get("share_with_room")
        if raw_share is not None and not isinstance(raw_share, bool):
            raise V02ValidationError("INVALID_FIELDS", "share_with_room must be a boolean")

        disk_alive = True
        if album_id_ref:
            if album_id_ref in set(state.get("invalidated_album_ids") or ()) or resolve_album_media_bytes(day_root, album_id_ref) is None:
                disk_alive = False
        if raw_alive is False:
            disk_alive = False

        eff_status = raw_status or ("fresh" if disk_alive else "stale_source")
        if not disk_alive:
            eff_status = "stale_source"

        items: list[dict[str, Any]] = list(state.get("collection_items") or [])
        if item_id_in:
            found_item: Optional[dict[str, Any]] = None
            for it in items:
                if isinstance(it, dict) and it.get("item_id") == item_id_in:
                    found_item = it
                    break
            if found_item is None:
                raise V02NotFoundError("COLLECTION_ITEM_NOT_FOUND", f"Collection item not found: {item_id_in!r}")
            if title_in:
                found_item["title"] = title_in
            if raw_kind is not None:
                found_item["source_kind"] = source_kind
            if album_id_ref:
                found_item["album_id"] = album_id_ref
            if source_ref:
                found_item["source_ref"] = source_ref
            if payload.get("note_cn") is not None:
                found_item["note_cn"] = note_cn
            if raw_share is not None:
                found_item["shared"] = raw_share
            found_item["status"] = eff_status
            found_item["source_available"] = disk_alive and (eff_status != "stale_source")
            found_item["checked_at"] = obs_iso
        else:
            if not title_in:
                raise V02ValidationError("INVALID_FIELDS", "Field 'title' is required for new collection entry")
            items.insert(0, {
                "item_id": _allocate_id(state, "col"),
                "title": title_in,
                "source_kind": source_kind,
                "album_id": album_id_ref or None,
                "source_ref": source_ref or "",
                "status": eff_status,
                "source_available": disk_alive and (eff_status != "stale_source"),
                "note_cn": note_cn,
                "saved_equals_read": False,
                "saved_equals_permanent_like": False,
                "shared": bool(raw_share) if raw_share is not None else False,
                "checked_at": obs_iso,
            })
        state["collection_items"] = items

    elif action == "curate_shared_collection":
        item_id_in = _optional_clean_str(payload.get("item_id"), "item_id", max_len=64)
        title_in = _optional_clean_str(payload.get("title"), "title", max_len=160)
        raw_kind = payload.get("source_kind")
        source_kind = _optional_clean_str(raw_kind, "source_kind", max_len=40) or "link"
        if source_kind not in COLLECTION_SOURCE_KINDS:
            raise V02ValidationError("INVALID_FIELDS", f"Unsupported collection source_kind: {source_kind!r}")
        source_ref = _optional_clean_str(payload.get("source_ref"), "source_ref", max_len=240)
        note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=200)

        raw_status = _optional_clean_str(payload.get("status"), "status", max_len=40)
        if raw_status and raw_status not in COLLECTION_STATUSES:
            raise V02ValidationError("INVALID_FIELDS", f"Invalid collection status: {raw_status!r}")

        raw_share = payload.get("share_with_room")
        if raw_share is not None and not isinstance(raw_share, bool):
            raise V02ValidationError("INVALID_FIELDS", "share_with_room must be a boolean")

        items = list(state.get("collection_items") or [])
        if item_id_in:
            found_item = None
            for it in items:
                if isinstance(it, dict) and it.get("item_id") == item_id_in:
                    found_item = it
                    break
            if found_item is None:
                raise V02NotFoundError("COLLECTION_ITEM_NOT_FOUND", f"Collection item not found: {item_id_in!r}")
            if actor_side == "partner" and not bool(found_item.get("shared", False)):
                raise V02PermissionError(
                    "FORBIDDEN_ACTOR_SIDE",
                    "Partner cannot access or modify a private collection item",
                )
            if title_in:
                found_item["title"] = title_in
            if raw_kind is not None:
                found_item["source_kind"] = source_kind
            if payload.get("source_ref") is not None:
                found_item["source_ref"] = source_ref
            if payload.get("note_cn") is not None:
                found_item["note_cn"] = note_cn
            if raw_status:
                found_item["status"] = raw_status
            if actor_side == "partner":
                found_item["shared"] = True
            elif raw_share is not None:
                found_item["shared"] = raw_share
            eff_st = str(found_item.get("status") or "fresh")
            aid_existing = found_item.get("album_id")
            if eff_st == "stale_source":
                found_item["source_available"] = False
            elif isinstance(aid_existing, str) and aid_existing:
                found_item["source_available"] = (
                    aid_existing not in set(state.get("invalidated_album_ids") or ())
                    and resolve_album_media_bytes(day_root, aid_existing) is not None
                )
            else:
                found_item["source_available"] = True
            found_item["curated_by"] = actor_side
            found_item["checked_at"] = obs_iso
        else:
            if not title_in:
                raise V02ValidationError("INVALID_FIELDS", "Field 'title' is required for new collection entry")
            eff_status = raw_status or "fresh"
            eff_shared = True if actor_side == "partner" else (bool(raw_share) if raw_share is not None else True)
            items.insert(0, {
                "item_id": _allocate_id(state, "col"),
                "title": title_in,
                "source_kind": source_kind,
                "album_id": None,
                "source_ref": source_ref or "",
                "status": eff_status,
                "source_available": eff_status != "stale_source",
                "note_cn": note_cn,
                "saved_equals_read": False,
                "saved_equals_permanent_like": False,
                "shared": eff_shared,
                "curated_by": actor_side,
                "checked_at": obs_iso,
            })
        state["collection_items"] = items

    elif action == "invalidate_deleted_media_source":
        if actor_side != "subject":
            raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can invalidate media sources")
        study_id_target = _optional_clean_str(payload.get("study_id"), "study_id", max_len=64)
        album_id_target = _optional_clean_str(payload.get("album_id"), "album_id", max_len=120)
        sha_target = _optional_clean_str(payload.get("sha256"), "sha256", max_len=128).lower()
        _reason = _optional_clean_str(payload.get("reason_cn"), "reason_cn", max_len=160)

        if not study_id_target and not album_id_target and not sha_target:
            raise V02ValidationError(
                "INVALID_FIELDS",
                "Provide at least one of 'study_id', 'album_id', or 'sha256'",
            )
        if album_id_target and not SAFE_ALBUM_ID_RE.match(album_id_target):
            raise V02ValidationError("INVALID_FIELDS", "Invalid album_id")

        if album_id_target:
            inv_albums = list(state.get("invalidated_album_ids") or [])
            if album_id_target not in inv_albums:
                inv_albums.append(album_id_target)
            state["invalidated_album_ids"] = inv_albums
        if sha_target:
            inv_shas = list(state.get("invalidated_sha256s") or [])
            if sha_target not in inv_shas:
                inv_shas.append(sha_target)
            state["invalidated_sha256s"] = inv_shas

        invalidated_study_ids: set[str] = set()
        found_explicit_study = False
        for st in state.get("palette_studies") or []:
            if not isinstance(st, dict):
                continue
            sid = str(st.get("study_id") or "")
            if study_id_target and sid == study_id_target:
                found_explicit_study = True
            matched = (
                (bool(study_id_target) and sid == study_id_target)
                or (bool(album_id_target) and st.get("album_id") == album_id_target)
                or (bool(sha_target) and str(st.get("sha256") or "").lower() == sha_target)
            )
            if matched:
                invalidated_study_ids.add(sid)
                st["source_available"] = False
                st["dominant_hex"] = []
                st["warmth_band"] = None
                st["warmth_label_cn"] = ""
                st["brightness_band"] = None
                st["brightness_label_cn"] = ""
                st["sha256"] = ""
                st["mood_summary_cn"] = "原图已删除或不可用，派生配色已扣留。"
                st["invalidated_at"] = obs_iso
                for app in st.get("applied_works") or []:
                    if isinstance(app, dict):
                        app["source_available"] = False
                        app["applied_hex"] = []

        if study_id_target and not found_explicit_study:
            raise V02NotFoundError("STUDY_NOT_FOUND", f"Palette study not found: {study_id_target!r}")

        for app in state.get("work_palette_applications") or []:
            if isinstance(app, dict) and str(app.get("study_id") or "") in invalidated_study_ids:
                app["source_available"] = False
                app["applied_hex"] = []

        if album_id_target:
            for col in state.get("collection_items") or []:
                if isinstance(col, dict) and col.get("album_id") == album_id_target:
                    col["status"] = "stale_source"
                    col["source_available"] = False
                    col["checked_at"] = obs_iso

    elif action == "curate_unshown_box_item":
        if actor_side != "subject":
            raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can curate unshown work box")
        box_id_in = _optional_clean_str(payload.get("box_item_id"), "box_item_id", max_len=120)
        box_items: list[dict[str, Any]] = list(state.get("unshown_work_box") or [])
        existing_box: Optional[dict[str, Any]] = None
        if box_id_in:
            for entry in box_items:
                if isinstance(entry, dict) and entry.get("box_item_id") == box_id_in:
                    existing_box = entry
                    break

        if existing_box is None:
            artifact_id = _require_clean_str(payload.get("artifact_id"), "artifact_id", max_len=160)
            title_cn = _require_clean_str(payload.get("title_cn"), "title_cn", max_len=160)
            version_id = _optional_clean_str(payload.get("version_id"), "version_id", max_len=128)
            note_cn = _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=240)
            raw_cur_state = _require_clean_str(payload.get("curation_state"), "curation_state", max_len=40)
            if raw_cur_state not in BOX_CURATION_STATES:
                raise V02ValidationError("INVALID_FIELDS", f"Invalid curation_state: {raw_cur_state!r}")
            raw_reuse = payload.get("reuse_mode")
            if raw_reuse is None:
                reuse_mode = "none"
            else:
                reuse_mode = _require_clean_str(raw_reuse, "reuse_mode", max_len=40)
                if reuse_mode not in BOX_REUSE_MODES:
                    raise V02ValidationError("INVALID_FIELDS", f"Invalid reuse_mode: {reuse_mode!r}")
            raw_src_del = payload.get("source_deleted", False)
            if raw_src_del is None:
                source_deleted = False
            elif not isinstance(raw_src_del, bool):
                raise V02ValidationError("INVALID_FIELDS", "Field 'source_deleted' must be a boolean")
            else:
                source_deleted = raw_src_del
            box_item_id = box_id_in or _allocate_id(state, "box")
            created_at = obs_iso
        else:
            artifact_id = (
                _require_clean_str(payload["artifact_id"], "artifact_id", max_len=160)
                if "artifact_id" in payload and payload["artifact_id"] is not None
                else str(existing_box.get("artifact_id") or "")
            )
            title_cn = (
                _require_clean_str(payload["title_cn"], "title_cn", max_len=160)
                if "title_cn" in payload and payload["title_cn"] is not None
                else str(existing_box.get("title_cn") or "")
            )
            version_id = (
                _optional_clean_str(payload.get("version_id"), "version_id", max_len=128)
                if "version_id" in payload
                else str(existing_box.get("version_id") or "")
            )
            note_cn = (
                _optional_clean_str(payload.get("note_cn"), "note_cn", max_len=240)
                if "note_cn" in payload
                else str(existing_box.get("note_cn") or "")
            )
            if "curation_state" in payload and payload["curation_state"] is not None:
                raw_cur_state = _require_clean_str(payload["curation_state"], "curation_state", max_len=40)
                if raw_cur_state not in BOX_CURATION_STATES:
                    raise V02ValidationError("INVALID_FIELDS", f"Invalid curation_state: {raw_cur_state!r}")
            else:
                raw_cur_state = str(existing_box.get("curation_state") or "kept")
            if "reuse_mode" in payload:
                raw_reuse = payload.get("reuse_mode")
                if raw_reuse is None:
                    reuse_mode = "none"
                else:
                    reuse_mode = _require_clean_str(raw_reuse, "reuse_mode", max_len=40)
                    if reuse_mode not in BOX_REUSE_MODES:
                        raise V02ValidationError("INVALID_FIELDS", f"Invalid reuse_mode: {reuse_mode!r}")
            else:
                reuse_mode = str(existing_box.get("reuse_mode") or "none")
            if "source_deleted" in payload and payload["source_deleted"] is not None:
                if not isinstance(payload["source_deleted"], bool):
                    raise V02ValidationError("INVALID_FIELDS", "Field 'source_deleted' must be a boolean")
                source_deleted = bool(payload["source_deleted"])
            else:
                source_deleted = bool(existing_box.get("source_deleted", False))
            box_item_id = str(existing_box.get("box_item_id") or box_id_in)
            created_at = existing_box.get("created_at") or obs_iso

        tomb_ids = _read_recall_tombstoned_ids_readonly(day_root)
        inv_arts = {
            str(x).strip()
            for x in (state.get("invalidated_box_artifact_ids") or ())
            if str(x).strip()
        }
        tombstoned = _is_id_in_tombstones(artifact_id, tomb_ids) or _is_id_in_tombstones(box_item_id, tomb_ids)
        is_invalidated = source_deleted or tombstoned or _is_id_in_tombstones(artifact_id, inv_arts)

        eff_curation_state = raw_cur_state
        prev_curation_state: Optional[str] = None
        if is_invalidated:
            candidate_invalidated = True
            selected_eligible = False
            work_intact = False
            if eff_curation_state == "selected_to_show":
                prev_curation_state = "selected_to_show"
                eff_curation_state = "dropped"
        else:
            candidate_invalidated = False
            selected_eligible = True
            work_intact = True

        new_box_doc: dict[str, Any] = {
            "box_item_id": box_item_id,
            "artifact_id": artifact_id,
            "version_id": version_id,
            "title_cn": title_cn,
            "curation_state": eff_curation_state,
            "curation_state_label_cn": BOX_CURATION_STATE_LABELS_CN[eff_curation_state],
            "reuse_mode": reuse_mode,
            "note_cn": note_cn,
            "source_deleted": bool(source_deleted or _is_id_in_tombstones(artifact_id, inv_arts)),
            "candidate_invalidated": candidate_invalidated,
            "selected_to_show_eligible": selected_eligible,
            "work_intact": work_intact,
            "created_at": created_at,
            "updated_at": obs_iso,
        }
        if prev_curation_state is not None:
            new_box_doc["previous_curation_state"] = prev_curation_state

        if existing_box is not None:
            existing_box.clear()
            existing_box.update(new_box_doc)
        else:
            box_items.insert(0, new_box_doc)
        state["unshown_work_box"] = box_items

    elif action == "withdraw_box_show_intent":
        if actor_side != "subject":
            raise V02PermissionError("FORBIDDEN_ACTOR_SIDE", "Only subject can withdraw box show intent")
        box_id_req = _require_clean_str(payload.get("box_item_id"), "box_item_id", max_len=120)
        box_items = list(state.get("unshown_work_box") or [])
        target_box: Optional[dict[str, Any]] = None
        for entry in box_items:
            if isinstance(entry, dict) and entry.get("box_item_id") == box_id_req:
                target_box = entry
                break
        if target_box is None:
            raise V02NotFoundError("BOX_ITEM_NOT_FOUND", f"Unshown box item not found: {box_id_req!r}")

        tomb_ids = _read_recall_tombstoned_ids_readonly(day_root)
        inv_arts = {
            str(x).strip()
            for x in (state.get("invalidated_box_artifact_ids") or ())
            if str(x).strip()
        }
        art_id = str(target_box.get("artifact_id") or "")
        if (
            bool(target_box.get("source_deleted", False))
            or bool(target_box.get("candidate_invalidated", False))
            or _is_id_in_tombstones(art_id, inv_arts)
            or _is_id_in_tombstones(art_id, tomb_ids)
            or _is_id_in_tombstones(box_id_req, tomb_ids)
        ):
            raise V02ConflictError(
                "SOURCE_MEDIA_UNAVAILABLE",
                f"Box item {box_id_req!r} source is deleted or invalidated",
            )

        cur_state = str(target_box.get("curation_state") or "kept")
        if cur_state not in ("selected_to_show", "kept"):
            raise V02ConflictError(
                "SHOW_INTENT_NOT_ACTIVE",
                f"Box item {box_id_req!r} is in state {cur_state!r}, not 'selected_to_show'",
            )
        target_box["curation_state"] = "kept"
        target_box["curation_state_label_cn"] = BOX_CURATION_STATE_LABELS_CN["kept"]
        target_box["work_intact"] = True
        target_box["show_intent_withdrawn_at"] = obs_iso
        target_box["updated_at"] = obs_iso
        state["unshown_work_box"] = box_items

    elif action == "invalidate_deleted_box_source":
        if actor_side != "subject":
            raise V02PermissionError(
                "FORBIDDEN_ACTOR_SIDE", "Only subject can invalidate deleted box sources"
            )
        artifact_target = _require_clean_str(payload.get("artifact_id"), "artifact_id", max_len=160)
        inv_arts_list = list(state.get("invalidated_box_artifact_ids") or [])
        if artifact_target not in inv_arts_list:
            inv_arts_list.append(artifact_target)
        state["invalidated_box_artifact_ids"] = inv_arts_list

        target_set = frozenset({artifact_target})
        for entry in state.get("unshown_work_box") or []:
            if not isinstance(entry, dict):
                continue
            if _is_id_in_tombstones(str(entry.get("artifact_id") or ""), target_set):
                entry["source_deleted"] = True
                entry["candidate_invalidated"] = True
                entry["work_intact"] = False
                entry["selected_to_show_eligible"] = False
                if entry.get("curation_state") == "selected_to_show":
                    entry["previous_curation_state"] = "selected_to_show"
                    entry["curation_state"] = "dropped"
                    entry["curation_state_label_cn"] = BOX_CURATION_STATE_LABELS_CN["dropped"]
                entry["invalidated_at"] = obs_iso
                entry["updated_at"] = obs_iso

    elif action == "ingest_audio_note":
        if "actor_side" in payload:
            raise V02ValidationError("INVALID_FIELDS", "Forbidden key: 'actor_side'")
        title_cn = _require_clean_str(payload.get("title_cn"), "title_cn", max_len=80)
        raw_dur = payload.get("duration_seconds")
        if isinstance(raw_dur, bool) or not isinstance(raw_dur, (int, float)):
            raise V02ValidationError("INVALID_FIELDS", "Field 'duration_seconds' must be a number in 0.5..600")
        dur_float = float(raw_dur)
        if not math.isfinite(dur_float) or dur_float < 0.5 or dur_float > 600.0:
            raise V02ValidationError("INVALID_FIELDS", "Field 'duration_seconds' must be in range 0.5..600")
        duration_val = round(dur_float, 2)

        audio_format = _require_clean_str(payload.get("audio_format"), "audio_format", max_len=32).lower()
        if audio_format not in AUDIO_FORMATS:
            raise V02ValidationError("INVALID_FIELDS", f"Unsupported audio_format: {audio_format!r}")

        raw_source_kind = payload.get("source_kind")
        if raw_source_kind is None or (isinstance(raw_source_kind, str) and not raw_source_kind.strip()):
            source_kind = "local_file"
        else:
            source_kind = _require_clean_str(raw_source_kind, "source_kind", max_len=32).lower()
            if source_kind not in AUDIO_SOURCE_KINDS:
                raise V02ValidationError("INVALID_FIELDS", f"Unsupported audio source_kind: {source_kind!r}")

        raw_sha = _optional_clean_str(payload.get("sha256_hex"), "sha256_hex", max_len=64)
        if raw_sha:
            if not AUDIO_SHA256_RE.match(raw_sha):
                raise V02ValidationError("INVALID_FIELDS", "Field 'sha256_hex' must be a 64-char hex digest")
            sha256_hex = raw_sha.lower()
        else:
            sha256_hex = hashlib.sha256(f"{title_cn}|{obs_iso}".encode("utf-8")).hexdigest()

        transcript_cn = _optional_clean_str(payload.get("transcript_cn"), "transcript_cn", max_len=240)
        raw_tstatus = _optional_clean_str(payload.get("transcript_status"), "transcript_status", max_len=40)
        if raw_tstatus and raw_tstatus not in AUDIO_TRANSCRIPT_STATUSES:
            raise V02ValidationError("INVALID_FIELDS", f"Unsupported transcript_status: {raw_tstatus!r}")
        transcript_status = (
            AUDIO_TRANSCRIPT_STATUS_USER_SUPPLIED
            if transcript_cn
            else AUDIO_TRANSCRIPT_STATUS_UNAVAILABLE
        )

        ambient_tag_cn = _optional_clean_str(payload.get("ambient_tag_cn"), "ambient_tag_cn", max_len=40)

        raw_shared = payload.get("shared")
        if raw_shared is None:
            shared_flag = True if actor_side == "partner" else False
        elif not isinstance(raw_shared, bool):
            raise V02ValidationError("INVALID_FIELDS", "Field 'shared' must be a boolean")
        else:
            shared_flag = raw_shared

        audio_note_id = _allocate_id(state, "aud")
        note_doc: dict[str, Any] = {
            "audio_note_id": audio_note_id,
            "title_cn": title_cn,
            "duration_seconds": duration_val,
            "audio_format": audio_format,
            "source_kind": source_kind,
            "sha256_hex": sha256_hex,
            "transcript_cn": transcript_cn,
            "transcript_status": transcript_status,
            "ambient_tag_cn": ambient_tag_cn,
            "author_side": actor_side,
            "shared": shared_flag,
            "not_lived_event_claim": True,
            "created_at": obs_iso,
        }
        notes = list(state.get("audio_notes") or [])
        notes.insert(0, note_doc)
        state["audio_notes"] = notes[:AUDIO_NOTE_MAX_ENTRIES]
        action_receipt = {
            "audio_note_id": audio_note_id,
            "title_cn": title_cn,
            "duration_seconds": duration_val,
            "audio_format": audio_format,
            "source_kind": source_kind,
            "transcript_status": transcript_status,
            "sha256_hex": sha256_hex,
            "author_side": actor_side,
            "shared": shared_flag,
            "created_at": obs_iso,
        }

    else:
        raise V02ValidationError("INVALID_ACTION", f"Unsupported action: {action!r}")

    state["revision"] = int(state.get("revision") or 0) + 1
    state["updated_at"] = obs_iso
    if op_key:
        ledger_rec: dict[str, Any] = {
            "fingerprint": fp,
            "action": action,
            "actor_side": actor_side,
            "applied_at": obs_iso,
            "revision": state["revision"],
        }
        if action_receipt is not None:
            ledger_rec["action_receipt"] = copy.deepcopy(action_receipt)
        ledger[op_key] = ledger_rec
        # 控制幂等账本上限避免无界增长
        if len(ledger) > 256:
            oldest_keys = list(ledger.keys())[: len(ledger) - 256]
            for ok in oldest_keys:
                ledger.pop(ok, None)

    _atomic_write_state(day_root, state)
    projected = project_v02_crossmodal(
        day_root,
        has_subject_capability=has_cap,
        now_iso=obs_iso,
    )
    if action_receipt is not None:
        projected["action_receipt"] = copy.deepcopy(action_receipt)
    return projected, "APPLIED"


def summarize_crossmodal_state_for_context(
    day_root: Path | str,
    *,
    include_private: bool = True,
    now_iso: Optional[str] = None,
) -> dict[str, Any]:
    """100% 零磁盘写入的跨模态生活背景摘要（供聊天上下文与候选桥接消费）。"""
    projected = project_v02_crossmodal(
        day_root,
        has_subject_capability=bool(include_private),
        now_iso=now_iso,
    )
    pad = projected["color_trial_pad"]
    has_active_trial = bool(pad.get("active")) and bool(pad.get("combined_swatches"))

    recent_palettes: list[dict[str, Any]] = []
    for st in projected["palette_studies"]:
        if not st.get("source_available"):
            continue
        is_shared = bool(st.get("shared", False))
        recent_palettes.append({
            "study_id": st["study_id"],
            "title": st["title"],
            "dominant_hex": list(st["dominant_hex"]),
            "warmth_label_cn": st["warmth_label_cn"],
            "brightness_label_cn": st["brightness_label_cn"],
            "mood_summary_cn": st["mood_summary_cn"],
            "shared": is_shared,
            "privacy_tag": "" if is_shared else "[未向伴侣公开·勿顺嘴说漏]",
            "not_lived_event_claim": True,
        })
        if len(recent_palettes) >= 3:
            break

    open_uncertainties: list[dict[str, Any]] = []
    for mk in projected["reading_uncertainty_marks"]:
        if mk.get("state") == "clarified":
            continue
        is_shared = bool(mk.get("shared", False))
        open_uncertainties.append({
            "mark_id": mk["mark_id"],
            "book_title": mk["book_title"],
            "page": mk["page"],
            "passage_hint": mk["passage_hint"],
            "state": mk["state"],
            "state_label_cn": mk["state_label_cn"],
            "shared": is_shared,
            "privacy_tag": "" if is_shared else "[未向伴侣公开·勿顺嘴说漏]",
        })
        if len(open_uncertainties) >= 3:
            break

    stale_count = int(projected["collection_health"]["counts_by_status"].get("stale_source", 0))

    hint_parts: list[str] = []
    if has_active_trial:
        sw_str = "/".join(pad["combined_swatches"][:4])
        hint_parts.append(f"试色窗开着({sw_str}·零积分随意试)")
    if recent_palettes:
        top_p = recent_palettes[0]
        p_tag = top_p["privacy_tag"]
        hx0 = top_p["dominant_hex"][0] if top_p["dominant_hex"] else ""
        hint_parts.append(f"近期读图配色《{top_p['title']}》({top_p['warmth_label_cn']}·{hx0}·非亲历){p_tag}")
    if open_uncertainties:
        top_m = open_uncertainties[0]
        m_tag = top_m["privacy_tag"]
        hint_parts.append(f"未懂书签《{top_m['book_title']}》P{top_m['page']}({top_m['state_label_cn']}){m_tag}")
    if stale_count > 0:
        hint_parts.append(f"收藏失效源{stale_count}条")

    context_hint_cn = "；".join(hint_parts)[:220]

    return {
        "schema": VIEW_SCHEMA,
        "state_exists": projected["state_exists"],
        "has_active_trial_pad": has_active_trial,
        "trial_swatches": list(pad.get("combined_swatches") or []),
        "trial_pad": {
            "active": has_active_trial,
            "partner_swatches": list(pad.get("partner_swatches") or []),
            "subject_swatches": list(pad.get("subject_swatches") or []),
            "partner_note_cn": str(pad.get("partner_note_cn") or ""),
            "subject_note_cn": str(pad.get("subject_note_cn") or ""),
            "zero_score_pressure": True,
            "not_permanent_preference": True,
        },
        "recent_palettes": recent_palettes,
        "open_reading_uncertainties": open_uncertainties,
        "stale_collection_count": stale_count,
        "unshown_work_box_visible": list(projected["unshown_work_box"]["visible_items"]),
        "context_hint_cn": context_hint_cn,
        "llm_calls": 0,
    }


__all__ = [
    "AUDIO_FORMATS",
    "AUDIO_NOTE_MAX_ENTRIES",
    "AUDIO_SOURCE_KINDS",
    "AUDIO_TRANSCRIPT_STATUSES",
    "AUDIO_TRANSCRIPT_STATUS_UNAVAILABLE",
    "AUDIO_TRANSCRIPT_STATUS_USER_SUPPLIED",
    "BOX_CURATION_STATES",
    "BOX_CURATION_STATE_LABELS_CN",
    "BOX_REUSE_MODES",
    "BRIGHTNESS_LABELS_CN",
    "COLLECTION_SOURCE_KINDS",
    "COLLECTION_STATUSES",
    "COLLECTION_STATUS_LABELS_CN",
    "FORBIDDEN_FOREIGN_AND_SCORE_KEYS",
    "MAX_IMAGE_BYTES",
    "MAX_IMAGE_PIXELS",
    "PNG_SIGNATURE",
    "READING_STATE_LABELS_CN",
    "READING_UNCERTAINTY_STATES",
    "STATE_REL_PATH",
    "V02ConflictError",
    "V02CrossmodalError",
    "V02NotFoundError",
    "V02PermissionError",
    "V02ValidationError",
    "VIEW_SCHEMA",
    "WARMTH_LABELS_CN",
    "apply_crossmodal_mutation",
    "decode_image_pixels",
    "decode_image_pixels_stdlib",
    "extract_real_image_palette",
    "project_v02_crossmodal",
    "resolve_album_media_bytes",
    "strip_and_decode_image_base64",
    "summarize_crossmodal_state_for_context",
    "validate_hex_color",
]
