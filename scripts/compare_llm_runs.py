from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from lolo_lead_management.application.container import build_container
from lolo_lead_management.config.env import load_env_file
from lolo_lead_management.config.settings import Settings
from lolo_lead_management.domain.models import LeadSearchStartRequest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run the same lead-search prompt against local and OpenAI-compatible LLM endpoints and compare outputs."
    )
    parser.add_argument(
        "--prompt",
        required=True,
        help="Prompt to execute for both profiles.",
    )
    parser.add_argument(
        "--profiles",
        nargs="+",
        choices=["local", "openai"],
        default=["local", "openai"],
        help="Profiles to execute. Default: local openai",
    )
    parser.add_argument(
        "--output-root",
        default="test-output/llm-compare",
        help="Base directory for per-run databases, artifacts and comparison summary.",
    )
    parser.add_argument("--local-base-url", help="Override local LLM base URL.")
    parser.add_argument("--local-model", help="Override local LLM model.")
    parser.add_argument("--local-api-key", help="Optional API key for the local/openai-like endpoint.")
    parser.add_argument("--local-timeout-seconds", type=int, help="Override local LLM timeout seconds.")
    parser.add_argument("--local-max-completion-tokens", type=int, help="Override local LLM max completion tokens.")
    parser.add_argument("--openai-base-url", help="Override OpenAI-compatible base URL.")
    parser.add_argument("--openai-model", help="Override OpenAI model. Example: gpt-5-mini")
    parser.add_argument("--openai-api-key", help="Override OpenAI API key.")
    parser.add_argument("--openai-timeout-seconds", type=int, help="Override OpenAI LLM timeout seconds.")
    parser.add_argument("--openai-max-completion-tokens", type=int, help="Override OpenAI LLM max completion tokens.")
    parser.add_argument("--openai-reasoning-effort", help="Override OpenAI reasoning effort.")
    return parser.parse_args()


def _timestamp_slug() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _profile_config(profile: str, args: argparse.Namespace, base_env: dict[str, str]) -> dict[str, str | None]:
    if profile == "local":
        return {
            "base_url": args.local_base_url
            or base_env.get("COMPARE_LOCAL_LLM_BASE_URL")
            or "http://127.0.0.1:1234/v1/chat/completions",
            "model": args.local_model
            or base_env.get("COMPARE_LOCAL_LLM_MODEL")
            or base_env.get("LOLO_LLM_MODEL")
            or base_env.get("LOLO_LM_STUDIO_MODEL")
            or "qwen/qwen3-30b-a3b-instruct-2507",
            "api_key": args.local_api_key or base_env.get("COMPARE_LOCAL_LLM_API_KEY") or None,
            "timeout_seconds": str(
                args.local_timeout_seconds
                or base_env.get("COMPARE_LOCAL_LLM_TIMEOUT_SECONDS")
                or base_env.get("LOLO_LLM_TIMEOUT_SECONDS")
                or "90"
            ),
            "max_completion_tokens": (
                str(args.local_max_completion_tokens)
                if args.local_max_completion_tokens is not None
                else base_env.get("COMPARE_LOCAL_LLM_MAX_COMPLETION_TOKENS")
                or base_env.get("LOLO_LLM_MAX_COMPLETION_TOKENS")
                or None
            ),
            "reasoning_effort": None,
        }
    return {
        "base_url": args.openai_base_url
        or base_env.get("COMPARE_OPENAI_LLM_BASE_URL")
        or "https://api.openai.com/v1/chat/completions",
        "model": args.openai_model
        or base_env.get("COMPARE_OPENAI_LLM_MODEL")
        or "gpt-5-mini",
        "api_key": args.openai_api_key
        or base_env.get("COMPARE_OPENAI_LLM_API_KEY")
        or base_env.get("LOLO_LLM_API_KEY")
        or base_env.get("OPENAI_API_KEY")
        or None,
        "timeout_seconds": str(
            args.openai_timeout_seconds
            or base_env.get("COMPARE_OPENAI_LLM_TIMEOUT_SECONDS")
            or base_env.get("LOLO_LLM_TIMEOUT_SECONDS")
            or "90"
        ),
        "max_completion_tokens": (
            str(args.openai_max_completion_tokens)
            if args.openai_max_completion_tokens is not None
            else base_env.get("COMPARE_OPENAI_LLM_MAX_COMPLETION_TOKENS")
            or base_env.get("LOLO_LLM_MAX_COMPLETION_TOKENS")
            or None
        ),
        "reasoning_effort": args.openai_reasoning_effort
        or base_env.get("COMPARE_OPENAI_LLM_REASONING_EFFORT")
        or base_env.get("LOLO_LLM_REASONING_EFFORT")
        or None,
    }


def _artifact_for_run(artifact_dir: Path, run_id: str) -> Path | None:
    matches = sorted(artifact_dir.glob(f"*lead-search-run*{run_id}*.json"))
    return matches[-1] if matches else None


