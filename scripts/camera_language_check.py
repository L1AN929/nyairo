#!/usr/bin/env python3
"""拍照收口：用同一组实际参数，只把提示词换成英文，生成一次对照图。

边界（与 ``components/phone/camera.py`` 相同）：

* 只连本机 loopback 的既有 Stable Diffusion WebUI（``http://127.0.0.1:7860``）；
* 一次显式调用只生成一张，**不排队**：发请求前读一次 ``/sdapi/v1/progress``，
  ``job_count`` 非 0 就以 ``CAMERA_BUSY`` 具名停下，不插队、不等别人作业；
* 不改模型 / options / override_settings / 监听 / 插件 / 水印参数，不下载、不装包。

同条件来自上一张演示图自己写的 PNG ``parameters`` 块（seed、采样器、步数、CFG、尺寸、
模型、Clip skip）。若当前运行时模型与那张图不一致，脚本直接停下——不换模型伪称同条件。

脚本只用项目内路径；输出写到 ``.agents/worker-runs/``，不动原图、不写相册 owner。

运行（Windows / 直达 loopback）：

    python -B scripts/camera_language_check.py
"""
from __future__ import annotations

import base64
import json
import re
import struct
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components"
if str(COMPONENTS) not in sys.path:
    sys.path.insert(0, str(COMPONENTS))

from phone.camera import (  # noqa: E402  (components/phone 在 components 下)
    CameraRefused,
    GENERATION_PARAMS,
    LocalPictureLab,
)

WORKER_RUNS = ROOT / ".agents" / "worker-runs"
BASE_PHOTO = WORKER_RUNS / "phone-camera-demo.png"
OUT_PHOTO = WORKER_RUNS / "phone-camera-english.png"
RESULT_JSON = WORKER_RUNS / "camera-language-result.json"

REFERENCE_BASE_URL = "http://127.0.0.1:7860"

#: 与上一张中文提示词语义等价：雨后窗台、白瓷杯、热气、灰蓝色天空。
ENGLISH_PROMPT = ("After the rain, a windowsill, a white porcelain cup with a little steam "
                  "rising from it, and a grey-blue sky outside the window.")

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _png_parameters(path: Path) -> str:
    """Return the PNG ``parameters`` text chunk (what the WebUI wrote), or ``""``."""

    data = path.read_bytes()
    if not data.startswith(PNG_SIGNATURE):
        return ""
    offset = 8
    while offset + 12 <= len(data):
        length = struct.unpack(">I", data[offset:offset + 4])[0]
        chunk_type = data[offset + 4:offset + 8]
        body = data[offset + 8:offset + 8 + length]
        if chunk_type == b"tEXt":
            key, _, value = body.partition(b"\x00")
            if key == b"parameters":
                return value.decode("utf-8", "replace")
        elif chunk_type == b"iTXt":
            parts = body.split(b"\x00", 5)
            if len(parts) == 6 and parts[0] == b"parameters":
                return parts[5].decode("utf-8", "replace")
        offset += 12 + length
    return ""


def _field(text: str, name: str) -> str:
    # WebUI 把提示词和参数写在同一块里：参数可能紧跟逗号，也可能另起一行。
    match = re.search(r"(?:^|[,，]|[\r\n])\s*%s:\s*([^,\r\n]+)" % re.escape(name), text)
    return match.group(1).strip() if match else ""


def _conditions(text: str) -> dict[str, object]:
    size = _field(text, "Size")
    width, _, height = size.partition("x")
    return {
        "seed": int(_field(text, "Seed") or 0),
        "steps": int(_field(text, "Steps") or 0),
        "sampler": _field(text, "Sampler"),
        "cfg_scale": float(_field(text, "CFG scale") or 0),
        "width": int(width or 0),
        "height": int(height or 0),
        "model": _field(text, "Model"),
        "model_hash": _field(text, "Model hash"),
        "clip_skip": int(_field(text, "Clip skip") or 0),
    }


