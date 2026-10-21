"""v0.4 阶段 B：资料库 + 研究工作台，仍然只落在既有 ``ManagedArtifact`` owner 上。

这个模块是什么
--------------
一部资料库和一个研究工作台，两者都只是**同一个 owner 的两种视图**：

* **资料（material）**：同 owner 的、带公开类型标记的 artifact。第一行是机器可读标记
  ``<!-- nyairo-material:v1 {JSON} -->``，正文是操作者自己提供的文本。``source_kind`` 只有
  ``user_supplied`` / ``fictional`` 两种，``origin`` 是**来源声明**，不是真实性认证。本阶段
  不联网抓取任何内容。
* **研究（research）**：一件可以由既有 AR0 ``commit_version`` 续写的 Markdown 作品。第一行是
  ``<!-- nyairo-research:v1 {JSON} -->``，JSON 至少含 ``question`` / ``source_refs``
  （每项 ``artifact_id`` + ``source_revision``）/ ``scope`` / ``uncertainty``。正文是暂定研究笔记，
  writer 只能往下写；marker 不在文末、也不由 writer 改写（``update_research`` 在每次提交时用
  owner 里那份 marker 重建第一行，正文原样保留）。

它不是什么
----------
* **不是第二个 store**。没有新表、没有新数据库、没有第二份 Memory：列资料/列研究都是
  ``list_artifacts`` + ``read_artifact``，写都是 ``create_artifact`` / ``commit_version`` /
  ``archive_artifact``，全部经 :class:`PrivateProjectService`（它自己又经 ``LifeArtifactPort``）。
* **不是抓取器**。``import_material`` 只存操作者交来的文本；本模块没有 http 客户端，
  也从不声称"读到了某一页"。
* **不是 Memory、不是稳定信念**。研究作品的内容是 owner 文档情境，``read_research`` 每次都会
  带上 ``not_memory=True`` / ``stable_belief=False`` 和一句人话说明；关闭研究走 ``archive``，
  一个字都不删。
* **不抛异常给"owner 说了什么"**。编程错误抛 ``ResearchContractRefused``；owner 的答复一律是
  带 ``status`` 的 dict：``ok / applied / already_applied / conflict / not_found / rejected /
  unknown / unavailable``（与 :mod:`private_project_service` 同一个词表）。UNKNOWN 先按**原 key**
  对账再谈重试（由 ``PrivateProjectService`` 完成，本模块只透传 ``reconciled`` / ``retryable``）。

给 A 的固定接缝
--------------
* :class:`ResearchWorkspaceService` —— 固定构造 ``ResearchWorkspaceService(projects, *,
  reading_artifact_id=None, private_items_artifact_id=None)``，``projects`` 是
  :class:`PrivateProjectService` 实例。
* :func:`research_from_env` —— ``research_from_env(port, env)``：环境不全就返回 ``None``
  （与 ``private_projects_from_env`` 相同的 env/bindings），绝不造一个半可用的对象。
* 两个纯 helper：:func:`parse_research_content` / :func:`build_research_content`，外加
  :func:`is_material_content` / :func:`parse_material_content` 让 A 也能认出资料工件。
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

_LIFE_SCRIPTS = str(Path(__file__).resolve().parent)
if _LIFE_SCRIPTS not in sys.path:
    sys.path.insert(0, _LIFE_SCRIPTS)

from life_artifact_port import (  # noqa: E402
    ArtifactOwnerRefused,
    LifeArtifactError,
    LifeArtifactUnavailable,
)
from private_project_service import (  # noqa: E402
    ENV_GRANT_NAMES,
    ENV_SUBJECT_NAMES,
    ENV_WORKSPACE_NAMES,
    MAX_CONTENT_CHARS,
    MAX_TITLE_CHARS,
    PrivateProjectService,
    ProjectContractRefused,
    ProjectUnavailable,
    SOURCE_OWNER,
    STATUS_ALREADY_APPLIED,
    STATUS_APPLIED,
    STATUS_CONFLICT,
    STATUS_NOT_FOUND,
    STATUS_OK,
    STATUS_REJECTED,
    STATUS_UNAVAILABLE,
    STATUS_UNKNOWN,
    default_excluded_artifact_ids,
    life_supply_port_from_env,
)

# ---------------------------------------------------------------------------------------------
# 标记（公开、可检测、A 可复用）
# ---------------------------------------------------------------------------------------------

#: 资料工件第一行的标记名。``<!-- nyairo-material:v1 {JSON} -->``
MATERIAL_MARKER = "nyairo-material:v1"
#: 研究作品第一行的标记名。``<!-- nyairo-research:v1 {JSON} -->``
RESEARCH_MARKER = "nyairo-research:v1"
#: 标记在正文里的唯一形状：行首 ``<!--``，行尾 `` -->``，中间是单行 JSON。
MARKER_OPEN = "<!-- "
MARKER_CLOSE = " -->"

#: 允许的 ``source_kind``：一个是操作者自己提供的文本，一个是他明确说这是虚构的。
SOURCE_KIND_USER = "user_supplied"
SOURCE_KIND_FICTION = "fictional"
SOURCE_KINDS: tuple[str, ...] = (SOURCE_KIND_USER, SOURCE_KIND_FICTION)
#: 既有阅读材料（``reading_artifact_id``）在资料列表里的类型。它**只读**：导入不会覆盖它。
SOURCE_KIND_READING = "reading_material"

#: 来源核对结果。``UNAVAILABLE`` 同时覆盖"读不到"和"已归档/撤回"。
CHECK_CURRENT = "CURRENT"
CHECK_STALE = "STALE"
CHECK_UNAVAILABLE = "UNAVAILABLE"

#: 工件类型（研究列表/项目列表都靠它区分"可续写作品"与"资料/研究"）。
KIND_PROJECT = "project"
KIND_MATERIAL = "material"
KIND_READING_MATERIAL = "reading_material"
KIND_RESEARCH = "research"
KIND_UNKNOWN = "unknown"

#: 研究作品默认的诚实措辞：不是"已验证"。
DEFAULT_UNCERTAINTY = "暂定，尚未验证"
#: 每次读取研究/资料都会带上的那句话。
NOT_MEMORY_NOTE = "研究作品不是 Memory，也不是稳定信念：内容仍是暂定的，随来源版本变化。"

MAX_QUESTION_CHARS = 500
MAX_SCOPE_CHARS = 400
MAX_UNCERTAINTY_CHARS = 400
MAX_ORIGIN_CHARS = 300
MAX_MATERIAL_TEXT_CHARS = 19000
MAX_RESEARCH_BODY_CHARS = 19000
MAX_SOURCE_REFS = 12
MAX_MATERIALS = 50
MAX_RESEARCH = 50
MAX_SCAN = 200
MAX_EXCERPT_CHARS = 160

#: 读资料/研究时最多扫多少件工件（一件一次 ``read_artifact``）。超出就照实报截断。
DEFAULT_LIMIT = 12


class ResearchWorkspaceError(RuntimeError):
    """本模块自己的错误基类（编程/接线错误，不是 owner 的答复）。"""


class ResearchContractRefused(ResearchWorkspaceError):
    """调用方给的形状本模块不接受。"""


class ResearchUnavailable(ResearchWorkspaceError):
    """没有可用的 ``PrivateProjectService``（owner 根本没被问过）。"""


# ---------------------------------------------------------------------------------------------
# 纯 helper：marker 的读与写（A 直接可用，不用复制第二套定义）
# ---------------------------------------------------------------------------------------------


def _json_line(payload: Mapping[str, Any]) -> str:
    return json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _marker_line(name: str, payload: Mapping[str, Any]) -> str:
    line = MARKER_OPEN + str(name) + " " + _json_line(payload) + MARKER_CLOSE
    if "\n" in line or "\r" in line:  # json.dumps 已经转义换行；这里只是不变量
        raise ResearchContractRefused("marker line must be one physical line")
    return line


def _split_marker(content: Any) -> tuple[Optional[tuple[str, str]], str]:
    """把 content 拆成 ``((标记名, JSON 文本) | None, 正文)``。

    只认**第一行**。标记缺失、或第一行不是标记形状，就原样把整段当正文返回——绝不猜。
    """
    if not isinstance(content, str):
        return None, ""
    first, separator, rest = content.partition("\n")
    line = first.strip()
    if line.startswith(MARKER_OPEN) and line.endswith(MARKER_CLOSE):
        inner = line[len(MARKER_OPEN):-len(MARKER_CLOSE)]
        name, space, blob = inner.partition(" ")
        if space and name and blob:
            body = rest if separator else ""
            # 标记行与正文之间那一个空行是分隔，不是正文的一部分（只剥一个）。
            if body.startswith("\n"):
                body = body[1:]
            return (name.strip(), blob.strip()), body
    return None, content


def _parse_marker(content: Any, name: str) -> dict[str, Any]:
    found, body = _split_marker(content)
    result: dict[str, Any] = {
        "marker_present": False,
        "marker_version": "",
        "parse_error": "",
        "body": body,
        "marker_line": "",
    }
    if found is None or found[0] != name:
        if found is not None:
            result["parse_error"] = "OTHER_MARKER:%s" % found[0]
        return result
    try:
        payload = json.loads(found[1])
    except ValueError:
        result["marker_present"] = True
        result["marker_version"] = found[0]
        result["parse_error"] = "MARKER_JSON_INVALID"
        return result
    if not isinstance(payload, Mapping):
        result["marker_present"] = True
        result["marker_version"] = found[0]
        result["parse_error"] = "MARKER_NOT_AN_OBJECT"
        return result
    result["marker_present"] = True
    result["marker_version"] = found[0]
    result["marker_line"] = MARKER_OPEN + found[0] + " " + found[1] + MARKER_CLOSE
    result["payload"] = dict(payload)
    return result


def _clean_refs(value: Any) -> list[dict[str, str]]:
    """把来源引用规范成 ``[{artifact_id, source_revision}]``（宽松但绝不编造）。"""
    if not isinstance(value, (list, tuple)):
        return []
    rows: list[dict[str, str]] = []
    for item in value:
        if isinstance(item, Mapping):
            artifact_id = item.get("artifact_id")
            revision = item.get("source_revision")
        elif isinstance(item, str):
            artifact_id = item
            revision = ""
        else:
            continue
        if not isinstance(artifact_id, str) or not artifact_id.strip():
            continue
        text = artifact_id.strip()
        rows.append({"artifact_id": text,
                     "source_revision": revision.strip() if isinstance(revision, str) else ""})
    return rows


def parse_research_content(content: Any) -> dict[str, Any]:
    """读一件研究作品的内容：**第一行**的 marker + 正文。

    总是返回一个 dict（不抛、不猜）：

    ``marker_present`` / ``marker_version`` / ``parse_error`` / ``question`` /
    ``source_refs`` / ``scope`` / ``uncertainty`` / ``title`` / ``body`` / ``marker_line``

    没有 marker（或第一行是别的标记）时 ``marker_present=False``、``parse_error`` 说明原因，
    ``body`` 就是整段内容——调用方据此自己判断"这不是一件研究作品"。
    """
    parsed = _parse_marker(content, RESEARCH_MARKER)
    payload = parsed.get("payload") if isinstance(parsed.get("payload"), Mapping) else {}
    question = payload.get("question")
    scope = payload.get("scope")
    uncertainty = payload.get("uncertainty")
    title = payload.get("title")
    return {
        "marker_present": bool(parsed.get("marker_present")),
        "marker_version": str(parsed.get("marker_version") or ""),
        "parse_error": str(parsed.get("parse_error") or ""),
        "marker_line": str(parsed.get("marker_line") or ""),
        "question": question if isinstance(question, str) else "",
        "source_refs": _clean_refs(payload.get("source_refs")),
        "scope": scope if isinstance(scope, str) else "",
        "uncertainty": uncertainty if isinstance(uncertainty, str) else "",
        "title": title if isinstance(title, str) else "",
        "body": parsed.get("body") if isinstance(parsed.get("body"), str) else "",
    }


def is_research_content(content: Any) -> bool:
    """这件内容的第一行是不是**有效**的研究标记（A 用它可以不解析就分流）。"""
    parsed = _parse_marker(content, RESEARCH_MARKER)
    return bool(parsed.get("marker_present")) and not parsed.get("parse_error")


def build_research_content(
    question: str,
    source_refs: Iterable[Any] = (),
    scope: str = "",
    uncertainty: str = DEFAULT_UNCERTAINTY,
    body: str = "",
    title: Optional[str] = None,
) -> str:
    """拼一件研究作品的内容：第一行 marker，然后空行，然后正文。

    marker 的 JSON 至少含 ``question`` / ``source_refs`` / ``scope`` / ``uncertainty``；
    ``title`` 只是顺带存一份（工件行里也有标题，但 A 只拿 content 时也用得上）。
    """
    if not isinstance(question, str) or not question.strip():
        raise ResearchContractRefused("question must be a non-empty string")
    clean_question = _one_line(question, "question", limit=MAX_QUESTION_CHARS)
    clean_scope = _text_or_empty(scope, "scope", limit=MAX_SCOPE_CHARS)
    clean_uncertainty = _text_or_empty(uncertainty, "uncertainty", limit=MAX_UNCERTAINTY_CHARS)
    clean_body = body if isinstance(body, str) else ""
    if "\x00" in clean_body:
        raise ResearchContractRefused("body must not contain NUL")
    payload: dict[str, Any] = {
        "question": clean_question,
        "source_refs": [dict(row) for row in _clean_refs(source_refs)],
        "scope": clean_scope,
        "uncertainty": clean_uncertainty or DEFAULT_UNCERTAINTY,
    }
    if isinstance(title, str) and title.strip():
        payload["title"] = _one_line(title, "title", limit=MAX_TITLE_CHARS)
    head = _marker_line(RESEARCH_MARKER, payload)
    text = clean_body if clean_body.strip() else ""
    return head + ("\n\n" + text if text else "\n")


def parse_material_content(content: Any) -> Optional[dict[str, Any]]:
    """读一件资料工件的内容；第一行不是资料标记就返回 ``None``（诚实，不猜）。"""
    parsed = _parse_marker(content, MATERIAL_MARKER)
    if not parsed.get("marker_present") or parsed.get("parse_error"):
        return None
    payload = parsed.get("payload") if isinstance(parsed.get("payload"), Mapping) else {}
    source_kind = payload.get("source_kind")
    origin = payload.get("origin")
    return {
        "source_kind": source_kind if isinstance(source_kind, str) else "",
        "origin": origin if isinstance(origin, str) else "",
        "declared_title": (payload.get("title") if isinstance(payload.get("title"), str) else ""),
        "body": parsed.get("body") if isinstance(parsed.get("body"), str) else "",
        "marker_line": str(parsed.get("marker_line") or ""),
    }


def is_material_content(content: Any) -> bool:
    """这件内容是不是一件**资料**（给 A 用：资料不能当可续写作品候选）。"""
    return parse_material_content(content) is not None


def build_material_content(*, text: str, source_kind: str, origin: str,
                           title: Optional[str] = None) -> str:
    """拼一件资料工件的内容：第一行资料标记，然后空行，然后是操作者交来的文本。"""
    if not isinstance(text, str) or not text.strip():
        raise ResearchContractRefused("text must be a non-empty string")
    if "\x00" in text:
        raise ResearchContractRefused("text must not contain NUL")
    clean_kind = _source_kind(source_kind)
    clean_origin = _one_line(origin, "origin", limit=MAX_ORIGIN_CHARS)
    payload: dict[str, Any] = {"source_kind": clean_kind, "origin": clean_origin}
    if isinstance(title, str) and title.strip():
        payload["title"] = _one_line(title, "title", limit=MAX_TITLE_CHARS)
    head = _marker_line(MATERIAL_MARKER, payload)
    body = text.replace("\r\n", "\n").replace("\r", "\n")
    return head + "\n\n" + body


# ---------------------------------------------------------------------------------------------
# 输入规范
# ---------------------------------------------------------------------------------------------


def _one_line(value: Any, name: str, *, limit: int) -> str:
    if not isinstance(value, str):
        raise ResearchContractRefused("%s must be a string" % name)
    collapsed = " ".join(part for part in value.replace("\r\n", "\n").splitlines()
                         if part.strip()).strip()
    if not collapsed:
        raise ResearchContractRefused("%s must not be blank" % name)
    if "\x00" in collapsed:
        raise ResearchContractRefused("%s must not contain NUL" % name)
    if len(collapsed) > int(limit):
        raise ResearchContractRefused("%s exceeds %d characters" % (name, int(limit)))
    return collapsed


def _text_or_empty(value: Any, name: str, *, limit: int) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ResearchContractRefused("%s must be a string" % name)
    text = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    if "\x00" in text:
        raise ResearchContractRefused("%s must not contain NUL" % name)
    if len(text) > int(limit):
        raise ResearchContractRefused("%s exceeds %d characters" % (name, int(limit)))
    return text


def _source_kind(value: Any) -> str:
    if not isinstance(value, str):
        raise ResearchContractRefused("source_kind must be a string")
    text = value.strip().lower()
    if text not in SOURCE_KINDS:
        raise ResearchContractRefused(
            "source_kind must be one of %s" % ",".join(SOURCE_KINDS))
    return text


def _canonical_id(value: Any) -> str:
    """owner 的 artifact id 规范化：``artifact:<hex>`` 与裸十六进制都认。"""
    if not isinstance(value, str) or not value.strip():
        raise ResearchContractRefused("artifact_id is required")
    text = value.strip()
    if len(text) > 200:
        raise ResearchContractRefused("artifact_id is too long")
    if not text.startswith("artifact:") and re.match(r"^[0-9a-fA-F]{8,64}$", text):
        return "artifact:" + text
    return text


def _operation_key(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResearchContractRefused("operation_key must be a non-empty string")
    text = value.strip()
    if len(text) > 128:
        raise ResearchContractRefused("operation_key is too long")
    return text


def _normalise_refs(source_refs: Any) -> list[dict[str, str]]:
    """校验调用方点名的来源：``[{artifact_id, source_revision}]``，最多 12 条。"""
    if source_refs is None or isinstance(source_refs, (str, bytes)):
        raise ResearchContractRefused("source_refs must be a list of {artifact_id, source_revision}")
    rows: list[dict[str, str]] = []
    seen: list[str] = []
    for item in source_refs:
        if isinstance(item, Mapping):
            unknown = sorted(set(item) - {"artifact_id", "source_revision"})
            if unknown:
                raise ResearchContractRefused("source_refs item has unknown keys: %s"
                                              % ",".join(unknown))
            artifact_id = _canonical_id(item.get("artifact_id"))
            raw_revision = item.get("source_revision")
            if raw_revision is None:
                revision = ""
            elif isinstance(raw_revision, str):
                revision = raw_revision.strip()
            else:
                raise ResearchContractRefused("source_revision must be a string")
        elif isinstance(item, str):
            artifact_id = _canonical_id(item)
            revision = ""
        else:
            raise ResearchContractRefused("source_refs items must be objects or id strings")
        if len(revision) > 200:
            raise ResearchContractRefused("source_revision is too long")
        if artifact_id in seen:
            continue
        seen.append(artifact_id)
        rows.append({"artifact_id": artifact_id, "source_revision": revision})
    if not rows:
        raise ResearchContractRefused("source_refs must name at least one material")
    if len(rows) > MAX_SOURCE_REFS:
        raise ResearchContractRefused("source_refs exceeds %d entries" % MAX_SOURCE_REFS)
    return rows


def _excerpt(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    collapsed = " ".join(part.strip() for part in str(text or "").splitlines() if part.strip())
    return collapsed[:int(limit)]


# ---------------------------------------------------------------------------------------------
# 服务
# ---------------------------------------------------------------------------------------------


class ResearchWorkspaceService:
    """列/读资料、导入资料；创建/列/读/续写/关闭研究作品。"""

    def __init__(
        self,
        projects: PrivateProjectService,
        *,
        reading_artifact_id: Optional[str] = None,
        private_items_artifact_id: Optional[str] = None,
    ) -> None:
        if projects is None:
            raise ResearchUnavailable("PROJECT_SERVICE_REQUIRED")
        if not isinstance(projects, PrivateProjectService):
            raise ResearchUnavailable("PROJECT_SERVICE_INVALID")
        self.projects = projects
        self.port = projects.port
        self.workspace_id = projects.workspace_id
        self.grant_id = projects.grant_id
        self.subject_id = projects.subject_id
        #: 既有阅读材料：**只读资料**，可检索、可引用，但导入永不覆盖它。
        self.reading_artifact_id = _optional_id(reading_artifact_id)
        #: 私人事项文档：既不是资料也不是研究，扫描时直接略过。
        self.private_items_artifact_id = _optional_id(private_items_artifact_id)

    # -- introspection -------------------------------------------------------------------

    def describe(self) -> dict[str, Any]:
        """这个服务接到了什么（不读 owner、不含任何私人文句）。"""
        return {
            "kind": "research_workspace_service",
            "source": SOURCE_OWNER,
            "subject_id": self.subject_id,
            "workspace_id": self.workspace_id,
            "grant_present": bool(self.grant_id),
            "reading_artifact_id": self.reading_artifact_id,
            "private_items_artifact_id": self.private_items_artifact_id,
            "research_marker": RESEARCH_MARKER,
            "material_marker": MATERIAL_MARKER,
            "source_kinds": list(SOURCE_KINDS),
            "limits": {
                "materials": MAX_MATERIALS,
                "research": MAX_RESEARCH,
                "source_refs": MAX_SOURCE_REFS,
                "scan": MAX_SCAN,
            },
        }

    # -- 资料 ----------------------------------------------------------------------------

    def list_materials(self, *, query: str = "", limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """列同 owner 的资料（含既有阅读材料），按最近更新排序。纯读。"""
        text = query if isinstance(query, str) else ""
        needle = text.strip().lower()
        cap = _limit(limit, MAX_MATERIALS)
        try:
            rows = self.port.list_artifacts()
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            return self._owner_failure(exc, reason="MATERIALS_UNAVAILABLE")
        except Exception as exc:
            return self._unavailable("MATERIALS_UNAVAILABLE", exc)
        scanned = 0
        materials: list[dict[str, Any]] = []
        skipped = {"research": 0, "private_items": 0, "other": 0}
        for row in rows or ():
            if not isinstance(row, Mapping) or not row.get("artifact_id"):
                continue
            artifact_id = str(row["artifact_id"])
            if self.private_items_artifact_id and artifact_id == self.private_items_artifact_id:
                skipped["private_items"] += 1
                continue
            if scanned >= MAX_SCAN:
                break
            scanned += 1
            try:
                view = self.port.read_artifact(artifact_id)
            except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError):
                # 一件读不出来的工件：跳过它，但绝不当成"资料读取成功"。
                skipped["other"] += 1
                continue
            except Exception:
                skipped["other"] += 1
                continue
            classified = self._classify(artifact_id, view)
            kind = classified.get("kind")
            if kind == KIND_RESEARCH:
                skipped["research"] += 1
                continue
            if kind not in (KIND_MATERIAL, KIND_READING_MATERIAL):
                skipped["other"] += 1
                continue
            if needle and needle not in self._haystack(classified):
                continue
            materials.append(self._material_row(classified))
        materials.sort(key=lambda item: (-float(item.get("sort_key") or 0.0),
                                         str(item.get("title") or "")))
        for item in materials:
            item.pop("sort_key", None)
        truncated = len(materials) > cap
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "data": {
                "materials": materials[:cap],
                "query": text.strip(),
                "count": len(materials[:cap]),
                "matched": len(materials),
                "truncated": bool(truncated),
                "scanned": scanned,
                "skipped": skipped,
                "reading_artifact_id": self.reading_artifact_id,
                "not_memory": True,
            },
        }

    def import_material(self, *, title: str, text: str, source_kind: str, origin: str,
                        operation_key: str) -> dict[str, Any]:
        """导入一件资料：owner 新建 artifact，正文原样保存。**永不覆盖既有材料**。"""
        clean_title = _one_line(title, "title", limit=MAX_TITLE_CHARS)
        if not isinstance(text, str) or not text.strip():
            raise ResearchContractRefused("text must be a non-empty string")
        if len(text) > MAX_MATERIAL_TEXT_CHARS:
            raise ResearchContractRefused("text exceeds %d characters" % MAX_MATERIAL_TEXT_CHARS)
        content = build_material_content(text=text, source_kind=source_kind, origin=origin,
                                         title=clean_title)
        if len(content) > MAX_CONTENT_CHARS:
            raise ResearchContractRefused("material content exceeds %d characters"
                                          % MAX_CONTENT_CHARS)
        key = _operation_key(operation_key)
        answer = self.projects.create_project(title=clean_title, content=content,
                                              operation_key=key)
        status = str(answer.get("status") or "")
        if not answer.get("ok") or status not in (STATUS_APPLIED, STATUS_ALREADY_APPLIED):
            return self._project_failure(answer, reason="MATERIAL_IMPORT_REFUSED")
        artifact_id = str(answer.get("artifact_id") or "")
        data: dict[str, Any] = {
            "operation_key": key,
            "version_id": answer.get("version_id"),
            "version_number": answer.get("version_number"),
            "head_version_id": answer.get("head_version_id") or answer.get("version_id"),
            "reconciled": bool(answer.get("reconciled")),
            "already_applied": status == STATUS_ALREADY_APPLIED,
            "title": clean_title,
            "source_kind": _source_kind(source_kind),
            "origin": _one_line(origin, "origin", limit=MAX_ORIGIN_CHARS),
            "not_memory": True,
        }
        material = None
        if artifact_id:
            fresh = self.read_material(artifact_id)
            if fresh.get("ok"):
                material = (fresh.get("data") or {}).get("material")
                data["head_version_id"] = (material or {}).get("source_revision") or \
                    data.get("head_version_id")
                data["revision"] = (material or {}).get("revision")
            else:
                data["material_read_reason"] = str(fresh.get("reason_code") or "")
        data["material"] = material or {
            "artifact_id": artifact_id, "title": clean_title,
            "source_kind": _source_kind(source_kind),
            "origin": _one_line(origin, "origin", limit=MAX_ORIGIN_CHARS),
            "source_revision": data.get("head_version_id") or "",
            "kind": KIND_MATERIAL, "read_only": False, "availability": "AVAILABLE",
            "excerpt": _excerpt(text), "text": None,
        }
        return {"ok": True, "status": status, "source": SOURCE_OWNER, "data": data}

    def read_material(self, artifact_id: str, *,
                      expected_revision: Any = None) -> dict[str, Any]:
        """读一件资料（含正文）。``expected_revision`` 不符就**具名拒绝**，不返回别人的正文。"""
        target = _canonical_id(artifact_id)
        try:
            view = self.port.read_artifact(target)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc, reason="MATERIAL_UNAVAILABLE")
            answer["data"]["artifact_id"] = target
            if str(getattr(exc, "reason", "") or "").upper() == "ARTIFACT_NOT_FOUND":
                answer["status"] = STATUS_NOT_FOUND
                answer["reason_code"] = "MATERIAL_NOT_FOUND"
                answer["ok"] = False
            return answer
        except Exception as exc:
            return self._unavailable("MATERIAL_UNAVAILABLE", exc, artifact_id=target)
        classified = self._classify(target, view)
        kind = classified.get("kind")
        if kind not in (KIND_MATERIAL, KIND_READING_MATERIAL):
            return {
                "ok": False,
                "status": STATUS_NOT_FOUND,
                "reason_code": ("ARTIFACT_IS_A_RESEARCH" if kind == KIND_RESEARCH
                                else "NOT_A_MATERIAL"),
                "source": SOURCE_OWNER,
                "data": {"artifact_id": target, "kind": kind,
                         "detail": "这件工件不是资料；研究作品请走 read_research。"},
            }
        current_revision = str(classified.get("source_revision") or "")
        mismatch = self._revision_mismatch(classified, expected_revision)
        if mismatch:
            return {
                "ok": False,
                "status": STATUS_CONFLICT,
                "reason_code": "MATERIAL_REVISION_MISMATCH",
                "source": SOURCE_OWNER,
                "data": {"artifact_id": target,
                         "expected_revision": mismatch[0],
                         "source_revision": current_revision,
                         "revision": classified.get("revision"),
                         "detail": mismatch[1]},
            }
        material = self._material_row(classified)
        material.pop("sort_key", None)
        # 正文是操作者交来的那段文本（标记已剥掉），不是整件工件的原始 content。
        material["text"] = (classified.get("body")
                            if isinstance(classified.get("body"), str) else "")
        material["content_available"] = isinstance(classified.get("body"), str)
        material["availability"] = self._availability(classified)
        material["not_memory"] = True
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "data": {"material": material,
                     "artifact_id": target,
                     "not_memory": True,
                     "note": "资料只是被保存下来的文本；origin 是来源声明，不是真实性认证。"},
        }

    # -- 研究 ----------------------------------------------------------------------------

    def create_research(self, *, question: str, source_refs: Iterable[Any],
                        title: Optional[str] = None, scope: str = "",
                        uncertainty: str = DEFAULT_UNCERTAINTY,
                        operation_key: str) -> dict[str, Any]:
        """从"一个问题 + 具体来源"建一件研究作品：先核对来源，再让 owner 建工件。"""
        clean_question = _one_line(question, "question", limit=MAX_QUESTION_CHARS)
        refs = _normalise_refs(source_refs)
        clean_scope = _text_or_empty(scope, "scope", limit=MAX_SCOPE_CHARS)
        clean_uncertainty = _text_or_empty(uncertainty, "uncertainty",
                                           limit=MAX_UNCERTAINTY_CHARS) or DEFAULT_UNCERTAINTY
        key = _operation_key(operation_key)
        checks = [self._source_check(row) for row in refs]
        unavailable = [row for row in checks if row["status"] == CHECK_UNAVAILABLE]
        if unavailable:
            return {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": "RESEARCH_SOURCE_UNAVAILABLE",
                "source": SOURCE_OWNER,
                "data": {"source_checks": checks,
                         "sources_available": False,
                         "detail": "至少一个来源现在读不到或已撤回；研究没有建立。"},
            }
        resolved_refs = [{"artifact_id": row["artifact_id"],
                          "source_revision": row["source_revision"]} for row in checks]
        clean_title = (_one_line(title, "title", limit=MAX_TITLE_CHARS)
                       if isinstance(title, str) and title.strip()
                       else _default_title(clean_question))
        body = _initial_body(clean_question, checks, clean_scope, clean_uncertainty)
        content = build_research_content(clean_question, resolved_refs, clean_scope,
                                         clean_uncertainty, body, clean_title)
        if len(content) > MAX_CONTENT_CHARS:
            raise ResearchContractRefused("research content exceeds %d characters"
                                          % MAX_CONTENT_CHARS)
        answer = self.projects.create_project(title=clean_title, content=content,
                                              operation_key=key)
        status = str(answer.get("status") or "")
        if not answer.get("ok") or status not in (STATUS_APPLIED, STATUS_ALREADY_APPLIED):
            return self._project_failure(answer, reason="RESEARCH_CREATE_REFUSED")
        artifact_id = str(answer.get("artifact_id") or "")
        data: dict[str, Any] = {
            "operation_key": key,
            "version_number": answer.get("version_number"),
            "reconciled": bool(answer.get("reconciled")),
            "already_applied": status == STATUS_ALREADY_APPLIED,
            "source_checks": checks,
            "not_memory": True,
        }
        project = None
        if artifact_id:
            fresh = self.read_research(artifact_id)
            if fresh.get("ok"):
                project = (fresh.get("data") or {}).get("project")
                data["source_checks"] = (project or {}).get("source_checks") or checks
            else:
                data["project_read_reason"] = str(fresh.get("reason_code") or "")
        data["project"] = project or {
            "artifact_id": artifact_id,
            "title": clean_title,
            "content": content,
            "question": clean_question,
            "source_refs": resolved_refs,
            "scope": clean_scope,
            "uncertainty": clean_uncertainty,
            "head_version_id": answer.get("head_version_id") or answer.get("version_id"),
            "source_checks": checks,
            "not_memory": True,
            "stable_belief": False,
        }
        return {"ok": True, "status": status, "source": SOURCE_OWNER, "data": data}

    def list_research(self, *, limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
        """列同 owner 的研究作品（有界读当前 head，不列正文）。"""
        cap = _limit(limit, MAX_RESEARCH)
        try:
            rows = self.port.list_artifacts()
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            return self._owner_failure(exc, reason="RESEARCH_UNAVAILABLE")
        except Exception as exc:
            return self._unavailable("RESEARCH_UNAVAILABLE", exc)
        scanned = 0
        projects: list[dict[str, Any]] = []
        for row in rows or ():
            if not isinstance(row, Mapping) or not row.get("artifact_id"):
                continue
            artifact_id = str(row["artifact_id"])
            if self.private_items_artifact_id and artifact_id == self.private_items_artifact_id:
                continue
            if scanned >= MAX_SCAN:
                break
            scanned += 1
            try:
                view = self.port.read_artifact(artifact_id)
            except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError):
                continue
            except Exception:
                continue
            classified = self._classify(artifact_id, view)
            if classified.get("kind") != KIND_RESEARCH:
                continue
            parsed = classified.get("parsed") or {}
            projects.append({
                "artifact_id": artifact_id,
                "title": classified.get("title") or parsed.get("title") or "未命名研究",
                "question": parsed.get("question") or "",
                "scope": parsed.get("scope") or "",
                "uncertainty": parsed.get("uncertainty") or "",
                "source_refs": parsed.get("source_refs") or [],
                "source_count": len(parsed.get("source_refs") or []),
                "lifecycle": classified.get("lifecycle"),
                "status": "archived" if classified.get("lifecycle") == "ARCHIVED" else "active",
                "revision": classified.get("revision"),
                "head_version_id": classified.get("source_revision"),
                "updated_at": classified.get("updated_at"),
                "not_memory": True,
                "stable_belief": False,
                "kind": KIND_RESEARCH,
                "_sort": float(classified.get("updated_at") or 0.0),
            })
        projects.sort(key=lambda item: (-item.get("_sort", 0.0), str(item.get("title") or "")))
        for item in projects:
            item.pop("_sort", None)
        truncated = len(projects) > cap
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "data": {
                "projects": projects[:cap],
                "count": len(projects[:cap]),
                "truncated": bool(truncated),
                "scanned": scanned,
                "not_memory": True,
                "note": NOT_MEMORY_NOTE,
            },
        }

    def read_research(self, artifact_id: str) -> dict[str, Any]:
        """读一件研究作品：正文、marker 里的引用，以及每个来源现在的真实版本状态。"""
        target = _canonical_id(artifact_id)
        try:
            view = self.port.read_artifact(target)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            answer = self._owner_failure(exc, reason="RESEARCH_UNAVAILABLE")
            answer["data"]["artifact_id"] = target
            if str(getattr(exc, "reason", "") or "").upper() == "ARTIFACT_NOT_FOUND":
                answer["status"] = STATUS_NOT_FOUND
                answer["reason_code"] = "RESEARCH_NOT_FOUND"
                answer["ok"] = False
            return answer
        except Exception as exc:
            return self._unavailable("RESEARCH_UNAVAILABLE", exc, artifact_id=target)
        classified = self._classify(target, view)
        kind = classified.get("kind")
        if kind != KIND_RESEARCH:
            return {
                "ok": False,
                "status": STATUS_NOT_FOUND,
                "reason_code": ("ARTIFACT_IS_A_MATERIAL" if kind in (KIND_MATERIAL,
                                                                    KIND_READING_MATERIAL)
                                else "NOT_A_RESEARCH"),
                "source": SOURCE_OWNER,
                "data": {"artifact_id": target, "kind": kind,
                         "detail": "这件工件不是研究作品；资料请走 read_material。"},
            }
        parsed = classified.get("parsed") or {}
        refs = parsed.get("source_refs") or []
        checks = [self._source_check(row) for row in refs]
        project = {
            "artifact_id": target,
            "title": classified.get("title") or parsed.get("title") or "未命名研究",
            "lifecycle": classified.get("lifecycle"),
            "status": "archived" if classified.get("lifecycle") == "ARCHIVED" else "active",
            "revision": classified.get("revision"),
            "head_version_id": classified.get("source_revision"),
            "version_number": classified.get("version_number"),
            "content": classified.get("text") if isinstance(classified.get("text"), str) else "",
            "content_chars": (len(classified["text"])
                              if isinstance(classified.get("text"), str) else 0),
            "content_available": isinstance(classified.get("text"), str),
            "question": parsed.get("question") or "",
            "source_refs": refs,
            "scope": parsed.get("scope") or "",
            "uncertainty": parsed.get("uncertainty") or DEFAULT_UNCERTAINTY,
            "body": parsed.get("body") or "",
            "source_checks": checks,
            "sources_current": all(row["status"] == CHECK_CURRENT for row in checks),
            "marker_present": bool(parsed.get("marker_present")),
            "marker_version": parsed.get("marker_version") or "",
            "not_memory": True,
            "stable_belief": False,
            "note": NOT_MEMORY_NOTE,
        }
        return {
            "ok": True,
            "status": STATUS_OK,
            "source": SOURCE_OWNER,
            "data": {"project": project, "source_checks": checks,
                     "not_memory": True, "note": NOT_MEMORY_NOTE},
        }

    def update_research(self, artifact_id: str, *, content: str, expected_head: str,
                        operation_key: str, scope: Optional[str] = None,
                        uncertainty: Optional[str] = None) -> dict[str, Any]:
        """续写一件研究作品：CAS 提交新版本，marker 与引用由 owner 里那份重建。"""
        target = _canonical_id(artifact_id)
        if not isinstance(expected_head, str) or not expected_head.strip():
            raise ResearchContractRefused(
                "expected_head is required: pass the head_version_id you read before editing")
        if not isinstance(content, str) or not content.strip():
            raise ResearchContractRefused("content must be a non-empty string")
        key = _operation_key(operation_key)
        current = self.read_research(target)
        if not current.get("ok"):
            return current
        project = (current.get("data") or {}).get("project") or {}
        if project.get("lifecycle") == "ARCHIVED":
            return {
                "ok": False,
                "status": STATUS_REJECTED,
                "reason_code": "RESEARCH_ARCHIVED",
                "source": SOURCE_OWNER,
                "data": {"artifact_id": target,
                         "detail": "这件研究已经关闭；内容与每个版本都在，但不再续写。"},
            }
        incoming = parse_research_content(content)
        body = incoming["body"] if incoming.get("marker_present") else content
        body = body.replace("\r\n", "\n").replace("\r", "\n")
        if "\x00" in body:
            raise ResearchContractRefused("content must not contain NUL")
        if len(body) > MAX_RESEARCH_BODY_CHARS:
            raise ResearchContractRefused("content exceeds %d characters" % MAX_RESEARCH_BODY_CHARS)
        resolved_scope = (_text_or_empty(scope, "scope", limit=MAX_SCOPE_CHARS)
                          if scope is not None else (project.get("scope") or ""))
        resolved_uncertainty = (_text_or_empty(uncertainty, "uncertainty",
                                              limit=MAX_UNCERTAINTY_CHARS)
                                if uncertainty is not None
                                else (project.get("uncertainty") or DEFAULT_UNCERTAINTY))
        rebuilt = build_research_content(
            project.get("question") or "",
            project.get("source_refs") or [],
            resolved_scope,
            resolved_uncertainty or DEFAULT_UNCERTAINTY,
            body,
            project.get("title") or None,
        )
        if len(rebuilt) > MAX_CONTENT_CHARS:
            raise ResearchContractRefused("research content exceeds %d characters"
                                          % MAX_CONTENT_CHARS)
        answer = self.projects.commit_project(artifact_id=target, content=rebuilt,
                                              expected_head=expected_head.strip(),
                                              operation_key=key)
        status = str(answer.get("status") or "")
        if not answer.get("ok") or status not in (STATUS_APPLIED, STATUS_ALREADY_APPLIED):
            return self._project_failure(answer, reason="RESEARCH_UPDATE_REFUSED",
                                         artifact_id=target)
        data: dict[str, Any] = {
            "operation_key": key,
            "version_id": answer.get("version_id"),
            "version_number": answer.get("version_number"),
            "head_version_id": answer.get("head_version_id") or answer.get("version_id"),
            "reconciled": bool(answer.get("reconciled")),
            "already_applied": status == STATUS_ALREADY_APPLIED,
            "marker_preserved": True,
            "source_refs_preserved": True,
            "not_memory": True,
        }
        fresh = self.read_research(target)
        if fresh.get("ok"):
            data["project"] = (fresh.get("data") or {}).get("project")
            data["source_checks"] = (fresh.get("data") or {}).get("source_checks") or []
        return {"ok": True, "status": status, "source": SOURCE_OWNER, "data": data}

    def close_research(self, artifact_id: str, *, expected_revision: int,
                       operation_key: str) -> dict[str, Any]:
        """关闭一件研究作品：复用 ``archive``，每个版本与正文都保留，不删除。"""
        target = _canonical_id(artifact_id)
        if isinstance(expected_revision, bool) or not isinstance(expected_revision, int) \
                or expected_revision < 1:
            raise ResearchContractRefused(
                "expected_revision must be the integer artifact revision read from the owner (>= 1)")
        key = _operation_key(operation_key)
        current = self.read_research(target)
        if not current.get("ok"):
            return current
        project = (current.get("data") or {}).get("project") or {}
        answer = self.projects.close_project(artifact_id=target,
                                             expected_revision=int(expected_revision),
                                             operation_key=key)
        status = str(answer.get("status") or "")
        if not answer.get("ok") or status not in (STATUS_APPLIED, STATUS_ALREADY_APPLIED):
            return self._project_failure(answer, reason="RESEARCH_CLOSE_REFUSED",
                                         artifact_id=target)
        fresh = self.read_research(target)
        closed = (fresh.get("data") or {}).get("project") if fresh.get("ok") else None
        return {
            "ok": True,
            "status": status,
            "source": SOURCE_OWNER,
            "data": {
                "artifact_id": target,
                "operation_key": key,
                "lifecycle": (closed or {}).get("lifecycle") or answer.get("lifecycle"),
                "revision": (closed or {}).get("revision", answer.get("revision")),
                "already_applied": status == STATUS_ALREADY_APPLIED,
                "content_kept": True,
                "question": project.get("question") or "",
                "title": project.get("title") or "",
                "versions_kept": True,
                "not_memory": True,
                "note": "关闭只是 archive：正文、每个版本和来源引用都保留，不删除。",
            },
        }

    # -- 分类 / 视图 ----------------------------------------------------------------------

    def _classify(self, artifact_id: str, view: Mapping[str, Any]) -> dict[str, Any]:
        """一件工件到底是资料、研究还是别的。只按第一行 marker 和 owner 的行判断。"""
        artifact = dict(view.get("artifact") or {}) if isinstance(view.get("artifact"),
                                                                  Mapping) else {}
        head = dict(view.get("head") or {}) if isinstance(view.get("head"), Mapping) else {}
        content = view.get("content")
        text = content if isinstance(content, str) else ""
        result: dict[str, Any] = {
            "artifact_id": artifact_id,
            "title": str(artifact.get("title") or ""),
            "lifecycle": str(artifact.get("lifecycle") or ""),
            "revision": artifact.get("revision"),
            "source_revision": head.get("version_id") or artifact.get("head_version_id"),
            "version_number": head.get("version_number"),
            "updated_at": artifact.get("updated_at"),
            "created_at": artifact.get("created_at"),
            "text": content if isinstance(content, str) else None,
            "parsed": None,
            "kind": KIND_UNKNOWN,
            "read_only": False,
            "source_kind": "",
            "origin": "",
            "body": "",
        }
        if self.reading_artifact_id and artifact_id == self.reading_artifact_id:
            result.update({"kind": KIND_READING_MATERIAL, "read_only": True,
                           "source_kind": SOURCE_KIND_READING,
                           "origin": "既有阅读材料（owner 的 reading workbench，只读）",
                           "body": text, "parsed": None})
            return result
        research = parse_research_content(content)
        if research.get("marker_present") and not research.get("parse_error"):
            result.update({"kind": KIND_RESEARCH, "parsed": research,
                           "body": research.get("body") or ""})
            return result
        material = parse_material_content(content)
        if material is not None:
            result.update({"kind": KIND_MATERIAL, "read_only": False,
                           "source_kind": material.get("source_kind") or "",
                           "origin": material.get("origin") or "",
                           "body": material.get("body") or ""})
            return result
        result["kind"] = KIND_UNKNOWN
        return result

    @staticmethod
    def _haystack(classified: Mapping[str, Any]) -> str:
        parts = [str(classified.get("title") or ""),
                 str(classified.get("body") or ""),
                 str(classified.get("origin") or ""),
                 str(classified.get("source_kind") or "")]
        return "\n".join(parts).lower()

    @staticmethod
    def _availability(classified: Mapping[str, Any]) -> str:
        if str(classified.get("lifecycle") or "") == "ARCHIVED":
            return "ARCHIVED"
        if classified.get("text") is None:
            return "UNAVAILABLE"
        return "AVAILABLE"

    @staticmethod
    def _material_row(classified: Mapping[str, Any]) -> dict[str, Any]:
        row = {
            "artifact_id": str(classified.get("artifact_id") or ""),
            "title": str(classified.get("title") or "") or "未命名资料",
            "source_revision": str(classified.get("source_revision") or ""),
            "source_kind": str(classified.get("source_kind") or ""),
            "origin": str(classified.get("origin") or ""),
            "excerpt": _excerpt(str(classified.get("body") or "")),
            "revision": classified.get("revision"),
            "version_number": classified.get("version_number"),
            "lifecycle": classified.get("lifecycle"),
            "availability": ResearchWorkspaceService._availability(classified),
            "kind": str(classified.get("kind") or KIND_UNKNOWN),
            "read_only": bool(classified.get("read_only")),
            "updated_at": classified.get("updated_at"),
            "not_memory": True,
            "sort_key": float(classified.get("updated_at") or 0.0),
        }
        return row

    @staticmethod
    def _revision_mismatch(classified: Mapping[str, Any],
                           expected: Any) -> Optional[tuple[Any, str]]:
        """``expected_revision`` 可以给 version id，也可以给 artifact revision 整数。"""
        if expected is None or expected == "":
            return None
        if isinstance(expected, bool):
            raise ResearchContractRefused("expected_revision must be a string or an integer")
        if isinstance(expected, int):
            if int(expected) < 1:
                raise ResearchContractRefused("expected_revision must be >= 1")
            current = classified.get("revision")
            if current is None or int(current) != int(expected):
                return expected, "owner 的 artifact revision 已经变了"
            return None
        if isinstance(expected, str):
            current = str(classified.get("source_revision") or "")
            if current and expected.strip() != current:
                return expected.strip(), "owner 的 head 已经变了"
            return None
        raise ResearchContractRefused("expected_revision must be a string or an integer")

    # -- 来源核对 ------------------------------------------------------------------------

    def _source_check(self, ref: Mapping[str, Any]) -> dict[str, Any]:
        """一个来源现在到底是什么状态：CURRENT / STALE / UNAVAILABLE。"""
        artifact_id = str(ref.get("artifact_id") or "")
        declared = str(ref.get("source_revision") or "")
        check: dict[str, Any] = {
            "artifact_id": artifact_id,
            "source_revision": declared,
            "status": CHECK_UNAVAILABLE,
            "reason_code": "",
            "title": "",
            "kind": KIND_UNKNOWN,
            "source_kind": "",
            "current_source_revision": None,
            "lifecycle": "",
            "checked": True,
        }
        try:
            view = self.port.read_artifact(artifact_id)
        except (LifeArtifactUnavailable, ArtifactOwnerRefused, LifeArtifactError) as exc:
            check["reason_code"] = str(getattr(exc, "reason", "") or type(exc).__name__).upper()[:64]
            return check
        except Exception as exc:
            check["reason_code"] = "SOURCE_READ_FAILED:%s" % type(exc).__name__
            return check
        classified = self._classify(artifact_id, view)
        kind = str(classified.get("kind") or KIND_UNKNOWN)
        current = str(classified.get("source_revision") or "")
        check.update({
            "title": str(classified.get("title") or ""),
            "kind": kind,
            "source_kind": str(classified.get("source_kind") or ""),
            "current_source_revision": current or None,
            "lifecycle": str(classified.get("lifecycle") or ""),
        })
        if str(classified.get("lifecycle") or "") == "ARCHIVED":
            check["reason_code"] = "SOURCE_ARCHIVED"
            return check
        if kind not in (KIND_MATERIAL, KIND_READING_MATERIAL):
            check["reason_code"] = "SOURCE_NOT_A_MATERIAL"
            return check
        if not current:
            check["reason_code"] = "SOURCE_REVISION_UNKNOWN"
            return check
        if not declared:
            # 调用方只点了 id、没点版本：这次核对出来的 head 就是它引用到的版本。
            check["source_revision"] = current
            check["status"] = CHECK_CURRENT
            check["reason_code"] = "CURRENT_AT_CREATION"
            return check
        if declared == current:
            check["status"] = CHECK_CURRENT
            check["reason_code"] = "CURRENT"
            return check
        check["status"] = CHECK_STALE
        check["reason_code"] = "SOURCE_REVISION_MOVED"
        return check

    # -- 答复 ----------------------------------------------------------------------------

    def _owner_failure(self, exc: Exception, *, reason: str) -> dict[str, Any]:
        if type(exc).__name__ == "LifeArtifactUnavailable":
            return {"ok": False, "status": STATUS_UNAVAILABLE,
                    "reason_code": "SUPPLY_UNAVAILABLE",
                    "source": SOURCE_OWNER,
                    "data": {"reason": reason, "detail": str(exc)[:200]}}
        owner_status = str(getattr(exc, "status", "") or "")
        owner_reason = str(getattr(exc, "reason", "") or "") or owner_status or reason
        detail = str(getattr(exc, "detail", "") or str(exc))[:200]
        status = STATUS_NOT_FOUND if owner_status == "NOT_FOUND" else STATUS_UNAVAILABLE
        return {"ok": False, "status": status,
                "reason_code": owner_reason.upper()[:64],
                "source": SOURCE_OWNER,
                "data": {"owner_status": owner_status, "detail": detail,
                         "reason": reason}}

    @staticmethod
    def _unavailable(reason: str, exc: Exception, **extra: Any) -> dict[str, Any]:
        data: dict[str, Any] = {"error_type": type(exc).__name__}
        data.update(extra)
        return {"ok": False, "status": STATUS_UNAVAILABLE,
                "reason_code": str(reason), "source": SOURCE_OWNER, "data": data}

    @staticmethod
    def _project_failure(answer: Mapping[str, Any], *, reason: str,
                         **extra: Any) -> dict[str, Any]:
        status = str(answer.get("status") or "")
        mapped = {
            STATUS_APPLIED: STATUS_APPLIED,
            STATUS_ALREADY_APPLIED: STATUS_ALREADY_APPLIED,
            STATUS_CONFLICT: STATUS_CONFLICT,
            STATUS_NOT_FOUND: STATUS_NOT_FOUND,
            STATUS_REJECTED: STATUS_REJECTED,
            STATUS_UNKNOWN: STATUS_UNKNOWN,
            STATUS_UNAVAILABLE: STATUS_UNAVAILABLE,
            STATUS_OK: STATUS_OK,
        }.get(status, STATUS_UNAVAILABLE)
        data: dict[str, Any] = {
            "reason": reason,
            "owner_status": answer.get("owner_status") or status,
            "detail": str(answer.get("detail") or "")[:200],
        }
        for key in ("artifact_id", "operation_key", "effect_state", "reconciled",
                    "retryable", "version_id", "version_number", "head_version_id"):
            if answer.get(key) is not None:
                data[key] = answer[key]
        data.update(extra)
        return {
            "ok": False,
            "status": mapped,
            "reason_code": str(answer.get("reason_code") or reason).upper()[:64],
            "source": SOURCE_OWNER,
            "data": data,
        }


# ---------------------------------------------------------------------------------------------
# 内部小工具
# ---------------------------------------------------------------------------------------------


def _optional_id(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        return ""
    try:
        return _canonical_id(value)
    except ResearchContractRefused:
        return ""


def _limit(value: Any, maximum: int) -> int:
    if value is None:
        return DEFAULT_LIMIT
    if isinstance(value, bool) or not isinstance(value, int):
        raise ResearchContractRefused("limit must be an integer")
    return max(1, min(int(value), int(maximum)))


def _default_title(question: str) -> str:
    seed = "研究：" + question
    return seed[:MAX_TITLE_CHARS]


def _initial_body(question: str, checks: list[dict[str, Any]], scope: str,
                  uncertainty: str) -> str:
    """第一版正文：只写"问题 / 范围 / 引用 / 未知"，绝不假装已经读过任何一页。"""
    lines: list[str] = []
    lines.append("## 问题")
    lines.append(question)
    lines.append("")
    lines.append("## 暂定范围")
    lines.append(scope.strip() or "尚未划定：这一份只从这个问题开始。")
    lines.append("")
    lines.append("## 已知来源（只是引用，不是「已经读完」的证明）")
    for row in checks:
        lines.append("- %s @ %s（%s）" % (row.get("artifact_id") or "",
                                          row.get("source_revision") or "",
                                          row.get("status") or CHECK_UNAVAILABLE))
    lines.append("")
    lines.append("## 暂定发现")
    lines.append("还没有：这一份研究还没有属于自己的发现，页面上的引用不能算读过。")
    lines.append("")
    lines.append("## 未知")
    lines.append(uncertainty.strip() or DEFAULT_UNCERTAINTY)
    return "\n".join(lines)


# ---------------------------------------------------------------------------------------------
# 环境工厂（A 的接缝）
# ---------------------------------------------------------------------------------------------

#: 既有阅读材料 / 私人事项文档在环境里的名字。第一个非空即用。
ENV_READING_NAMES = ("NYAIRO_PHONE_READING_ARTIFACT", "NYAIRO_LIFE_READING_ARTIFACT_ID",
                     "NYAIRO_READING_ARTIFACT_ID", "LIFE_PHONE_READING_ARTIFACT_ID")
ENV_PRIVATE_ITEMS_NAMES = ("NYAIRO_LIFE_PRIVATE_ITEMS_ARTIFACT_ID",
                           "NYAIRO_PRIVATE_ITEMS_ARTIFACT_ID",
                           "NYAIRO_PHONE_PRIVATE_ITEMS_ARTIFACT",
                           "LIFE_PHONE_PRIVATE_ITEMS_ARTIFACT_ID")


def _first_env(environment: Mapping[str, str], names: Iterable[str]) -> Optional[str]:
    for name in names:
        value = environment.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def research_from_env(port: Any = None, env: Optional[Mapping[str, str]] = None
                      ) -> Optional[ResearchWorkspaceService]:
    """按环境里的同一套 bindings 建一个可用的研究工作台；不成就返回 ``None``。

    ``port`` 可以是一个现成的 ``LifeArtifactPort``（调用方已经建好），也可以是 socket 路径；
    省略时按 ``private_projects_from_env`` 的同一套 env 名建一个。workspace / grant / subject
    缺一件就返回 ``None``——半接好的 owner 比没有更坏。
    """
    environment: dict[str, str] = dict(env or {})
    try:
        workspace_id = _first_env(environment, ENV_WORKSPACE_NAMES)
        grant_id = _first_env(environment, ENV_GRANT_NAMES)
        subject_id = _first_env(environment, ENV_SUBJECT_NAMES)
        if not workspace_id or not grant_id or not subject_id:
            return None
        resolved = port
        if resolved is None or isinstance(resolved, str):
            if isinstance(resolved, str) and resolved.strip():
                from life_artifact_port import LifeArtifactPort

                resolved = LifeArtifactPort(resolved.strip(), subject_id)
            else:
                resolved = life_supply_port_from_env(environment)
        reading = _first_env(environment, ENV_READING_NAMES)
        items = _first_env(environment, ENV_PRIVATE_ITEMS_NAMES)
        excluded = default_excluded_artifact_ids(reading, items)
        projects = PrivateProjectService(resolved, workspace_id, grant_id,
                                         excluded_artifact_ids=excluded)
        return ResearchWorkspaceService(projects, reading_artifact_id=reading,
                                        private_items_artifact_id=items)
    except (ProjectUnavailable, ProjectContractRefused, LifeArtifactError,
            ResearchContractRefused, ResearchUnavailable):
        return None
    except Exception:
        # 形态不对的 port / env：宁可回答"没有可用研究服务"，也不升一个半坏的上来。
        return None


#: A 可能用别的名字找同一个工厂。
research_service_from_env = research_from_env
research_workspace_from_env = research_from_env
from_env = research_from_env


__all__ = [
    "CHECK_CURRENT",
    "CHECK_STALE",
    "CHECK_UNAVAILABLE",
    "DEFAULT_LIMIT",
    "DEFAULT_UNCERTAINTY",
    "ENV_PRIVATE_ITEMS_NAMES",
    "ENV_READING_NAMES",
    "KIND_MATERIAL",
    "KIND_PROJECT",
    "KIND_READING_MATERIAL",
    "KIND_RESEARCH",
    "KIND_UNKNOWN",
    "MARKER_CLOSE",
    "MARKER_OPEN",
    "MATERIAL_MARKER",
    "MAX_EXCERPT_CHARS",
    "MAX_MATERIALS",
    "MAX_MATERIAL_TEXT_CHARS",
    "MAX_ORIGIN_CHARS",
    "MAX_QUESTION_CHARS",
    "MAX_RESEARCH",
    "MAX_RESEARCH_BODY_CHARS",
    "MAX_SCOPE_CHARS",
    "MAX_SOURCE_REFS",
    "MAX_UNCERTAINTY_CHARS",
    "NOT_MEMORY_NOTE",
    "RESEARCH_MARKER",
    "ResearchContractRefused",
    "ResearchUnavailable",
    "ResearchWorkspaceError",
    "ResearchWorkspaceService",
    "SOURCE_KINDS",
    "SOURCE_KIND_FICTION",
    "SOURCE_KIND_READING",
    "SOURCE_KIND_USER",
    "build_material_content",
    "build_research_content",
    "from_env",
    "is_material_content",
    "is_research_content",
    "parse_material_content",
    "parse_research_content",
    "research_from_env",
    "research_service_from_env",
    "research_workspace_from_env",
]
