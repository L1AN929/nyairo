#!/usr/bin/env python3
"""一次本机免费的中文画面 → 英文标签转换演示：**不出图**。

只做一件事：把一句**合成的**中文画面描述交给手机拍照已经在用的
``SceneTranslator``（默认本机 ``http://127.0.0.1:18190`` 的既有 llama.cpp 服务），
打印转换后的英文标签，让人核对语义有没有丢。

边界：

* 不调用产品认知网关，不调 Stable Diffusion，不生成、不保存任何图片；
* 不使用她的日记/私人正文，不读 ``novel-rewrite`` 的历史请求或文稿；
* 不换模型、不装依赖、不联网、不排队；服务正忙就直接具名拒绝；
* 转换失败就如实退出，不把中文原样当英文提示词。

运行（一次真实本机转换）：

    python -B scripts/camera_translation_demo.py

父级复读上次结果（不再调用模型）：

    python -B scripts/camera_translation_demo.py --reuse-last
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENTS = ROOT / "components"
if str(COMPONENTS) not in sys.path:
    sys.path.insert(0, str(COMPONENTS))

from phone.camera import CameraRefused, SceneTranslator  # noqa: E402

#: Synthetic only -- never her diary or any private text.
SCENE = "雨后的窗台，一只白瓷杯，杯口有一点热气，窗外是灰蓝色的天。"

RESULT_JSON = ROOT / ".agents" / "worker-runs" / "camera-translation-demo.json"


def _print_result(payload: dict[str, object], *, reuse: bool) -> int:
    prompt = str(payload.get("generation_prompt") or "")
    translated = bool(payload.get("translated"))
    ascii_only = bool(prompt) and prompt.isascii()
    ok = translated and ascii_only
    print("reuse=%s" % ("yes" if reuse else "no"))
    print("scene=%s" % payload.get("scene"))
    print("prompt=%s" % prompt)
    print("translated=%s" % ("yes" if translated else "no"))
    print("translator_source=%s" % payload.get("source"))
    print("prompt_is_ascii=%s" % ("yes" if ascii_only else "no"))
    print("prompt_chars=%d" % len(prompt))
    print("image_generated=no")
    print("result=%s" % ("OK" if ok else "FAIL"))
    return 0 if ok else 1


def _print_refusal(payload: dict[str, object]) -> int:
    print("reuse=no" if not payload.get("reused_from_file") else "reuse=yes")
    print("scene=%s" % payload.get("scene"))
    print("result=REFUSED")
    print("reason_code=%s" % payload.get("reason_code"))
    print("detail=%s" % payload.get("detail"))
    print("image_generated=no")
    return 1


def _reuse_last() -> int:
    try:
        payload = json.loads(RESULT_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print("reuse=NO_RESULT")
        return 2
    if not isinstance(payload, dict):
        print("reuse=BAD_RESULT")
        return 2
    if payload.get("refused"):
        return _print_refusal(dict(payload, reused_from_file=True))
    return _print_result(payload, reuse=True)


def _store(payload: dict[str, object]) -> None:
    RESULT_JSON.parent.mkdir(parents=True, exist_ok=True)
    RESULT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                           encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="phone camera: one local translation demo")
    parser.add_argument("--reuse-last", action="store_true",
                        help="print the stored last result without calling the model again")
    args = parser.parse_args()
    if args.reuse_last:
        return _reuse_last()

    try:
        result = SceneTranslator().prompt_for(SCENE)
    except CameraRefused as exc:
        payload = {
            "scene": SCENE,
            "refused": True,
            "reason_code": exc.reason_code,
            "detail": exc.detail,
            "at": datetime.now().isoformat(timespec="seconds"),
            "image_generated": False,
        }
        _store(payload)
        print("result=REFUSED")
        print("reason_code=%s" % exc.reason_code)
        print("detail=%s" % exc.detail)
        print("image_generated=no")
        print("stored=%s" % RESULT_JSON)
        return 1

    payload = {
        "scene": SCENE,
        "generation_prompt": result["prompt"],
        "translated": bool(result["translated"]),
        "source": result["source"],
        "at": datetime.now().isoformat(timespec="seconds"),
        "image_generated": False,
    }
    _store(payload)
    rc = _print_result(payload, reuse=False)
    print("stored=%s" % RESULT_JSON)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
