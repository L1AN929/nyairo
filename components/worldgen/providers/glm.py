#!/usr/bin/env python3
"""GLM（OpenAI 兼容 chat/completions API）内容丰富器。

职责边界：

* LLM **只**生成文本内容（地点介绍、氛围细节、NPC 公开背景、节庆候选），
  输出经结构校验后写入 ``content.json``；
* LLM **不**决定地图拓扑、连接关系、营业时间、NPC 出场时间等任何
  程序权威数据，也不能触达运行时状态；
* 无 API Key 时，生成器完全使用本地模板内容，功能不受影响；
* 生成结果保存进世界包，加载世界时不调用模型。

环境变量：

* ``WORLDGEN_LLM_API_KEY``   必填（未设置时本模块不可用）
* ``WORLDGEN_LLM_BASE_URL``  默认 https://open.bigmodel.cn/api/paas/v4
* ``WORLDGEN_LLM_MODEL``     默认 glm-5.3-flash
"""

from __future__ import annotations

import json
import os
import re
import urllib.error
from pathlib import Path
import urllib.request
from typing import Any, Mapping, Optional

from .. import schema

DEFAULT_BASE_URL = "https://open.bigmodel.cn/api/paas/v4"
DEFAULT_MODEL = "glm-5.3-flash"
REQUEST_TIMEOUT_SECONDS = 120
MAX_ENTRIES = 64


class GLMProviderError(ValueError):
    """GLM 内容生成失败。失败时生成器会回退到本地模板内容。"""


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.environ.get(name)
    return value.strip() if value and value.strip() else default


def is_configured(environment: Optional[Mapping[str, str]] = None) -> bool:
    env = environment if environment is not None else os.environ
    return bool(str(env.get("WORLDGEN_LLM_API_KEY") or "").strip())


def _build_prompt(docs: Mapping[str, Any]) -> str:
    world = docs["world.json"]
    topology = docs["topology.json"]
    rules = docs["rules.json"]
    npcs = docs["npcs.json"]["npcs"]
    location_lines = []
    for location in topology["locations"]:
        rule = rules["location_rules"].get(location["location_id"], {})
        hours = (
            f"{rule['open_hour']}点到{rule['close_hour']}点"
            if rule.get("open_hour") is not None
            else "全天开放"
        )
        weekdays = "（周末休息）" if rule.get("weekdays_only") else ""
        location_lines.append(
            f"- id={location['location_id']} 名称={location['name']} "
            f"类型={location['kind']} 营业={hours}{weekdays}"
        )
    npc_lines = [
        f"- id={npc['npc_id']} 姓名={npc['display_name']} 身份={npc['public_role']} "
        f"常在={npc['home_base_area']}"
        for npc in npcs
    ]
    location_ids = [location["location_id"] for location in topology["locations"]]
    npc_ids = [npc["npc_id"] for npc in npcs]
    return f"""你是一座虚拟小镇的世界设定师。小镇信息：
名称：{world['name']}（主题：{world['theme']}，种子：{world['seed']}）

地点列表：
{chr(10).join(location_lines)}

公共角色列表：
{chr(10).join(npc_lines) if npc_lines else "（无）"}

请只输出一个 JSON 对象（不要 markdown 代码块），结构如下：
{{
  "entries": {{
    "world": {{"tagline": "一句话概括小镇的生活气息，60字以内", "festivals": ["当地节庆候选1", "节庆候选2"]}},
    "<地点id>": {{"intro": "地点介绍，40-80字，自然、克制、有生活气息", "flavor": "一个环境细节，20字以内"}},
    "<npc_id>": {{"public_note": "这个公共角色的公开背景一句话，30字以内"}}
  }},
  "ambient_candidates": [{{"summary": "与当地生活相关的定期公共活动或环境事实，30字以内", "kind_hint": "shop/school/park/home/street/public 之一"}}]
}}

要求：
1. entries 的键必须原样使用上面列表中 id= 后面的标识符（如 bookstore、npc_xxx），
   绝对不要用地点或人物的中文名称作键。
2. 每个地点都要有 intro；街道和公园等公共场所还要有 flavor。
3. ambient_candidates 最多 4 条，是"定期发生的环境事实"候选（例如书店定期整理旧书、
   咖啡馆推出季节饮品），不要写已经发生的具体事件，不要戏剧化。
4. 内容要普通、日常、可信：允许平凡的店铺和没有事件的日子，不要悲惨过去、秘密组织或戏剧化设定。
5. 不要使用 markdown，不要输出 JSON 以外的任何文字。"""