def _stop(reason: str, detail: str = "") -> int:
    RESULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULT_JSON.write_text(json.dumps({
        "status": "STOPPED", "reason_code": reason, "detail": detail[:300],
        "generated": False, "photo": None, "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print("status=STOPPED")
    print("reason=%s" % reason)
    if detail:
        print("detail=%s" % detail[:200])
    print("generated=no")
    return 1


def main() -> int:
    if not BASE_PHOTO.is_file():
        return _stop("REFERENCE_PHOTO_MISSING", str(BASE_PHOTO))

    reference = _conditions(_png_parameters(BASE_PHOTO))
    missing = [name for name in ("seed", "steps", "sampler", "width", "height", "model")
               if not reference.get(name)]
    if missing:
        return _stop("REFERENCE_PARAMS_INCOMPLETE", ",".join(missing))

    lab = LocalPictureLab(REFERENCE_BASE_URL)

    # Same model as the reference picture?  Never swap the runtime's model to fake "same".
    options = lab._request("GET", "/sdapi/v1/options", timeout=15.0)  # noqa: SLF001
    current_model = str(options.get("sd_model_checkpoint") or "")
    if str(reference["model_hash"]) not in current_model \
            or str(reference["model"]) not in current_model:
        return _stop("MODEL_CHANGED",
                     "reference=%s; current=%s" % (reference["model"], current_model[:120]))
    current_clip = int(options.get("CLIP_stop_at_last_layers") or 0)
    if int(reference["clip_skip"]) and current_clip != int(reference["clip_skip"]):
        return _stop("CLIP_SKIP_CHANGED",
                     "reference=%s; current=%s" % (reference["clip_skip"], current_clip))

    # Never queue behind somebody else's GPU job: one check, refuse if busy.
    progress = lab._request("GET", "/sdapi/v1/progress", timeout=10.0)  # noqa: SLF001
    state = progress.get("state") if isinstance(progress.get("state"), dict) else {}
    job_count = state.get("job_count")
    if not isinstance(job_count, int) or isinstance(job_count, bool):
        return _stop("PROGRESS_INVALID", "job_count is missing")
    if job_count != 0:
        return _stop("CAMERA_BUSY", "another generation is already running")

    payload = dict(GENERATION_PARAMS)
    payload.update({
        "prompt": ENGLISH_PROMPT,
        "negative_prompt": "",
        "seed": int(reference["seed"]),
        "steps": int(reference["steps"]),
        "sampler_name": str(reference["sampler"]),
        "cfg_scale": float(reference["cfg_scale"]),
        "width": int(reference["width"]),
        "height": int(reference["height"]),
    })

    started = time.time()
    try:
        answer = lab._request("POST", "/sdapi/v1/txt2img", payload)  # noqa: SLF001
    except CameraRefused as exc:
        return _stop(exc.reason_code, exc.detail)

    images = answer.get("images")
    if not isinstance(images, list) or not images or not isinstance(images[0], str) or not images[0]:
        return _stop("CAMERA_EMPTY_ANSWER", "the runtime returned no picture")
    try:
        data = base64.b64decode(images[0].encode("ascii"), validate=True)
    except Exception:
        return _stop("CAMERA_ANSWER_INVALID", "picture is not strict base64")
    if not data.startswith(PNG_SIGNATURE):
        return _stop("CAMERA_UNSUPPORTED_IMAGE", "the runtime returned non-PNG bytes")

    OUT_PHOTO.write_bytes(data)
    actual = _conditions(_png_parameters(OUT_PHOTO))
    same = {name: (actual.get(name) == reference.get(name))
            for name in ("seed", "steps", "sampler", "cfg_scale", "width", "height",
                         "model", "clip_skip")}

    RESULT_JSON.write_text(json.dumps({
        "status": "OK",
        "generated": True,
        "sent_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(started)),
        "elapsed_seconds": round(time.time() - started, 3),
        "prompt_language": "en",
        "prompt_chars": len(ENGLISH_PROMPT),
        "job_count_before": job_count,
        "reference": reference,
        "actual": actual,
        "same_conditions": same,
        "photo": str(OUT_PHOTO.relative_to(ROOT)),
        "photo_bytes": len(data),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    print("status=OK")
    print("photo=%s" % OUT_PHOTO)
    print("photo_bytes=%d" % len(data))
    print("sent_seed=%d steps=%d sampler=%s cfg=%s size=%dx%d model=%s clip_skip=%d"
          % (reference["seed"], reference["steps"], reference["sampler"],
             reference["cfg_scale"], reference["width"], reference["height"],
             reference["model"], reference["clip_skip"]))
    print("same_conditions=%s" % ("yes" if all(same.values()) else
                                  "no:" + ",".join(k for k, ok in same.items() if not ok)))
    print("prompt_language=en chars=%d" % len(ENGLISH_PROMPT))
    return 0 if all(same.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
