"""Local picture generation for the phone camera (one narrow adapter, no second owner).

The phone's "camera" does not talk to a real camera: it asks a **local** Stable Diffusion WebUI
runtime (default ``http://127.0.0.1:7860``) for one small picture from a scene description she
wrote.  The bytes then go through the existing private-album import, so storage, versions,
grants and lifecycle stay with the Supply ``ManagedArtifact`` owner -- this module never writes
an album file or a database row itself.

Two hard boundaries:

* only loopback HTTP, never an external API, a redirect, a download or an installed dependency;
* one generation per explicit call.  Nothing here queues behind somebody else's GPU job: the
  caller checks once and refuses with ``CAMERA_BUSY`` instead of waiting or stealing the GPU.

Why a Windows-bridge path exists: this repository runs in WSL, while the SD WebUI runtime on
this machine listens on Windows ``127.0.0.1`` only (WSL's own loopback cannot reach it).  WSL's
own Windows interop is already available, so the *same* HTTP request is executed by a tiny
transport bridge under Windows Python.  The bridge carries bytes only -- it contains no prompt,
no storage and no policy.  On a host where loopback already works, the direct path is used and
the bridge is never started.

One more layer exists because the picture checkpoint understands tag-style English better than
Chinese prose: :class:`SceneTranslator` asks the machine's **already running** local llama.cpp
server (default ``http://127.0.0.1:18190``) for a tag-style English rendering of her Chinese
scene.  It is a technical converter, not her cognition: it never chooses what to photograph,
never rewrites her caption, and the original Chinese scene plus the exact English prompt are
both kept in the phone's capture index.  No external API, no download, no model swap, no queue:
a busy or unreachable translator is a named refusal and no picture is generated.
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit

#: The one local runtime this adapter speaks to.  The environment may point at another loopback
#: port; a non-loopback or non-HTTP endpoint is refused before any request leaves the process.
DEFAULT_BASE_URL = "http://127.0.0.1:7860"
ALLOWED_HOSTS = frozenset({"127.0.0.1", "localhost"})

#: The machine's *existing* local text runtime, used only to turn her Chinese scene into tags.
DEFAULT_TRANSLATOR_BASE_URL = "http://127.0.0.1:18190"
TRANSLATOR_CHAT_PATH = "/v1/chat/completions"
TRANSLATOR_SLOTS_PATH = "/slots"
TRANSLATOR_TIMEOUT = 90.0
TRANSLATOR_MAX_TOKENS = 160

#: Owner bounds: the album accepts at most 5 MiB and at most 200 caption characters.
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_CAPTION_CHARS = 200
MAX_SCENE_CHARS = 800
#: The converted prompt must stay short enough for a 512px checkpoint to actually honour it.
MAX_PROMPT_CHARS = 400

#: The smallest reasonable request: one 512px picture, few steps, no hires, no plugins, and no
#: ``override_settings`` (which would let this adapter swap the runtime's model behind its back).
GENERATION_PARAMS: dict[str, Any] = {
    "width": 512,
    "height": 512,
    "steps": 12,
    "cfg_scale": 5.0,
    "sampler_name": "Euler a",
    "batch_size": 1,
    "n_iter": 1,
    "seed": -1,
    "do_not_save_samples": True,
    "do_not_save_grid": True,
}
DEFAULT_TIMEOUT = 300.0
PROBE_TIMEOUT = 10.0
MAX_ANSWER_BYTES = 8 * 1024 * 1024

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


class CameraRefused(RuntimeError):
    """A picture this adapter will not ask for (or cannot accept), with a stable reason code."""

    def __init__(self, reason_code: str, detail: str = "") -> None:
        super().__init__(("%s:%s" % (reason_code, detail))[:300])
        self.reason_code = str(reason_code)
        self.detail = str(detail)[:200]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every redirect: a 3xx is never followed to another host or scheme."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[override]
        raise CameraRefused("CAMERA_REDIRECT_REFUSED", str(code))


def _loopback_base_url(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        raise CameraRefused("CAMERA_URL_MISSING")
    parts = urlsplit(text)
    if parts.scheme != "http" or (parts.hostname or "").lower() not in ALLOWED_HOSTS:
        raise CameraRefused("CAMERA_URL_NOT_LOOPBACK", "only http loopback is allowed")
    if not parts.port:
        raise CameraRefused("CAMERA_URL_INVALID", "an explicit port is required")
    if parts.path not in ("", "/") or parts.query or parts.fragment:
        raise CameraRefused("CAMERA_URL_INVALID", "the base URL cannot carry a path or query")
    return "http://%s:%d" % ((parts.hostname or "").lower(), int(parts.port))


def _windows_path(path: Path) -> Optional[str]:
    """The Windows spelling of a WSL path, or ``None`` when this is not a ``/mnt/<drive>`` path."""

    match = re.match(r"^/mnt/([A-Za-z])/(.*)$", str(path))
    if not match:
        return None
    return "%s:\\%s" % (match.group(1).upper(), match.group(2).replace("/", "\\"))


def _is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8").lower()
    except OSError:
        return False


def _detect_content_type(data: bytes) -> Optional[str]:
    if data.startswith(PNG_SIGNATURE):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    return None


def _bridge_python_for(env: Mapping[str, str]) -> Optional[str]:
    """The Windows Python used for the transport bridge, or ``None`` off WSL."""

    explicit = str(env.get("PHONE_CAMERA_BRIDGE_PYTHON") or "").strip()
    if explicit:
        return explicit
    if not _is_wsl():
        return None
    return shutil.which("python.exe") or shutil.which("py.exe")


def _direct_request(url: str, method: str, data: Optional[bytes],
                    timeout: float) -> Optional[Any]:
    """One request from this process.  ``None`` means *unreachable*, not *failed*.

    The parsed answer may be an object (SD WebUI, chat completions) or an array (llama.cpp
    ``/slots``); the caller decides which shape it accepts.
    """

    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            raw = response.read(MAX_ANSWER_BYTES + 1)
    except CameraRefused:
        raise
    except urllib.error.HTTPError as exc:
        # The runtime answered, so it is reachable: an error status is not a reason to try a
        # second transport (and never a reason to retry the generation).
        raise CameraRefused("CAMERA_HTTP_ERROR", "status=%d" % int(exc.code)) from exc
    except (urllib.error.URLError, TimeoutError, OSError, ConnectionError):
        return None
    if status != 200:
        raise CameraRefused("CAMERA_HTTP_ERROR", "status=%d" % status)
    if len(raw) > MAX_ANSWER_BYTES:
        raise CameraRefused("CAMERA_ANSWER_TOO_LARGE")
    try:
        answer = json.loads(raw.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise CameraRefused("CAMERA_ANSWER_INVALID", "runtime answer is not JSON") from exc
    if not isinstance(answer, (dict, list)):
        raise CameraRefused("CAMERA_ANSWER_INVALID")
    return answer


def _bridge_request(url: str, method: str, payload: Optional[Mapping[str, Any]],
                    timeout: float, python_exe: Optional[str]) -> Any:
    """The same request executed by the Windows-side bridge (bytes only)."""

    if not python_exe:
        raise CameraRefused("CAMERA_UNREACHABLE",
                            "loopback runtime is not reachable from this process")
    script = Path(__file__).resolve().parents[2] / "scripts" / "phone_camera_win_bridge.py"
    win_script = _windows_path(script)
    if win_script is None or not script.is_file():
        raise CameraRefused("CAMERA_BRIDGE_UNAVAILABLE", "bridge script is not on a drive path")
    request = {"url": url, "method": method, "body": dict(payload or {}),
               "timeout": min(timeout, 300.0)}
    try:
        finished = subprocess.run(
            [python_exe, "-B", win_script],
            input=json.dumps(request, ensure_ascii=False).encode("utf-8"),
            capture_output=True, timeout=timeout + 15.0)
    except (OSError, subprocess.SubprocessError) as exc:
        raise CameraRefused("CAMERA_BRIDGE_FAILED", type(exc).__name__) from exc
    if finished.returncode != 0:
        raise CameraRefused("CAMERA_BRIDGE_FAILED", "exit=%d" % finished.returncode)
    try:
        answer = json.loads(finished.stdout.decode("utf-8"))
    except (UnicodeError, ValueError) as exc:
        raise CameraRefused("CAMERA_ANSWER_INVALID", "bridge answer is not JSON") from exc
    if not isinstance(answer, dict):
        raise CameraRefused("CAMERA_ANSWER_INVALID")
    if answer.get("error"):
        raise CameraRefused("CAMERA_HTTP_ERROR", str(answer["error"])[:120])
    status = answer.get("status")
    if status != 200:
        raise CameraRefused("CAMERA_HTTP_ERROR", "status=%r" % (status,))
    body = answer.get("body")
    if not isinstance(body, (dict, list)):
        raise CameraRefused("CAMERA_ANSWER_INVALID")
    return body


def _request_json(base_url: str, method: str, path: str,
                  payload: Optional[Mapping[str, Any]], timeout: float,
                  python_exe: Optional[str]) -> Any:
    url = base_url + path
    data = None if payload is None else json.dumps(
        dict(payload), ensure_ascii=False).encode("utf-8")
    body = _direct_request(url, method, data, timeout)
    if body is not None:
        return body
    return _bridge_request(url, method, payload, timeout, python_exe)


class LocalPictureLab:
    """One explicit picture request against a loopback Stable Diffusion WebUI runtime."""

    def __init__(self, base_url: Any = None, *, environment: Optional[Mapping[str, str]] = None,
                 timeout: float = DEFAULT_TIMEOUT) -> None:
        env = dict(os.environ if environment is None else environment)
        self.base_url = _loopback_base_url(env.get("PHONE_CAMERA_BASE_URL") or base_url
                                           or DEFAULT_BASE_URL)
        self.timeout = float(timeout)
        self._bridge_python = _bridge_python_for(env)

    # -- transport -----------------------------------------------------------------------

    def _request(self, method: str, path: str, payload: Optional[Mapping[str, Any]] = None,
                 *, timeout: Optional[float] = None) -> Any:
        return _request_json(self.base_url, method, path, payload,
                             float(self.timeout if timeout is None else timeout),
                             self._bridge_python)

    # -- one picture ---------------------------------------------------------------------

    def generate(self, scene: Any) -> tuple[bytes, str]:
        """One picture for one explicit scene.  Refuses busy/unknown runtimes, never queues."""

        text = scene if isinstance(scene, str) else ""
        text = text.strip()
        if not text or len(text) > MAX_SCENE_CHARS or "\x00" in text:
            raise CameraRefused("CAMERA_SCENE_INVALID",
                                "scene must be 1..%d characters" % MAX_SCENE_CHARS)

        progress = self._request("GET", "/sdapi/v1/progress", timeout=PROBE_TIMEOUT)
        state = progress.get("state")
        job_count = state.get("job_count") if isinstance(state, dict) else None
        if isinstance(job_count, bool) or not isinstance(job_count, int):
            raise CameraRefused("CAMERA_PROGRESS_INVALID", "job_count is missing")
        if job_count != 0:
            raise CameraRefused("CAMERA_BUSY", "another generation is already running")

        payload = dict(GENERATION_PARAMS)
        payload["prompt"] = text
        payload["negative_prompt"] = ""
        answer = self._request("POST", "/sdapi/v1/txt2img", payload)
        images = answer.get("images")
        if not isinstance(images, list) or not images or not isinstance(images[0], str) \
                or not images[0]:
            raise CameraRefused("CAMERA_EMPTY_ANSWER", "the runtime returned no picture")
        try:
            data = base64.b64decode(images[0].encode("ascii"), validate=True)
        except Exception as exc:
            raise CameraRefused("CAMERA_ANSWER_INVALID", "picture is not strict base64") from exc
        if not data or len(data) > MAX_IMAGE_BYTES:
            raise CameraRefused("CAMERA_IMAGE_SIZE_INVALID", "picture is empty or too large")
        content_type = _detect_content_type(data)
        if content_type is None:
            raise CameraRefused("CAMERA_UNSUPPORTED_IMAGE", "only PNG/JPEG pictures are accepted")
        return data, content_type


#: The converter's whole job: her Chinese scene -> tags the checkpoint can actually follow.
#: It may not invent objects, styles, artists or watermarks, and may not answer in Chinese.
TRANSLATOR_SYSTEM_PROMPT = (
    "You translate a Chinese scene description into English comma-separated Stable Diffusion "
    "tags. Output tags only: no sentence, no explanation, no quotes, no labels. Keep every "
    "concrete object, its count, colours, materials, positions and weather. Translate only "
    "what the input states; never invent objects, reflections, people or decorations. Never "
    "emit quality, resolution, style, artist, watermark or composition tags. Output one line "
    "of concise tags and stop. Answer in English only."
)


def _has_cjk(text: str) -> bool:
    """True when the text carries CJK characters or CJK/fullwidth punctuation."""

    for ch in text:
        code = ord(ch)
        if (0x2E80 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF
                or 0xFE30 <= code <= 0xFE4F or 0xFF00 <= code <= 0xFFEF):
            return True
    return False


def _raise_translation_unavailable(detail: str) -> None:
    raise CameraRefused("CAMERA_TRANSLATION_UNAVAILABLE", detail)


def _clean_english_prompt(value: Any) -> str:
    """Accept only a short, non-empty, English tag line; anything else is a named refusal.

    This is deliberately strict: a chatty answer (an explanation, a refusal, the original
    Chinese back again) must never be handed to the picture checkpoint as if it were tags.
    """

    if not isinstance(value, str):
        _raise_translation_unavailable("translator_answer_invalid")
    text = value.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        if text.rstrip().endswith("```"):
            text = text.rstrip()[:-3]
    text = text.strip().strip("`").strip().strip('"').strip("'").strip()
    lowered = text.lower()
    for label in ("english tags:", "prompt:", "tags:", "output:", "英文提示词：",
                  "英文提示词:", "提示词：", "提示词:", "英文:", "标签："):
        if lowered.startswith(label):
            text = text[len(label):].strip()
            lowered = text.lower()
    text = re.sub(r"\s*\n+\s*", ", ", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,;，；")
    if not text:
        _raise_translation_unavailable("translator_answer_empty")
    if len(text) > MAX_PROMPT_CHARS:
        # Length only -- the answer itself is never logged or stored, so a refusal stays safe
        # to report while still telling the next run how far over the bound it was.
        _raise_translation_unavailable("translator_answer_too_long:%d" % len(text))
    if _has_cjk(text):
        _raise_translation_unavailable("translator_answer_not_english")
    if any(ord(ch) < 32 for ch in text):
        _raise_translation_unavailable("translator_answer_has_control")
    lowered = text.lower()
    refusal = ("i'm sorry", "i am sorry", "i cannot", "i can't", "i can not",
               "as an ai", "unable to", "cannot help")
    if any(marker in lowered for marker in refusal):
        _raise_translation_unavailable("translator_refused")
    return text


class SceneTranslator:
    """Chinese scene -> English tags through the machine's **existing** local text runtime.

    Boundaries, all deliberate:

    * loopback only (same host/port checks as the picture runtime), no external API;
    * one request, temperature 0, small output budget, no retry and no queue: if the runtime is
      already processing something the caller gets ``CAMERA_TRANSLATOR_BUSY``;
    * no model selection, no model swap, no download -- it uses whatever that server loaded;
    * a scene that is already English is used as-is (``translated=False``): nothing is invented;
    * the answer must be short, ASCII/English tags; anything else is
      ``CAMERA_TRANSLATION_UNAVAILABLE`` and the caller must not fall back to the Chinese text.

    It never picks the subject, never writes the caption, and never stores anything.
    """

    def __init__(self, base_url: Any = None, *, environment: Optional[Mapping[str, str]] = None,
                 timeout: float = TRANSLATOR_TIMEOUT) -> None:
        env = dict(os.environ if environment is None else environment)
        try:
            self.base_url = _loopback_base_url(
                env.get("PHONE_CAMERA_TRANSLATOR_BASE_URL") or base_url
                or DEFAULT_TRANSLATOR_BASE_URL)
        except CameraRefused as exc:
            raise CameraRefused("CAMERA_TRANSLATOR_URL_INVALID", exc.reason_code) from exc
        self.timeout = float(timeout)
        self._bridge_python = _bridge_python_for(env)

    def _request(self, method: str, path: str, payload: Optional[Mapping[str, Any]] = None,
                 *, timeout: Optional[float] = None) -> Any:
        return _request_json(self.base_url, method, path, payload,
                             float(self.timeout if timeout is None else timeout),
                             self._bridge_python)

    def _refuse_if_busy(self) -> None:
        """Read only ``is_processing``; the slot's prompt/params are never read or printed."""

        try:
            slots = self._request("GET", TRANSLATOR_SLOTS_PATH, timeout=PROBE_TIMEOUT)
        except CameraRefused as exc:
            raise CameraRefused("CAMERA_TRANSLATOR_BUSY", "idle_state_unknown") from exc
        if not isinstance(slots, list):
            raise CameraRefused("CAMERA_TRANSLATOR_BUSY", "idle_state_unknown")
        for slot in slots:
            if isinstance(slot, dict) and slot.get("is_processing") is True:
                raise CameraRefused("CAMERA_TRANSLATOR_BUSY", "translator_is_processing")

    def prompt_for(self, scene: Any) -> dict[str, Any]:
        """``{"prompt", "translated", "source"}`` for one scene; refusals are named."""

        text = scene.strip() if isinstance(scene, str) else ""
        if not text:
            raise CameraRefused("CAMERA_SCENE_INVALID")
        if not _has_cjk(text):
            # Already English (or at least non-CJK): hand it over unchanged, and still record
            # that this is the caller's own text rather than a conversion.
            return {"prompt": text, "translated": False, "source": "caller_english"}

        self._refuse_if_busy()
        answer = self._request("POST", TRANSLATOR_CHAT_PATH, {
            "messages": [
                {"role": "system", "content": TRANSLATOR_SYSTEM_PROMPT},
                {"role": "user", "content": text},
            ],
            "temperature": 0.0,
            "max_tokens": TRANSLATOR_MAX_TOKENS,
            "seed": 42,
            "stream": False,
        })
        if not isinstance(answer, dict):
            raise CameraRefused("CAMERA_TRANSLATION_UNAVAILABLE", "translator_answer_invalid")
        if "error" in answer:
            raise CameraRefused("CAMERA_TRANSLATION_UNAVAILABLE", "translator_error")
        choices = answer.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices \
            and isinstance(choices[0], dict) else {}
        # A cut-off answer can still look like English tags, but it may have lost objects or
        # counts; truncation is never accepted as a successful conversion.
        if choice.get("finish_reason") == "length":
            _raise_translation_unavailable("translator_answer_truncated")
        message = choice.get("message")
        content = message.get("content") if isinstance(message, dict) else None
        prompt = _clean_english_prompt(content)
        return {"prompt": prompt, "translated": True, "source": "local_llama_cpp"}


__all__ = [
    "CameraRefused",
    "DEFAULT_BASE_URL",
    "DEFAULT_TRANSLATOR_BASE_URL",
    "GENERATION_PARAMS",
    "LocalPictureLab",
    "MAX_CAPTION_CHARS",
    "MAX_IMAGE_BYTES",
    "MAX_PROMPT_CHARS",
    "MAX_SCENE_CHARS",
    "SceneTranslator",
]
