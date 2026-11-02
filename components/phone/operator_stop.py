#!/usr/bin/env python3
"""每次 serve 轮换的本地 operator 凭据（外部叫停的唯一服务端凭据）。

它与私人日记凭据是**两套互不通用的东西**：

* 这里是 ``phone-operator-stop``，文件名 ``operator-stop.json``，锁
  ``operator-stop.lock``；日记是 ``phone-capability`` / ``phone-capability.json``。
  拿日记 token 调叫停路由会被拒，拿这里的 token 读日记也会被拒。
* token 每次 serve 重新签发（32 字节随机），并与本次 serve 的 ``epoch`` 一起绑定到
  subject、实际回环 endpoint。重启后的旧 token 与旧 epoch 都拒绝。
* 服务端只做常量时间比较；请求体里的 ``operator`` / ``system`` / ``uid`` / ``actor``
  一律不接受。能读这个文件的同 uid 进程与管理员仍在保护边界之外；NTFS ACL 本项目
  不做核验，因此 Windows 上不签发（fail closed）。
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import urlsplit

SCHEMA_OPERATOR_STOP = 1
KIND_OPERATOR_STOP = "phone-operator-stop"
FILENAME = "operator-stop.json"
LOCK_FILENAME = "operator-stop.lock"
TOKEN_BYTES = 32
MIN_TOKEN_CHARS = 32


class OperatorStopCredentialError(RuntimeError):
    """Typed refusal; never carries the token."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(str(reason_code))
        self.reason_code = str(reason_code)


class OperatorStopUnavailable(OperatorStopCredentialError):
    """No credential may be written here; the stop route fails closed."""


class OperatorStopInvalid(OperatorStopCredentialError):
    """A credential file exists but is not one this module can trust."""


@dataclass(frozen=True, repr=False)
class OperatorStopCredential:
    token: str
    subject: str
    endpoint: str
    epoch: str
    issued_at: str
    #: 签发它的实例根（已 resolve）。凭据被复制到另一个实例根时不得复用。
    instance: str = ""
    #: 这个通道真正有权叫停的 Life subject（canonical Activity 的 subject）。
    life_subject: str = ""

    def __repr__(self) -> str:
        return ("<OperatorStopCredential subject=%r life_subject=%r instance=%r endpoint=%r "
                "token=REDACTED>" % (self.subject, self.life_subject, self.instance,
                                     self.endpoint))

    __str__ = __repr__


def credential_path(root: Path | str) -> Path:
    return Path(root) / "run" / FILENAME


def lock_path(root: Path | str) -> Path:
    return Path(root) / "run" / LOCK_FILENAME


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _inside_repository(path: Path) -> bool:
    try:
        path.relative_to(_repository_root())
        return True
    except ValueError:
        return False


def _private_file_ok(path: Path) -> bool:
    if os.name == "nt":
        return False
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    return (stat.S_IMODE(info.st_mode) & 0o077) == 0


def _private_directory_ok(path: Path) -> bool:
    if os.name == "nt":
        return False
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    return (stat.S_IMODE(info.st_mode) & 0o022) == 0


def _valid_endpoint(endpoint: str) -> bool:
    parts = urlsplit(str(endpoint))
    if parts.scheme != "http" or (parts.hostname or "").lower() not in {"127.0.0.1", "localhost"}:
        return False
    if not parts.port or parts.path not in ("", "/") or parts.query or parts.fragment:
        return False
    return True


class _EndpointRefused(ValueError):
    """A typed endpoint refusal; carries only a reason code."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(str(reason_code))
        self.reason_code = str(reason_code)


def normalize_loopback_endpoint(endpoint: str) -> str:
    """The one shape a stop request may ever be sent to.

    Only ``http://127.0.0.1:PORT`` / ``http://localhost:PORT`` (and the IPv6 loopback in its
    literal form) is accepted.  Userinfo, a path, a query, a fragment, any other host, and a
    missing port are refused *before* anything is sent, so a caller cannot steer the token to
    another host or port by dressing it up as a loopback URL.
    """

    text = str(endpoint or "").strip()
    if not text:
        raise _EndpointRefused("ENDPOINT_REQUIRED")
    parts = urlsplit(text)
    if parts.scheme != "http":
        raise _EndpointRefused("ENDPOINT_SCHEME_NOT_LOOPBACK_HTTP")
    if "@" in parts.netloc:
        raise _EndpointRefused("ENDPOINT_USERINFO_REFUSED")
    if parts.path not in ("", "/"):
        raise _EndpointRefused("ENDPOINT_PATH_REFUSED")
    if parts.query or parts.fragment:
        raise _EndpointRefused("ENDPOINT_QUERY_REFUSED")
    host = (parts.hostname or "").lower()
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise _EndpointRefused("ENDPOINT_NOT_LOOPBACK")
    try:
        port = parts.port
    except ValueError:
        raise _EndpointRefused("ENDPOINT_PORT_INVALID") from None
    if not port or not (1 <= int(port) <= 65535):
        raise _EndpointRefused("ENDPOINT_PORT_REQUIRED")
    rendered_host = "[::1]" if host == "::1" else host
    return "http://%s:%d" % (rendered_host, int(port))


def endpoint_matches(credential: OperatorStopCredential, endpoint: str) -> bool:
    """True only when the caller's target is *exactly* the endpoint this serve bound."""

    try:
        presented = normalize_loopback_endpoint(endpoint)
        bound = normalize_loopback_endpoint(credential.endpoint)
    except _EndpointRefused:
        return False
    return hmac.compare_digest(presented, bound)


