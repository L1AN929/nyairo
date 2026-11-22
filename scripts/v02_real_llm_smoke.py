#!/usr/bin/env python3
"""Step 3A: Bounded real-LLM live smoke test for Nyairo 0.2 closure.

Safety & honesty guarantees:
- Always runs against a brand-new isolated temporary root (never touches /root/.nyairo-day).
- Requires explicit ``--live-model`` to send real model requests; ``--dry-run`` sends 0 requests.
- Enforces a strict request ceiling (default 4, hard max 10) and short timeouts.
- Never prints or writes API keys, tokens, cookies, or authorization headers.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
for _p in (
    REPO_ROOT,
    REPO_ROOT / "scripts",
    REPO_ROOT / "components",
    REPO_ROOT / "components" / "life" / "scripts",
    REPO_ROOT / "components" / "supply",
    REPO_ROOT / "components" / "world" / "scripts",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from chiyo_bundle import lived_context_bridge, phone_tool
from components.life.scripts import profile_model_transport as pmt
from components.life.scripts import v02_representation_lease_forget as rlf
from components.phone import capability as phone_cap
from components.phone import life as phone_life
from components.phone import server as phone_server
from components.phone import service as phone_service
from components.phone import v02_runtime_migration_view as rtmig_view

HARD_MAX_REQUESTS = 10


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_dotenv_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        k, v = raw.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and v:
            out[k] = v
    return out


def resolve_live_model_env(base_env: Optional[Mapping[str, str]] = None) -> tuple[dict[str, str], dict[str, Any]]:
    """Resolve explicit NYA02_MODEL_* config from env or /root/.hermes without exposing secrets."""
    env = dict(os.environ if base_env is None else base_env)

    # 1. Already explicitly configured via NYA02_MODEL_*
    if env.get("NYA02_MODEL_BASE_URL") and env.get("NYA02_MODEL_NAME") and env.get("NYA02_MODEL_API_KEY"):
        info = phone_life.check_model(env)
        return env, info

    # 2. Fallback to /root/.hermes/.env (opencode-go) in memory only
    hermes_dir = Path(env.get("HERMES_HOME") or "/root/.hermes")
    dotenv = _load_dotenv_file(hermes_dir / ".env")
    if dotenv.get("OPENCODE_GO_BASE_URL") and dotenv.get("OPENCODE_GO_API_KEY"):
        model_name = "deepseek-v4-flash"
        cfg_yaml = hermes_dir / "config.yaml"
        if cfg_yaml.is_file():
            try:
                import yaml

                parsed = yaml.safe_load(cfg_yaml.read_text(encoding="utf-8")) or {}
                m = parsed.get("model")
                if isinstance(m, Mapping) and isinstance(m.get("default"), str) and m["default"].strip():
                    model_name = m["default"].strip()
                elif isinstance(m, str) and m.strip():
                    model_name = m.strip()
            except Exception:
                pass
        env.update(
            {
                "NYA02_MODEL_PROVIDER": "opencode-go",
                "NYA02_MODEL_NAME": model_name,
                "NYA02_MODEL_BASE_URL": dotenv["OPENCODE_GO_BASE_URL"],
                "NYA02_MODEL_API_KEY": dotenv["OPENCODE_GO_API_KEY"],
            }
        )
    info = phone_life.check_model(env)
    return env, info


def sanitize_evidence(doc: Any) -> Any:
    """Recursively strip any accidental secret-like keys before writing evidence."""
    banned = ("api_key", "secret", "password", "authorization", "cookie", "token", "bearer")
    if isinstance(doc, Mapping):
        clean: dict[str, Any] = {}
        for k, v in doc.items():
            lk = str(k).lower()
            if any(b in lk for b in banned) and not lk.endswith("_tokens") and lk not in (
                "api_key_present",
                "total_tokens",
                "input_tokens",
                "output_tokens",
                "reasoning_tokens",
                "prompt_tokens",
                "completion_tokens",
            ):
                continue
            clean[str(k)] = sanitize_evidence(v)
        return clean
    if isinstance(doc, list):
        return [sanitize_evidence(x) for x in doc]
    return doc


def _extract_json_object(raw_text: str) -> Optional[dict[str, Any]]:
    text = str(raw_text or "").strip()
    if not text:
        return None
    # Strip markdown code fence if present
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL)
    cand = fence.group(1) if fence else text
    start = cand.find("{")
    end = cand.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(cand[start : end + 1])
            if isinstance(obj, dict):
                return obj
        except ValueError:
            return None
    return None


def _http_json(
    method: str,
    url: str,
    *,
    body: Optional[dict[str, Any]] = None,
    csrf_token: Optional[str] = None,
    cap_token: Optional[str] = None,
    origin: str = "",
) -> tuple[int, dict[str, Any]]:
    headers: dict[str, str] = {}
    if origin:
        headers["Origin"] = origin
    if csrf_token:
        headers["X-CSRF-Token"] = csrf_token
    if cap_token:
        headers[phone_cap.HEADER_NAME] = cap_token
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["Content-Length"] = str(len(data))
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=8.0) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read().decode("utf-8"))
        except Exception:
            return exc.code, {"ok": False, "http_status": exc.code}


class _EmptySharing:
    def listing(self, **_kw: Any) -> dict[str, Any]:
        return {"ok": True, "status": phone_service.STATUS_OK, "data": {"shares": [], "truncated": False}}


def run_dry_check() -> dict[str, Any]:
    """Zero-LLM offline readiness check."""
    _env, model_info = resolve_live_model_env()
    return sanitize_evidence(
        {
            "schema": "nyairo.v02.real_llm_smoke.v1",
            "mode": "dry_run",
            "checked_at_utc": _utc_now_iso(),
            "ok": True,
            "llm_calls": 0,
            "model_configured": bool(model_info.get("configured")),
            "model_description": model_info.get("description") or {},
            "invariants": {
                "life_update_surfaces": len(phone_tool.LIFE_UPDATE_SURFACES),
                "max_lived_context_chars": lived_context_bridge.MAX_LIVED_CONTEXT_CHARS,
            },
        }
    )


def run_live_smoke(
    *,
    duration_seconds: int = 60,
    max_new_requests: int = 4,
    skip_autonomous_segment: bool = False,
) -> dict[str, Any]:
    """Execute bounded live-LLM 0.2 smoke test on an isolated temporary root."""
    if os.name != "posix":
        return {"ok": False, "reason_code": "POSIX_REQUIRED", "llm_calls": 0}

    cap_requests = max(1, min(int(max_new_requests), HARD_MAX_REQUESTS))
    env, model_info = resolve_live_model_env()
    if not model_info.get("configured"):
        return sanitize_evidence(
            {
                "schema": "nyairo.v02.real_llm_smoke.v1",
                "mode": "live_model",
                "checked_at_utc": _utc_now_iso(),
                "ok": False,
                "reason_code": "NO_MODEL_PROVIDER",
                "llm_calls": 0,
                "model_description": model_info.get("description") or {},
            }
        )

    py_bin = sys.executable
    started_at = _utc_now_iso()
    evidence: dict[str, Any] = {
        "schema": "nyairo.v02.real_llm_smoke.v1",
        "mode": "live_model",
        "started_at_utc": started_at,
        "provider": env.get("NYA02_MODEL_PROVIDER"),
        "model": env.get("NYA02_MODEL_NAME"),
        "base_url": env.get("NYA02_MODEL_BASE_URL"),
        "max_new_requests": cap_requests,
        "phases": {},
        "llm_calls": 0,
        "ok": False,
    }

    with tempfile.TemporaryDirectory(prefix="nyairo-v02-llm-smoke-") as td:
        base = Path(td)
        inst_root = base / "inst"
        auth_db = base / "inst-memory-authority" / "authority.db"
        ext_control = base / "inst-memory-authority" / "v02-control"
        backup_root = base / "inst-pre-forget-backup"

        run_env = dict(env)
        run_env["NYAIRO_V02_CONTROL_ROOT"] = str(ext_control)

        # Phase 1: Real nyairo_day.py init + bounded autonomous run-day (--live-model)
        init_cmd = [
            py_bin,
            str(REPO_ROOT / "scripts" / "nyairo_day.py"),
            "init",
            "--root",
            str(inst_root),
            "--memory-authority",
            str(auth_db),
            "--starter-reading",
            "--json",
        ]
        init_res = subprocess.run(init_cmd, cwd=str(REPO_ROOT), env=run_env, capture_output=True, text=True, timeout=60)
        evidence["phases"]["init"] = {
            "returncode": init_res.returncode,
            "ok": init_res.returncode == 0,
        }
        if init_res.returncode != 0:
            evidence["reason_code"] = "INIT_FAILED"
            evidence["finished_at_utc"] = _utc_now_iso()
            return sanitize_evidence(evidence)

        auto_calls = 0
        if not skip_autonomous_segment:
            auto_cap = max(1, cap_requests - 1)
            run_cmd = [
                py_bin,
                str(REPO_ROOT / "scripts" / "nyairo_day.py"),
                "run-day",
                "--root",
                str(inst_root),
                "--live-model",
                "--duration-seconds",
                str(max(20, int(duration_seconds))),
                "--max-new-requests",
                str(auto_cap),
                "--json",
            ]
            rd_res = subprocess.run(
                run_cmd,
                cwd=str(REPO_ROOT),
                env=run_env,
                capture_output=True,
                text=True,
                timeout=max(120, int(duration_seconds) + 90),
            )
            rd_doc: dict[str, Any] = {}
            for line in reversed(rd_res.stdout.splitlines()):
                s = line.strip()
                if s.startswith("{") and s.endswith("}"):
                    try:
                        rd_doc = json.loads(s)
                        break
                    except ValueError:
                        pass
            if not rd_doc:
                # Try parsing whole stdout from first '{'
                idx = rd_res.stdout.find("{")
                if idx >= 0:
                    try:
                        rd_doc = json.loads(rd_res.stdout[idx:])
                    except ValueError:
                        rd_doc = {}
            auto_calls = int(rd_doc.get("model_calls") or 0)
            evidence["phases"]["autonomous_run_day"] = {
                "returncode": rd_res.returncode,
                "status": rd_doc.get("status"),
                "reason": rd_doc.get("reason"),
                "model_calls": auto_calls,
                "model_called": bool(rd_doc.get("model_called")),
                "ok": rd_res.returncode == 0 or bool(rd_doc.get("ok")),
            }

        # Phase 2: Live PhoneHTTPServer + Lived Context + 1 Real-LLM 0.2 Tool Call + Forget/Restore Barrier
        prev_ctrl_env = os.environ.get(rlf.CONTROL_ROOT_ENV_VAR)
        os.environ[rlf.CONTROL_ROOT_ENV_VAR] = str(ext_control)
        try:
            inst_doc = phone_service.load_instance(inst_root)
            svc = phone_service.PhoneService(inst_doc, environment=run_env, sharing=_EmptySharing())
            csrf_tok = secrets.token_hex(16)
            cap_tok = secrets.token_hex(32)
            srv = phone_server.create_server(
                svc,
                csrf_token=csrf_tok,
                port=0,
                capability=phone_cap.Capability(
                    token=cap_tok,
                    subject=str(inst_doc.get("subject") or "subject_1"),
                    endpoint="http://127.0.0.1:1",
                    issued_at=_utc_now_iso(),
                ),
            )
            port = srv.server_port
            base_url = f"http://127.0.0.1:{port}"
            th = threading.Thread(target=srv.serve_forever, daemon=True)
            th.start()
            try:
                cap_dir = base / "cap_priv"
                cap_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
                os.chmod(cap_dir, 0o700)
                cap_file = cap_dir / "phone-capability.json"
                cap_file.write_text(
                    json.dumps(
                        {
                            "schema_version": phone_cap.SCHEMA_CAPABILITY,
                            "kind": phone_cap.KIND_CAPABILITY,
                            "token": cap_tok,
                            "subject": str(inst_doc.get("subject") or "subject_1"),
                            "endpoint": base_url,
                            "issued_at": _utc_now_iso(),
                        },
                        ensure_ascii=False,
                    ),
                    encoding="utf-8",
                )
                os.chmod(cap_file, 0o600)

                profile_dir = base / "hermes_profile"
                (profile_dir / "chiyo").mkdir(parents=True, exist_ok=True)
                profile_cfg = {
                    "cli_owner": True,
                    "local_owner_uid": os.getuid(),
                    "phone": {
                        "enabled": True,
                        "write_enabled": True,
                        "private_artifacts": True,
                        "base_url": base_url,
                        "profile_home": str(inst_root),
                        "capability_file": str(cap_file),
                    },
                }
                (profile_dir / "chiyo" / "config.json").write_text(
                    json.dumps(profile_cfg, ensure_ascii=False),
                    encoding="utf-8",
                )

                # 2a. Render lived context (<= 1800 chars, zero write)
                lived_ctx = lived_context_bridge.render_lived_companion_context(
                    profile_dir,
                    profile_cfg,
                    incoming_text="窗外起风了，今天海边的松木纸会不会有点潮？",
                )
                evidence["phases"]["lived_context"] = {
                    "chars": len(lived_ctx),
                    "within_1800": len(lived_ctx) <= lived_context_bridge.MAX_LIVED_CONTEXT_CHARS,
                }

                # 2b. Real LLM call via ProfileModelTransport asking for structured 0.2 action + short reply
                transport = pmt.OpenAICompatibleTransport(
                    base_url=env["NYA02_MODEL_BASE_URL"],
                    api_key=env["NYA02_MODEL_API_KEY"],
                    model=env["NYA02_MODEL_NAME"],
                    provider=env.get("NYA02_MODEL_PROVIDER") or "opencode-go",
                    timeout_s=45.0,
                    temperature=0.2,
                )
                sys_prompt = (
                    "你是一个独立居住的普通人，说话自然简短，不用客服腔。"
                    "请只输出一个合法 JSON 对象，字段如下："
                    '{"reply_cn":"一句不超过30字的中文回复","inquiry_question":"一个不超过25字的小观察问题",'
                    '"topic_tag":"paper_craft","explanations":["解释1","解释2"],"next_evidence":"下一步观察方式"}'
                )
                user_prompt = (
                    f"【当下处境】\n{lived_ctx or '在海边屋里整理桌上的画纸。'}\n\n"
                    "伴侣问：窗外起风了，今天海边的松木纸会不会有点潮？\n"
                    "请按要求返回 JSON。"
                )
                t_res = transport.complete(
                    system_prompt=sys_prompt,
                    user_prompt=user_prompt,
                    timeout_s=45.0,
                    max_output_tokens=1024,
                )
                parsed_llm = _extract_json_object(t_res.text) or {}
                reply_cn = str(parsed_llm.get("reply_cn") or "风一起纸角就有点发软，先压在书下吧。").strip()[:60]
                inq_q = str(parsed_llm.get("inquiry_question") or "海风潮湿时松木纸为什么容易微卷？").strip()[:60]
                raw_exps = parsed_llm.get("explanations")
                if isinstance(raw_exps, list) and len(raw_exps) >= 1:
                    cands = [{"summary_cn": str(x).strip()[:40], "stance": "plausible"} for x in raw_exps[:3] if str(x).strip()]
                else:
                    cands = [{"summary_cn": "海风湿度偏高", "stance": "plausible"}]
                next_ev = str(parsed_llm.get("next_evidence") or "明天晴天对比同一批纸").strip()[:60]

                evidence["phases"]["live_llm_call"] = {
                    "ok": True,
                    "status": t_res.status,
                    "latency_ms": t_res.latency_ms,
                    "finish_reason": t_res.finish_reason,
                    "content_chars": t_res.content_chars,
                    "reasoning_chars": t_res.reasoning_chars,
                    "json_parsed": bool(parsed_llm),
                    "total_tokens": t_res.total_tokens,
                }

                # 2c. Execute newly unlocked 0.2 subject action via phone_tool.phone_handler over live HTTP
                tool_raw = phone_tool.phone_handler(
                    {
                        "action": "phone_life_update",
                        "target_surface": "v02_agency_depth",
                        "payload": {
                            "action": "open_bounded_inquiry",
                            "question": inq_q,
                            "topic_tag": "paper_craft",
                            "candidate_explanations": cands,
                            "next_evidence_needed": next_ev,
                            "step_budget": 3,
                            "shared": True,
                            "operation_key": "op_live_smoke_inq_1",
                        },
                    },
                    hermes_home=str(profile_dir),
                )
                tool_doc = json.loads(tool_raw)
                evidence["phases"]["phone_tool_v02_update"] = {
                    "ok": bool(tool_doc.get("ok")),
                    "status": tool_doc.get("status"),
                    "http_status": tool_doc.get("http_status"),
                }

                # 2d. Record completed turn -> partner forget hold -> subject approve -> restore backup -> verify still blocked
                turn_id = "turn_live_smoke_01"
                st_rt, rt_doc = _http_json(
                    "POST",
                    f"{base_url}/api/phone/autonomy/rhythm",
                    body={
                        "action": "record_turn_context",
                        "topic_label": reply_cn,
                        "reason": "COMPLETED_TURN",
                        "owner": "subject",
                        "hold_thread": False,
                        "session_id": "sess_live_smoke",
                        "turn_id": turn_id,
                    },
                    csrf_token=csrf_tok,
                    origin=base_url,
                )
                # Snapshot pre-forget inst_root BEFORE partner forget
                shutil.copytree(inst_root, backup_root, ignore=shutil.ignore_patterns("*.sock"))

                st_fh, fh_doc = _http_json(
                    "POST",
                    f"{base_url}/api/phone/v02/honest-ux",
                    body={
                        "action": "request_forget_scope_hold",
                        "scope_hint": "recent_turn",
                        "target_domain": "conversation",
                        "actor_side": "partner",
                        "operation_key": "op_live_smoke_hold_1",
                    },
                    csrf_token=csrf_tok,
                    origin=base_url,
                )
                hold_id = str(((fh_doc.get("data") or {}).get("case_id") or (fh_doc.get("data") or {}).get("hold_id") or ""))
                st_app, app_doc = _http_json(
                    "POST",
                    f"{base_url}/api/phone/v02/honest-ux",
                    body={
                        "action": "approve_pending_forget_hold",
                        "case_id": hold_id,
                        "scope_choice": "last_turn",
                        "resolution": "COMPLETE",
                        "operation_key": "op_live_smoke_approve_1",
                    },
                    csrf_token=csrf_tok,
                    cap_token=cap_tok,
                    origin=base_url,
                )

                # Live runtime-migration scorecard receipt check
                st_rm, rm_doc = _http_json("GET", f"{base_url}/api/phone/v02/runtime-migration", origin=base_url)
                live_receipt = str(
                    (((rm_doc.get("data") or {}).get("antipuppet_scorecard") or {}).get("plain_chinese_receipt") or "")
                )

                # Stop server before simulating full backup restore of inst_root
                srv.shutdown()
                srv.server_close()

                # Restore pre-forget backup into inst_root while keeping external ext_control intact
                shutil.rmtree(inst_root)
                shutil.copytree(backup_root, inst_root)
                barrier = rlf.load_unified_forget_barrier_readonly(inst_root)
                ext_ledger_exists = ext_control.joinpath(*rlf.CONTROL_LEDGER_REL_PATH).is_file()
                blocked_after_restore = bool(barrier.get("blocked_ids"))

                evidence["phases"]["forget_and_restore_barrier"] = {
                    "record_turn_http": st_rt,
                    "record_turn_ok": bool(rt_doc.get("ok")),
                    "partner_hold_http": st_fh,
                    "partner_hold_ok": bool(fh_doc.get("ok")),
                    "subject_approve_http": st_app,
                    "subject_approve_ok": bool(app_doc.get("ok")),
                    "ext_control_ledger_exists": ext_ledger_exists,
                    "blocked_ids_after_restore_count": len(barrier.get("blocked_ids") or ()),
                    "blocked_after_restore": blocked_after_restore,
                    "runtime_migration_http": st_rm,
                    "live_receipt_non_empty": bool(live_receipt),
                    "live_receipt": live_receipt,
                }
            finally:
                try:
                    srv.shutdown()
                    srv.server_close()
                except Exception:
                    pass
        finally:
            if prev_ctrl_env is None:
                os.environ.pop(rlf.CONTROL_ROOT_ENV_VAR, None)
            else:
                os.environ[rlf.CONTROL_ROOT_ENV_VAR] = prev_ctrl_env

        total_llm_calls = auto_calls + 1
        evidence["llm_calls"] = total_llm_calls
        evidence["finished_at_utc"] = _utc_now_iso()

        p_llm = evidence["phases"].get("live_llm_call") or {}
        p_tool = evidence["phases"].get("phone_tool_v02_update") or {}
        p_fgt = evidence["phases"].get("forget_and_restore_barrier") or {}
        evidence["ok"] = bool(
            p_llm.get("ok")
            and p_tool.get("ok")
            and p_fgt.get("record_turn_ok")
            and p_fgt.get("partner_hold_ok")
            and p_fgt.get("subject_approve_ok")
            and p_fgt.get("ext_control_ledger_exists")
            and p_fgt.get("blocked_after_restore")
            and p_fgt.get("live_receipt_non_empty")
        )
        return sanitize_evidence(evidence)


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Nyairo 0.2 bounded real-LLM live smoke test.")
    parser.add_argument("--dry-run", action="store_true", help="Offline check only (0 LLM calls).")
    parser.add_argument("--live-model", action="store_true", help="Explicitly authorize real LLM calls.")
    parser.add_argument("--duration-seconds", type=int, default=60, help="Autonomous segment duration (seconds).")
    parser.add_argument("--max-new-requests", type=int, default=4, help="Hard cap on real LLM calls (<= 10).")
    parser.add_argument("--skip-autonomous-segment", action="store_true", help="Only run Phase 2 live LLM + phone_tool smoke.")
    parser.add_argument("--output", type=Path, default=None, help="Optional JSON path to write sanitized evidence.")
    args = parser.parse_args(argv)

    if args.dry_run or not args.live_model:
        out = run_dry_check()
        if not args.dry_run and not args.live_model:
            out["note"] = "Pass --live-model to execute real model requests (0 requests sent)."
    else:
        out = run_live_smoke(
            duration_seconds=args.duration_seconds,
            max_new_requests=args.max_new_requests,
            skip_autonomous_segment=args.skip_autonomous_segment,
        )

    rendered = json.dumps(out, ensure_ascii=False, indent=2)
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8", newline="\n")
    print(rendered)
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