def _serialize_shortlist_option(option) -> dict[str, Any]:
    return {
        "option_number": option.option_number,
        "company_name": option.company_name,
        "person_name": option.person_name,
        "role_title": option.role_title,
        "lead_source_type": option.lead_source_type,
        "person_confidence": option.person_confidence,
        "primary_person_source_url": option.primary_person_source_url,
        "website": option.website,
        "qualification_outcome": option.qualification.outcome.value,
        "qualification_summary": option.qualification.summary,
    }


def _serialize_accepted_lead(lead) -> dict[str, Any]:
    return {
        "lead_id": lead.lead_id,
        "company_name": lead.company_name,
        "person_name": lead.person_name,
        "role_title": lead.role_title,
        "lead_source_type": lead.lead_source_type,
        "person_confidence": lead.person_confidence,
        "primary_person_source_url": lead.primary_person_source_url,
        "website": lead.website,
        "qualification_outcome": lead.qualification.outcome.value,
        "qualification_summary": lead.qualification.summary,
    }


def _artifact_snapshot(artifact_path: str | None) -> dict[str, Any]:
    if not artifact_path:
        return {}
    path = Path(artifact_path)
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    final_run = payload.get("final_run") or {}
    response = payload.get("response") or {}
    iterations = final_run.get("iterations") or []
    focus_companies: list[str] = []
    deterministic_outcomes: list[str] = []
    merged_outcomes: list[str] = []
    llm_error_count = 0
    for iteration in iterations:
        assembler = iteration.get("assembler_trace") or {}
        focus = ((assembler.get("company_selection") or {}).get("focus_resolution") or {})
        selected_company = focus.get("selected_company")
        if selected_company:
            focus_companies.append(selected_company)
        qualification = iteration.get("qualification_trace") or {}
        deterministic = (qualification.get("deterministic_decision") or {}).get("outcome")
        merged = (qualification.get("merged_decision") or {}).get("outcome")
        if deterministic:
            deterministic_outcomes.append(deterministic)
        if merged:
            merged_outcomes.append(merged)
        llm_error_count += sum(
            1
            for item in ((assembler.get("company_selection") or {}).get("focus_extraction_sanitized_outputs") or [])
            if item.get("llm_error")
        )
    shortlist_options = response.get("shortlist_options") or []
    return {
        "iteration_count": len(iterations),
        "focus_companies": focus_companies,
        "deterministic_outcomes": deterministic_outcomes,
        "merged_outcomes": merged_outcomes,
        "llm_error_count": llm_error_count,
        "shortlist_with_person_count": sum(1 for item in shortlist_options if item.get("person_name")),
        "shortlist_with_website_count": sum(1 for item in shortlist_options if item.get("website")),
    }


def _score_result(result: dict[str, Any]) -> tuple[int, int, int, int, float]:
    artifact = result.get("artifact_snapshot") or {}
    return (
        int(result.get("accepted_count") or 0),
        int(result.get("shortlist_count") or 0),
        int(artifact.get("shortlist_with_person_count") or 0),
        int(artifact.get("shortlist_with_website_count") or 0),
        -float(result.get("elapsed_seconds") or 0.0),
    )


def _build_comparison(results: list[dict[str, Any]]) -> dict[str, Any]:
    if len(results) < 2:
        return {
            "comparable": False,
            "summary": "Only one profile was executed.",
            "conclusions": [],
        }
    ordered = sorted(results, key=_score_result, reverse=True)
    best = ordered[0]
    worst = ordered[-1]
    conclusions: list[str] = []
    if best["accepted_count"] != worst["accepted_count"]:
        conclusions.append(
            f"{best['profile']} closed more accepted leads ({best['accepted_count']} vs {worst['accepted_count']})."
        )
    elif best["shortlist_count"] != worst["shortlist_count"]:
        conclusions.append(
            f"{best['profile']} produced more shortlist options ({best['shortlist_count']} vs {worst['shortlist_count']})."
        )
    best_person = (best.get("artifact_snapshot") or {}).get("shortlist_with_person_count", 0)
    worst_person = (worst.get("artifact_snapshot") or {}).get("shortlist_with_person_count", 0)
    if best_person != worst_person:
        conclusions.append(
            f"{best['profile']} preserved more named contacts in shortlist options ({best_person} vs {worst_person})."
        )
    best_focus = Counter((best.get("artifact_snapshot") or {}).get("focus_companies") or [])
    worst_focus = Counter((worst.get("artifact_snapshot") or {}).get("focus_companies") or [])
    if best_focus != worst_focus:
        conclusions.append(
            f"Focus selection diverged: {best['profile']} explored {list(best_focus.keys())[:3]} while {worst['profile']} explored {list(worst_focus.keys())[:3]}."
        )
    best_errors = (best.get("artifact_snapshot") or {}).get("llm_error_count", 0)
    worst_errors = (worst.get("artifact_snapshot") or {}).get("llm_error_count", 0)
    if best_errors != worst_errors:
        lower_error = best if best_errors < worst_errors else worst
        higher_error = worst if lower_error is best else best
        conclusions.append(
            f"{lower_error['profile']} had fewer LLM extraction errors ({lower_error['artifact_snapshot'].get('llm_error_count', 0)} vs {higher_error['artifact_snapshot'].get('llm_error_count', 0)})."
        )
    if not conclusions:
        conclusions.append("Both profiles produced materially similar top-level outcomes for this prompt.")
    return {
        "comparable": True,
        "winner": best["profile"],
        "summary": f"Best overall result for this prompt: {best['profile']}.",
        "conclusions": conclusions,
    }