def issue(root: Path | str, *, subject: str, endpoint: str,
          life_subject: Optional[str] = None,
          private_mode_check: Optional[Callable[[Path], bool]] = None
          ) -> tuple[Path, OperatorStopCredential]:
    root_path = Path(root).expanduser().resolve()
    if _inside_repository(root_path):
        raise OperatorStopUnavailable("ROOT_INSIDE_REPOSITORY")
    subject_text = str(subject or "").strip()
    endpoint_text = str(endpoint or "").strip()
    if not subject_text:
        raise OperatorStopUnavailable("SUBJECT_REQUIRED")
    if not _valid_endpoint(endpoint_text):
        raise OperatorStopUnavailable("ENDPOINT_INVALID")
    try:
        endpoint_text = normalize_loopback_endpoint(endpoint_text)
    except _EndpointRefused as exc:
        raise OperatorStopUnavailable(exc.reason_code) from None
    # ``life_subject`` is the scope the stop route really acts on (the canonical Activity
    # subject).  A caller that does not know it falls back to the instance subject, and the
    # eventual request is still checked against the canonical state on both ends.
    life_subject_text = str(life_subject if life_subject is not None else subject_text).strip()
    if not life_subject_text:
        raise OperatorStopUnavailable("LIFE_SUBJECT_REQUIRED")

    run_dir = root_path / "run"
    if run_dir.is_symlink():
        raise OperatorStopUnavailable("RUN_DIRECTORY_IS_SYMLINK")
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise OperatorStopUnavailable("RUN_DIRECTORY_UNAVAILABLE") from exc
    if not _private_directory_ok(run_dir):
        raise OperatorStopUnavailable("DIRECTORY_NOT_PRIVATE")

    target = run_dir / FILENAME
    if target.is_symlink():
        raise OperatorStopUnavailable("CAPABILITY_PATH_IS_SYMLINK")
    if target.exists() and not target.is_file():
        raise OperatorStopUnavailable("CAPABILITY_PATH_NOT_A_FILE")

    epoch = secrets.token_hex(16)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    payload = {
        "schema_version": SCHEMA_OPERATOR_STOP,
        "kind": KIND_OPERATOR_STOP,
        "token": token,
        "subject": subject_text,
        "life_subject": life_subject_text,
        "instance": str(root_path),
        "endpoint": endpoint_text,
        "epoch": epoch,
        "issued_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = run_dir / (".%s.%s.tmp" % (FILENAME, secrets.token_hex(8)))
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    created = False
    try:
        descriptor = os.open(tmp, flags, 0o600)
        created = True
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        checker = private_mode_check or _private_file_ok
        if not checker(tmp):
            raise OperatorStopUnavailable("STORAGE_NOT_PRIVATE")
        os.replace(tmp, target)
    except OperatorStopUnavailable:
        raise
    except OSError as exc:
        raise OperatorStopUnavailable("CAPABILITY_WRITE_FAILED") from exc
    finally:
        if created and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    checker = private_mode_check or _private_file_ok
    try:
        private_ok = checker(target)
        if private_ok:
            os.chmod(target, 0o600)
    except OSError:
        private_ok = False
    if not private_ok:
        try:
            target.unlink()
        except OSError:
            pass
        raise OperatorStopUnavailable("STORAGE_NOT_PRIVATE")
    return target, OperatorStopCredential(
        token=token, subject=subject_text, endpoint=endpoint_text, epoch=epoch,
        issued_at=payload["issued_at"], instance=str(root_path),
        life_subject=life_subject_text)


def read_credential(path: Path | str) -> OperatorStopCredential:
    target = Path(path)
    if target.is_symlink():
        raise OperatorStopInvalid("CAPABILITY_PATH_IS_SYMLINK")
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise OperatorStopInvalid("CAPABILITY_FILE_UNREADABLE") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise OperatorStopInvalid("CAPABILITY_FILE_NOT_JSON") from exc
    if not isinstance(payload, dict):
        raise OperatorStopInvalid("CAPABILITY_FILE_NOT_AN_OBJECT")
    if payload.get("kind") != KIND_OPERATOR_STOP:
        raise OperatorStopInvalid("CAPABILITY_KIND_UNKNOWN")
    if payload.get("schema_version") != SCHEMA_OPERATOR_STOP:
        raise OperatorStopInvalid("CAPABILITY_SCHEMA_UNKNOWN")
    token = payload.get("token")
    subject = payload.get("subject")
    endpoint = payload.get("endpoint")
    epoch = payload.get("epoch")
    if not isinstance(token, str) or len(token) < MIN_TOKEN_CHARS:
        raise OperatorStopInvalid("CAPABILITY_TOKEN_INVALID")
    if not isinstance(subject, str) or not subject.strip():
        raise OperatorStopInvalid("CAPABILITY_SUBJECT_INVALID")
    if not isinstance(endpoint, str) or not _valid_endpoint(endpoint):
        raise OperatorStopInvalid("CAPABILITY_ENDPOINT_INVALID")
    if not isinstance(epoch, str) or len(epoch) < 16:
        raise OperatorStopInvalid("CAPABILITY_EPOCH_INVALID")
    # Issued after 2026-10-28: a credential names the instance root it was written for and the
    # Life subject it may stop.  An old file without them cannot be bound to a caller, so it
    # fails closed here instead of being trusted on the token alone.
    instance = payload.get("instance")
    if not isinstance(instance, str) or not instance.strip():
        raise OperatorStopInvalid("CAPABILITY_INSTANCE_MISSING")
    life_subject = payload.get("life_subject")
    if not isinstance(life_subject, str) or not life_subject.strip():
        raise OperatorStopInvalid("CAPABILITY_LIFE_SUBJECT_MISSING")
    try:
        endpoint_text = normalize_loopback_endpoint(endpoint)
    except _EndpointRefused as exc:
        raise OperatorStopInvalid(exc.reason_code) from None
    return OperatorStopCredential(
        token=token, subject=subject.strip(), endpoint=endpoint_text,
        epoch=epoch, issued_at=str(payload.get("issued_at") or ""),
        instance=str(Path(instance).expanduser().resolve()),
        life_subject=life_subject.strip())


def matches(credential: Optional[OperatorStopCredential], presented: Optional[str],
            epoch: Optional[str] = None) -> bool:
    """Constant-time token check plus the serve epoch (a rebuilt serve revokes the old one)."""

    if credential is None or not presented:
        return False
    if epoch is not None and not hmac.compare_digest(str(credential.epoch), str(epoch)):
        return False
    try:
        return hmac.compare_digest(str(credential.token), str(presented))
    except (TypeError, ValueError):
        return False


def discard(root: Path | str, credential: Optional[OperatorStopCredential]) -> bool:
    if credential is None:
        return False
    target = credential_path(root)
    if target.is_symlink():
        return False
    try:
        current = read_credential(target)
    except OperatorStopInvalid:
        return False
    if not hmac.compare_digest(current.token, credential.token):
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


class OperatorStopLock:
    def __init__(self, descriptor: int, path: Path) -> None:
        self._descriptor: Optional[int] = int(descriptor)
        self.path = path

    def release(self) -> None:
        descriptor, self._descriptor = self._descriptor, None
        if descriptor is None:
            return
        try:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        except Exception:
            pass
        try:
            os.close(descriptor)
        except OSError:
            pass


def acquire_lock(root: Path | str) -> Optional[OperatorStopLock]:
    if os.name == "nt":
        return None
    try:
        import fcntl
    except ImportError:
        return None
    run_dir = Path(root) / "run"
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    target = run_dir / LOCK_FILENAME
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(target, flags, 0o600)
    except OSError:
        return None
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        try:
            os.close(descriptor)
        except OSError:
            pass
        return None
    return OperatorStopLock(descriptor, target)


__all__ = [
    "FILENAME",
    "KIND_OPERATOR_STOP",
    "LOCK_FILENAME",
    "MIN_TOKEN_CHARS",
    "OperatorStopCredential",
    "OperatorStopCredentialError",
    "OperatorStopInvalid",
    "OperatorStopLock",
    "OperatorStopUnavailable",
    "SCHEMA_OPERATOR_STOP",
    "TOKEN_BYTES",
    "acquire_lock",
    "credential_path",
    "discard",
    "endpoint_matches",
    "issue",
    "lock_path",
    "normalize_loopback_endpoint",
    "matches",
    "read_credential",
]
