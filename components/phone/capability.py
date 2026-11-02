#!/usr/bin/env python3
"""每次 serve 轮换的本地能力凭据（私人日记读写的唯一服务端凭据）。

它解决的是“回环 HTTP 上任何客户端都能自称 ``X-Phone-Actor: model``”这一条：
日记正文读取与主体日记写入除了 actor 声明，还必须持有本次 serve 签发的不透明凭据。
它**不是**模型身份，也不承诺“仅她可读”：

* 能读取凭据文件的同 uid 本地进程、能读该文件的任何账号、管理员，仍在保护边界外；
* 凭据文件必须由实际文件系统强制为 0600；无法确认私有权限时（例如 Windows NTFS
  没有本项目自己核验的 ACL），不写凭据，私人正文与主体写入 fail closed；
* 每次 serve 重新签发；旧进程/旧凭据在服务端只与内存中的当前凭据比较，重启后旧值拒绝；
* 同一实例根同时只允许一个 serve 轮换凭据（``flock``）；后启动的 serve 拿不到锁时
  不覆盖正在服务的凭据，只把日记私人面关掉。

凭据内容只出现在进程内存和实例根下的受管文件里；这里不打印、不写入日志、
不放进异常消息或 ``repr``。
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

SCHEMA_CAPABILITY = 1
KIND_CAPABILITY = "phone-capability"
FILENAME = "phone-capability.json"
LOCK_FILENAME = "phone-capability.lock"
#: 32 random bytes.  ``token_urlsafe`` renders them as 43 URL-safe characters.
TOKEN_BYTES = 32
#: A token shorter than this is not a credential this module issued.
MIN_TOKEN_CHARS = 32


class CapabilityError(RuntimeError):
    """A typed, non-secret-bearing capability refusal."""

    def __init__(self, reason_code: str) -> None:
        super().__init__(str(reason_code))
        self.reason_code = str(reason_code)


class CapabilityUnavailable(CapabilityError):
    """No credential may be written here; the private diary must fail closed."""


class CapabilityInvalid(CapabilityError):
    """A credential file exists but is not one this module can trust."""


@dataclass(frozen=True, repr=False)
class Capability:
    """One serve's credential.  ``token`` is never part of ``repr``/``str``."""

    token: str
    subject: str
    endpoint: str
    issued_at: str

    def __repr__(self) -> str:
        return "<Capability subject=%r endpoint=%r token=REDACTED>" % (
            self.subject, self.endpoint)

    __str__ = __repr__


# ---------------------------------------------------------------------------------------------
# paths and the repository boundary
# ---------------------------------------------------------------------------------------------


def capability_path(root: Path | str) -> Path:
    return Path(root) / "run" / FILENAME


def lock_path(root: Path | str) -> Path:
    return Path(root) / "run" / LOCK_FILENAME


def _repository_root() -> Path:
    # components/phone/capability.py -> components/phone -> components -> repository
    return Path(__file__).resolve().parents[2]


def _inside_repository(path: Path) -> bool:
    try:
        path.relative_to(_repository_root())
        return True
    except ValueError:
        return False


def _private_file_ok(path: Path) -> bool:
    """The real filesystem must enforce owner-only access.  Best effort only on POSIX."""

    if os.name == "nt":  # NTFS ACLs are not verified by this project: fail closed.
        return False
    try:
        info = os.stat(path, follow_symlinks=False)
    except OSError:
        return False
    if not stat.S_ISREG(info.st_mode):
        return False
    return (stat.S_IMODE(info.st_mode) & 0o077) == 0


def _private_directory_ok(path: Path) -> bool:
    """Our own ``run`` directory must not be writable by group/other."""

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
    """Only a loopback HTTP endpoint may own a credential."""

    parts = urlsplit(str(endpoint))
    if parts.scheme != "http" or (parts.hostname or "").lower() not in {"127.0.0.1", "localhost"}:
        return False
    if not parts.port or parts.path not in ("", "/") or parts.query or parts.fragment:
        return False
    return True


# ---------------------------------------------------------------------------------------------
# issue / read / discard
# ---------------------------------------------------------------------------------------------