def _extract_json(text: str) -> dict[str, Any]:
    """通用模型输出提取：剥围栏、剥离前后缀文字，解析 JSON 对象。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```[a-zA-Z]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        # 真实模型常在 JSON 前后加说明文字：截取首尾大括号之间再试。
        first, last = cleaned.find("{"), cleaned.rfind("}")
        if first == -1 or last <= first:
            raise GLMProviderError(f"模型输出不是有效 JSON: {cleaned[:120]}") from None
        try:
            parsed = json.loads(cleaned[first : last + 1])
        except json.JSONDecodeError as exc:
            raise GLMProviderError(f"模型输出不是有效 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise GLMProviderError("模型输出必须是 JSON 对象")
    return parsed


def _extract_enrich_payload(text: str) -> dict[str, Any]:
    """enrich 输出提取：要求包含 entries 对象。"""
    parsed = _extract_json(text)
    if not isinstance(parsed.get("entries"), dict):
        raise GLMProviderError("模型输出缺少 entries 对象")
    return parsed


_AMBIENT_KIND_HINTS = ("shop", "school", "park", "home", "street", "public")


def _extract_ambient_candidates(parsed: Mapping[str, Any]) -> list[dict[str, str]]:
    """提取环境事件候选（受限词汇：kind_hint 白名单，摘要限长，最多 4 条）。"""
    raw = parsed.get("ambient_candidates")
    if not isinstance(raw, list):
        return []
    candidates: list[dict[str, str]] = []
    for item in raw[:6]:
        if not isinstance(item, Mapping):
            continue
        summary = str(item.get("summary") or "").strip()[:240]
        hint = str(item.get("kind_hint") or "public").strip()
        if not summary or hint not in _AMBIENT_KIND_HINTS:
            continue
        candidates.append({"summary": summary, "kind_hint": hint})
    return candidates[:4]


def _request_chat(
    *,
    base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    timeout: float,
) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你是一个谨慎的虚拟世界文本生成器，只输出 JSON。"},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.8,
        "max_tokens": 4096,
    }
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            # 部分网关（如 Cloudflare 防护）会拦截默认的 Python-urllib 签名。
            "User-Agent": "nyairo-worldgen/0.3",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise GLMProviderError(f"GLM API 返回 HTTP {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise GLMProviderError(f"GLM API 请求失败: {exc}") from exc
    usage = body.get("usage") if isinstance(body.get("usage"), dict) else {}
    usage_record = {
        "model": body.get("model") or payload["model"],
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }
    try:
        content = body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise GLMProviderError(f"GLM API 响应结构异常: {exc}") from exc
    return content, usage_record


def _merge_entries(docs: Mapping[str, Any], entries: Mapping[str, Any]) -> dict[str, Any]:
    """把模型输出合并进 content.entries；未知键丢弃，不进入世界包。

    模型常用中文名称代替 id 作键：先按"名称 → id"做一次宽容重映射
    （名称必须与地点/角色真实名称精确一致，不构成发明），映射后仍未知
    的键按错误报告。
    """
    name_to_id: dict[str, str] = {}
    for loc in docs["topology.json"]["locations"]:
        name_to_id[loc["name"]] = loc["location_id"]
    for npc in docs["npcs.json"]["npcs"]:
        name_to_id[npc["display_name"]] = npc["npc_id"]
    known = set(name_to_id.values()) | {"world"}
    merged: dict[str, Any] = {}
    skipped: list[str] = []
    for key, entry in entries.items():
        if key in known:
            merged[key] = entry
            continue
        remapped = name_to_id.get(str(key).strip())
        if remapped is not None:
            merged[remapped] = entry
            continue
        skipped.append(key)
    if "world" not in merged:
        raise GLMProviderError("模型输出缺少 world 条目")
    location_ids = {loc["location_id"] for loc in docs["topology.json"]["locations"]}
    missing = location_ids - set(merged)
    if missing:
        raise GLMProviderError(f"模型输出缺少地点介绍: {sorted(missing)}")
    if skipped:
        # 未知 id 不写进包，但在错误信息里可见，便于排查。
        raise GLMProviderError(f"模型输出了未知 id: {sorted(skipped)}")
    return merged


def enrich(
    docs: Mapping[str, Any],
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> dict[str, Any]:
    """调用 GLM 生成内容，返回 {"content": 文档, "ambient_candidates": [...], "usage": {...}}。

    任何失败都以 GLMProviderError 报告；调用方决定是否回退本地内容。
    """
    resolved_base = base_url or _env("WORLDGEN_LLM_BASE_URL") or DEFAULT_BASE_URL
    resolved_key = api_key or _env("WORLDGEN_LLM_API_KEY") or ""
    resolved_model = model or _env("WORLDGEN_LLM_MODEL") or DEFAULT_MODEL
    if not resolved_key:
        raise GLMProviderError(
            "未设置 WORLDGEN_LLM_API_KEY，GLM 内容生成不可用；"
            "请使用本地模板内容或设置 API Key 后重试"
        )
    text, usage = _request_chat(
        base_url=resolved_base,
        api_key=resolved_key,
        model=resolved_model,
        prompt=_build_prompt(docs),
        timeout=timeout,
    )
    parsed = _extract_enrich_payload(text)
    entries = _merge_entries(docs, parsed["entries"])
    if len(entries) > MAX_ENTRIES:
        raise GLMProviderError(f"模型输出条目过多: {len(entries)}")
    content_doc = {
        "schema_version": schema.CONTENT_SCHEMA,
        "source": "glm",
        "model": resolved_model,
        "entries": entries,
    }
    return {
        "content": schema.validate_content_doc(content_doc),
        "ambient_candidates": _extract_ambient_candidates(parsed),
        "usage": usage,
    }


def append_usage_log(package_dir, usage: Mapping[str, Any], *, purpose: str) -> Optional[Path]:
    """把一次 GLM 调用的用量追加到世界包目录的 ``glm-usage.jsonl``。

    用量日志是运行记录而非世界数据：不纳入 MANIFEST，不参与校验；
    记录模型名与 Token 用量（成本可按服务商单价折算）。
    """
    if not usage:
        return None
    entry = {
        "timestamp": _timestamp_now(),
        "purpose": purpose,
        **{key: usage[key] for key in ("model", "prompt_tokens", "completion_tokens", "total_tokens") if key in usage},
    }
    log_path = Path(package_dir) / "glm-usage.jsonl"
    with log_path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return log_path


def _timestamp_now() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def llm_check(
    *,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """真实连通性自检：一次最小调用，返回内容与用量（供验收 A 使用）。"""
    resolved_base = base_url or _env("WORLDGEN_LLM_BASE_URL") or DEFAULT_BASE_URL
    resolved_key = api_key or _env("WORLDGEN_LLM_API_KEY") or ""
    resolved_model = model or _env("WORLDGEN_LLM_MODEL") or DEFAULT_MODEL
    if not resolved_key:
        raise GLMProviderError(
            "未设置 WORLDGEN_LLM_API_KEY；请设置后运行本自检，"
            "或使用 WORLDGEN_LLM_BASE_URL / WORLDGEN_LLM_MODEL 指定网关与模型"
        )
    content, usage = _request_chat(
        base_url=resolved_base,
        api_key=resolved_key,
        model=resolved_model,
        prompt="用一句不超过二十字的中文回答：你是什么模型？",
        timeout=timeout,
    )
    return {
        "status": "ok",
        "base_url": resolved_base,
        "model": usage.get("model", resolved_model),
        "reply": content.strip()[:200],
        "usage": usage,
    }


def design_interior(
    *,
    building_label: str,
    location_name: str,
    theme_desc: str,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """GLM 语义设计：回答"这栋建筑该有什么功能区/家具组合/风格/细节"。

    程序负责几何与合法性；本函数只产出**受限词汇**的语义建议：

    * ``furniture[].kind`` 必须来自编辑器九类家具（由调用方再次校验）；
    * 输出不包含任何坐标、ID 或动作能力——那些由程序决定。

    返回 ``(design, usage)``；失败以 GLMProviderError 报告。
    """
    resolved_base = base_url or _env("WORLDGEN_LLM_BASE_URL") or DEFAULT_BASE_URL
    resolved_key = api_key or _env("WORLDGEN_LLM_API_KEY") or ""
    resolved_model = model or _env("WORLDGEN_LLM_MODEL") or DEFAULT_MODEL
    if not resolved_key:
        raise GLMProviderError("未设置 WORLDGEN_LLM_API_KEY，GLM 语义设计不可用")
    prompt = f"""你是虚拟小镇的室内设计师。小镇背景：{theme_desc}