def run_profile(
    *,
    profile: str,
    args: argparse.Namespace,
    base_env: dict[str, str],
    session_root: Path,
) -> dict[str, Any]:
    config = _profile_config(profile, args, base_env)
    if profile == "openai" and not config["api_key"]:
        raise ValueError("OpenAI profile requires an API key. Set LOLO_LLM_API_KEY, OPENAI_API_KEY, or --openai-api-key.")

    profile_root = session_root / profile
    artifact_dir = profile_root / "artifacts"
    database_path = profile_root / "lead_management.sqlite3"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    env = dict(base_env)
    env["LOLO_LLM_ENABLED"] = "true"
    env["LOLO_SEARCH_ENABLED"] = env.get("LOLO_SEARCH_ENABLED", "true") or "true"
    env["LOLO_DATABASE_PATH"] = str(database_path.resolve())
    env["LOLO_EXECUTION_RESULTS_DIR"] = str(artifact_dir.resolve())
    env["LOLO_LLM_BASE_URL"] = str(config["base_url"])
    env["LOLO_LLM_MODEL"] = str(config["model"])
    env["LOLO_LLM_TIMEOUT_SECONDS"] = str(config["timeout_seconds"])
    if config["api_key"]:
        env["LOLO_LLM_API_KEY"] = str(config["api_key"])
    else:
        env.pop("LOLO_LLM_API_KEY", None)
    if config["max_completion_tokens"]:
        env["LOLO_LLM_MAX_COMPLETION_TOKENS"] = str(config["max_completion_tokens"])
    else:
        env.pop("LOLO_LLM_MAX_COMPLETION_TOKENS", None)
    if config["reasoning_effort"]:
        env["LOLO_LLM_REASONING_EFFORT"] = str(config["reasoning_effort"])
    else:
        env.pop("LOLO_LLM_REASONING_EFFORT", None)

    settings = Settings.from_environ(env)
    container = build_container(settings)
    payload = LeadSearchStartRequest(user_text=args.prompt)

    started = time.perf_counter()
    run = container.engine.initialize_run(payload)
    container.run_store.save_run(run)
    completed = container.engine.run_to_completion(run.run_id, raise_on_error=False)
    elapsed_seconds = round(time.perf_counter() - started, 2)
    artifact_path = _artifact_for_run(artifact_dir, completed.run_id)

    result = {
        "profile": profile,
        "llm_base_url": settings.llm_base_url,
        "llm_model": settings.llm_model,
        "llm_timeout_seconds": settings.llm_timeout_seconds,
        "llm_max_completion_tokens": settings.llm_max_completion_tokens,
        "llm_reasoning_effort": settings.llm_reasoning_effort,
        "database_path": settings.database_path,
        "artifact_dir": str(artifact_dir.resolve()),
        "artifact_path": str(artifact_path.resolve()) if artifact_path else None,
        "elapsed_seconds": elapsed_seconds,
        "run_id": completed.run_id,
        "status": completed.status.value,
        "completed_reason": completed.completed_reason,
        "accepted_count": len(completed.accepted_leads),
        "shortlist_count": len(completed.shortlist_options),
        "accepted_leads": [_serialize_accepted_lead(item) for item in completed.accepted_leads],
        "shortlist_options": [_serialize_shortlist_option(item) for item in completed.shortlist_options],
        "errors": completed.errors,
    }
    result["artifact_snapshot"] = _artifact_snapshot(result["artifact_path"])
    return result


def main() -> int:
    load_env_file()
    args = parse_args()
    session_root = (REPO_ROOT / args.output_root / _timestamp_slug()).resolve()
    session_root.mkdir(parents=True, exist_ok=True)
    base_env = dict(os.environ)

    results: list[dict[str, Any]] = []
    for profile in args.profiles:
        print(f"[{profile}] starting")
        result = run_profile(profile=profile, args=args, base_env=base_env, session_root=session_root)
        results.append(result)
        print(
            f"[{profile}] done  status={result['status']}  reason={result['completed_reason']}  "
            f"accepted={result['accepted_count']}  shortlist={result['shortlist_count']}"
        )
        print(f"[{profile}] artifact={result['artifact_path']}")
        artifact = result.get("artifact_snapshot") or {}
        if artifact:
            print(
                f"[{profile}] focuses={artifact.get('focus_companies', [])[:3]} "
                f"shortlist_with_person={artifact.get('shortlist_with_person_count', 0)} "
                f"llm_errors={artifact.get('llm_error_count', 0)}"
            )

    comparison = _build_comparison(results)
    print("comparison_summary:")
    print(f"  {comparison['summary']}")
    for conclusion in comparison.get("conclusions", []):
        print(f"  - {conclusion}")

    summary = {
        "prompt": args.prompt,
        "session_root": str(session_root),
        "results": results,
        "comparison": comparison,
    }
    summary_path = session_root / "comparison_summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"comparison_summary={summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