def issue(root: Path | str, *, subject: str, endpoint: str,
          private_mode_check: Optional[Callable[[Path], bool]] = None
          ) -> tuple[Path, Capability]:
    """Write one fresh credential for this serve, atomically and owner-only.

    ``private_mode_check`` exists for controlled fault injection in the demo/tests: a checker
    that answers ``False`` proves the code removes the half-written secret and fails closed.
    Production callers never pass it.
    """

    root_path = Path(root).expanduser().resolve()
    if _inside_repository(root_path):
        raise CapabilityUnavailable("ROOT_INSIDE_REPOSITORY")
    subject_text = str(subject or "").strip()
    endpoint_text = str(endpoint or "").strip()
    if not subject_text:
        raise CapabilityUnavailable("SUBJECT_REQUIRED")
    if not _valid_endpoint(endpoint_text):
        raise CapabilityUnavailable("ENDPOINT_INVALID")

    run_dir = root_path / "run"
    if run_dir.is_symlink():
        raise CapabilityUnavailable("RUN_DIRECTORY_IS_SYMLINK")
    try:
        run_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise CapabilityUnavailable("RUN_DIRECTORY_UNAVAILABLE") from exc
    if not _private_directory_ok(run_dir):
        # No secret is created when the directory boundary is not trustworthy.
        raise CapabilityUnavailable("DIRECTORY_NOT_PRIVATE")

    target = run_dir / FILENAME
    if target.is_symlink():
        raise CapabilityUnavailable("CAPABILITY_PATH_IS_SYMLINK")
    if target.exists() and not target.is_file():
        raise CapabilityUnavailable("CAPABILITY_PATH_NOT_A_FILE")

    token = secrets.token_urlsafe(TOKEN_BYTES)
    issued_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    payload = {
        "schema_version": SCHEMA_CAPABILITY,
        "kind": KIND_CAPABILITY,
        "token": token,
        "subject": subject_text,
        "endpoint": endpoint_text,
        "issued_at": issued_at,
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
            raise CapabilityUnavailable("STORAGE_NOT_PRIVATE")
        os.replace(tmp, target)
    except CapabilityUnavailable:
        raise
    except OSError as exc:
        raise CapabilityUnavailable("CAPABILITY_WRITE_FAILED") from exc
    finally:
        if created and tmp.exists():
            try:
                tmp.unlink()
            except OSError:
                pass

    # Re-check the final file (replace is atomic, but the mode must still be enforced).
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
        raise CapabilityUnavailable("STORAGE_NOT_PRIVATE")
    return target, Capability(token=token, subject=subject_text, endpoint=endpoint_text,
                              issued_at=issued_at)


def read_capability(path: Path | str) -> Capability:
    """Read one credential file for the client side.  Never logs or echoes its content."""

    target = Path(path)
    if target.is_symlink():
        raise CapabilityInvalid("CAPABILITY_PATH_IS_SYMLINK")
    try:
        raw = target.read_text(encoding="utf-8")
    except OSError as exc:
        raise CapabilityInvalid("CAPABILITY_FILE_UNREADABLE") from exc
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise CapabilityInvalid("CAPABILITY_FILE_NOT_JSON") from exc
    if not isinstance(payload, dict):
        raise CapabilityInvalid("CAPABILITY_FILE_NOT_AN_OBJECT")
    if payload.get("kind") != KIND_CAPABILITY:
        raise CapabilityInvalid("CAPABILITY_KIND_UNKNOWN")
    if payload.get("schema_version") != SCHEMA_CAPABILITY:
        raise CapabilityInvalid("CAPABILITY_SCHEMA_UNKNOWN")
    token = payload.get("token")
    subject = payload.get("subject")
    endpoint = payload.get("endpoint")
    issued_at = payload.get("issued_at")
    if not isinstance(token, str) or len(token) < MIN_TOKEN_CHARS:
        raise CapabilityInvalid("CAPABILITY_TOKEN_INVALID")
    if not isinstance(subject, str) or not subject.strip():
        raise CapabilityInvalid("CAPABILITY_SUBJECT_INVALID")
    if not isinstance(endpoint, str) or not _valid_endpoint(endpoint):
        raise CapabilityInvalid("CAPABILITY_ENDPOINT_INVALID")
    return Capability(token=token, subject=subject.strip(), endpoint=endpoint.strip(),
                      issued_at=str(issued_at or ""))


def matches(capability: Optional[Capability], presented: Optional[str]) -> bool:
    """Constant-time comparison; anything absent or non-ASCII is a refusal."""

    if capability is None or not presented:
        return False
    try:
        return hmac.compare_digest(str(capability.token), str(presented))
    except (TypeError, ValueError):
        # Non-ASCII header bytes can never equal a URL-safe token; fail closed, never 500.
        return False


def discard(root: Path | str, capability: Optional[Capability]) -> bool:
    """Remove *our* credential on a clean shutdown; another serve's file is never touched."""

    if capability is None:
        return False
    target = capability_path(root)
    if target.is_symlink():
        return False
    try:
        current = read_capability(target)
    except CapabilityInvalid:
        return False
    if not hmac.compare_digest(current.token, capability.token):
        return False
    try:
        target.unlink()
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------------------------
# one rotating writer per instance root
# ---------------------------------------------------------------------------------------------


class CapabilityLock:
    """Advisory lock held for the life of one serve."""

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


def acquire_lock(root: Path | str) -> Optional[CapabilityLock]:
    """Try to become the one serve allowed to rotate this root's credential.

    ``None`` means another live serve owns the credential (or this platform cannot lock).
    The caller must then *not* write or rotate the file and must fail the private diary closed.
    """

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
    return CapabilityLock(descriptor, target)


__all__ = [
    "Capability",
    "CapabilityError",
    "CapabilityInvalid",
    "CapabilityLock",
    "CapabilityUnavailable",
    "FILENAME",
    "KIND_CAPABILITY",
    "LOCK_FILENAME",
    "MIN_TOKEN_CHARS",
    "SCHEMA_CAPABILITY",
    "TOKEN_BYTES",
    "acquire_lock",
    "capability_path",
    "discard",
    "issue",
    "lock_path",
    "matches",
    "read_capability",
]