建筑：{location_name}（类型：{building_label}）。

可用的家具种类只有：bed(床)、book(书)、cabinet(柜/架)、chair(椅)、cup(杯)、dispenser(饮水机/咖啡机)、lamp(灯)、plant(盆栽)、table(桌)。

请只输出一个 JSON 对象（不要 markdown）：
{{
  "style": "这间场所的风格与经营特色，40字以内",
  "furniture": [{{"kind": "家具种类", "count": 建议数量1-6, "purpose": "用途说明20字以内"}}],
  "details": "能体现当地生活的可观察细节，60字以内",
  "activities": ["这里可能发生的普通日常活动，每条30字以内"]
}}

要求：furniture 最多 8 条、kind 只能用上面列出的种类；activities 最多 4 条；
内容要普通、日常、可信；不要输出 JSON 以外的任何文字。"""
    text, usage = _request_chat(
        base_url=resolved_base,
        api_key=resolved_key,
        model=resolved_model,
        prompt=prompt,
        timeout=timeout,
    )
    design = _extract_json(text)
    normalized = _validate_design(design)
    return normalized, usage


def _validate_design(design: Any) -> dict[str, Any]:
    """语义设计校验：受限词汇与边界；越界内容丢弃而非静默采纳。"""
    from ..interior import DESIGNABLE_KINDS

    if not isinstance(design, dict):
        raise GLMProviderError("设计输出必须是 JSON 对象")

    def text_field(value: Any, limit: int) -> str:
        if not isinstance(value, str):
            return ""
        return value.strip()[:limit]

    style = text_field(design.get("style"), 80)
    details = text_field(design.get("details"), 240)
    furniture: list[dict[str, Any]] = []
    raw_furniture = design.get("furniture")
    if isinstance(raw_furniture, list):
        for item in raw_furniture[:10]:
            if not isinstance(item, dict):
                continue
            kind = item.get("kind")
            if kind not in DESIGNABLE_KINDS:
                continue  # 模型发明的家具种类直接丢弃
            count = item.get("count")
            if isinstance(count, bool) or not isinstance(count, int):
                count = 1
            count = max(1, min(6, count))
            furniture.append(
                {"kind": kind, "count": count, "purpose": text_field(item.get("purpose"), 60),
                 "label": text_field(item.get("label"), 20)}
            )
    activities: list[str] = []
    raw_activities = design.get("activities")
    if isinstance(raw_activities, list):
        for item in raw_activities:
            text = text_field(item, 120)
            if text:
                activities.append(text)
    activities = activities[:4]
    if not furniture and not activities and not style and not details:
        raise GLMProviderError("设计输出为空或全部越界")
    return {"style": style, "furniture": furniture, "details": details, "activities": activities}


def propose_world(
    *,
    theme: str,
    theme_name: str,
    seed: Optional[int] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = REQUEST_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """世界设计提案（v0.4 两阶段生成的第一步）。

    GLM 根据主题提出：地区整体风格、建筑用途组合倾向、生活设施分布、
    地方文化、公共活动类型与不同区域的生活特点。**程序**据此生成合法的
    空间结构、地点和规则——提案不承担拓扑权威，也不能发明不存在的
    地点槽位（未知槽位建议丢弃）。

    返回 ``(proposal, usage)``。
    """
    resolved_base = base_url or _env("WORLDGEN_LLM_BASE_URL") or DEFAULT_BASE_URL
    resolved_key = api_key or _env("WORLDGEN_LLM_API_KEY") or ""
    resolved_model = model or _env("WORLDGEN_LLM_MODEL") or DEFAULT_MODEL
    if not resolved_key:
        raise GLMProviderError("未设置 WORLDGEN_LLM_API_KEY，世界设计提案不可用")
    prompt = f"""你是虚拟小镇的总体设计师。主题：{theme_name}（{theme}）。

请只输出一个 JSON 对象（不要 markdown），结构如下：
{{
  "style": "地区整体风格与气质，50字以内",
  "culture": "地方文化与生计特点，50字以内",
  "building_emphasis": {{"<槽位id>": 1-3 的权重数字}},
  "facility_tendencies": ["生活设施分布倾向，每条25字以内"],
  "public_activities": ["当地公共活动候选，每条30字以内"],
  "area_life": {{"住宅区": "生活特点20字", "商业街": "…"}},
  "npc_rhythm": "居民作息与公共生活的总体描述，40字以内"
}}

要求：building_emphasis 的键只能用这些槽位 id：home、school、bookstore、cafe、
convenience_store、bakery、park、library、shrine、station、market、fish_market、
beach、pier、lighthouse、community_center、home_goods_store、playground（可只写
部分）；public_activities 最多 4 条且是"定期发生的普通活动"；内容普通、日常、
可信；不要输出 JSON 以外的任何文字。"""
    text, usage = _request_chat(
        base_url=resolved_base,
        api_key=resolved_key,
        model=resolved_model,
        prompt=prompt,
        timeout=timeout,
    )
    parsed = _extract_json(text)
    proposal = _validate_proposal(parsed)
    return proposal, usage


def _validate_proposal(parsed: dict[str, Any]) -> dict[str, Any]:
    """提案校验：受限词汇与边界；越界内容丢弃。"""
    KNOWN_SLOTS = {
        "home", "school", "bookstore", "cafe", "convenience_store", "bakery",
        "park", "library", "shrine", "station", "market", "fish_market",
        "beach", "pier", "lighthouse", "community_center", "home_goods_store",
        "playground",
    }

    def text_field(value: Any, limit: int) -> str:
        return value.strip()[:limit] if isinstance(value, str) else ""

    building_emphasis: dict[str, int] = {}
    raw_emphasis = parsed.get("building_emphasis")
    if isinstance(raw_emphasis, dict):
        for slot, weight in raw_emphasis.items():
            if slot in KNOWN_SLOTS and isinstance(weight, int) and not isinstance(weight, bool):
                building_emphasis[slot] = max(1, min(3, weight))
    activities: list[str] = []
    raw_activities = parsed.get("public_activities")
    if isinstance(raw_activities, list):
        for item in raw_activities[:6]:
            text = text_field(item, 120)
            if text:
                activities.append(text)
    activities = activities[:4]
    area_life: dict[str, str] = {}
    raw_area_life = parsed.get("area_life")
    if isinstance(raw_area_life, dict):
        for zone, desc in list(raw_area_life.items())[:6]:
            text = text_field(desc, 60)
            if text and isinstance(zone, str):
                area_life[zone.strip()[:20]] = text
    tendencies: list[str] = []
    raw_tendencies = parsed.get("facility_tendencies")
    if isinstance(raw_tendencies, list):
        for item in raw_tendencies[:4]:
            text = text_field(item, 80)
            if text:
                tendencies.append(text)
    proposal = {
        "style": text_field(parsed.get("style"), 120),
        "culture": text_field(parsed.get("culture"), 120),
        "building_emphasis": building_emphasis,
        "facility_tendencies": tendencies,
        "public_activities": activities,
        "area_life": area_life,
        "npc_rhythm": text_field(parsed.get("npc_rhythm"), 100),
    }
    if not any(proposal.values()):
        raise GLMProviderError("提案输出为空或全部越界")
    return proposal


__all__ = [
    "DEFAULT_BASE_URL",
    "DEFAULT_MODEL",
    "GLMProviderError",
    "append_usage_log",
    "design_interior",
    "enrich",
    "is_configured",
    "llm_check",
    "propose_world",
]
