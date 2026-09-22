"""Generate one isolated model-comparison poster from an existing report snapshot.

Never fetch messages, enqueue delivery, modify the source report, or retry an
existing comparison. The API key comes from the application's local settings.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from time import perf_counter

from app.ai.prompt_builder import DeepSeekImagePromptBuilder, PromptInput
from app.ai.speaker_attribution import build_attribution_contract
from app.ai.strict_prompt_contract import append_strict_image_fact_contract
from app.config.settings import Settings
from app.image.codex_generator import CodexImageGenerator
from app.image.fact_verification import strip_unverified_prompt_numeric_units
from app.image.image_task import verify_image_contract
from app.pipeline.daily_pipeline import DailyPipeline
from app.ranking.engine import RankingEngine
from app.v2.run_store import RunStore


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def compare(source: Path, destination: Path) -> dict:
    source, destination = source.resolve(), destination.resolve()
    if destination == source or destination.is_relative_to(source):
        raise ValueError("Comparison must be outside the original report directory")
    destination.mkdir(parents=True, exist_ok=False)
    names = ("messages.json", "ranking.json", "ranking.txt", "run.json", "image_prompt.txt", "daily_image.png")
    original_hashes = {name: digest(source / name) for name in names}
    original = json.loads((source / "run.json").read_text(encoding="utf-8"))
    if original.get("report_kind", "daily") != "daily":
        raise ValueError("This comparison command accepts daily reports only")
    settings = Settings()
    # Read runtime preferences without replacing the explicitly supplied local key.
    with sqlite3.connect(f"file:{settings.db_path.as_posix()}?mode=ro", uri=True) as connection:
        values = dict(connection.execute("SELECT key,value FROM settings WHERE key != 'ai_api_key'"))
        row = connection.execute("SELECT image_prompt_override FROM groups WHERE id=?", (original["group_id"],)).fetchone()
    settings.apply_runtime_values(values)
    settings = settings.model_copy(update={
        "ai_base_url": "https://api.deepseek.com",
        "ai_model": "deepseek-flash", "summary_provider_primary": "codex",
        "summary_provider_fallback": "none", "codex_summary_model": "gpt-5.6-luna",
        "codex_image_model": "gpt-5.6-luna", "codex_reasoning_effort": "max",
    })
    if not settings.ai_api_key:
        raise ValueError("Local AI_API_KEY is missing")
    summary_settings = settings.model_copy(update={"summary_provider_primary": "deepseek", "summary_provider_fallback": "none"})
    for name in ("messages.json", "ranking.json", "ranking.txt"):
        shutil.copy2(source / name, destination / name)
    shutil.copy2(source / "daily_image.png", destination / "original.png")
    messages = DailyPipeline._load_message_snapshot(source / "messages.json")
    ranking = json.loads((source / "ranking.json").read_text(encoding="utf-8"))
    attribution = build_attribution_contract(messages)
    store = RunStore(settings.output_dir)
    data = PromptInput(
        group_name=original["group_name"], group_id=str(original["group_id"]),
        visible_group_name=original.get("wechat_group_name") or original["group_name"],
        run_date=original["run_date"], period_start=original["period_start"], period_end=original["period_end"],
        report_date=original["period_end"][:10], report_kind=original.get("report_kind", "daily"),
        message_count=ranking["message_count"], speaker_count=ranking["speaker_count"],
        messages=[m for m in messages if RankingEngine._countable(m)],
        template=original.get("image_prompt_template", "default"), template_override=(row[0] if row else "") or "",
        image_theme=original.get("image_theme", "ai_free"), image_theme_custom=original.get("image_theme_custom", ""),
        message_snapshot_sha256=attribution.message_snapshot_sha256, speaker_fingerprint=attribution.speaker_fingerprint,
        previous_theme_signature=store.previous_theme_signature(original["group_name"], original["run_date"]),
        recent_layout_history=store.recent_layout_history(original["group_name"], original["run_date"], limit=3),
    )
    run = {key:original[key] for key in ("group_id", "group_name", "run_date", "period_start", "period_end", "report_kind", "ranking_count_policy", "strict_image_fact_check") if key in original}
    run.update(status="COMPARISON_ONLY", wechat_send_enabled=False, image_fact_contract="strict_evidence_v1")
    write_json(destination / "run.json", run)
    report = {"source":str(source), "destination":str(destination), "source_hashes":original_hashes, "status":"prompt_submitting", "send_enabled":False}
    report_path = destination / "comparison.json"
    write_json(report_path, report)
    started = perf_counter()
    try:
        result = DeepSeekImagePromptBuilder(settings, summary_settings=summary_settings).build(data)
        report["prompt_seconds"] = round(perf_counter() - started, 2)
        report["prompt_meta"] = result.meta
        if not result.success:
            report.update(status="prompt_failed", error=result.error)
            return report
        run["prompt_meta"] = result.meta
        write_json(destination / "run.json", run)
        prompt_path = destination / "image_prompt.txt"
        prompt_path.write_text(append_strict_image_fact_contract(result.prompt), encoding="utf-8")
        text, removed = strip_unverified_prompt_numeric_units(prompt_path)
        prompt_path.write_text(text, encoding="utf-8")
        report.update(status="image_submitting", stripped_numeric_units=list(removed))
        write_json(report_path, report)
        image_started = perf_counter()
        image_result = CodexImageGenerator(settings, max_attempts=1).generate(prompt_path, destination / "daily_image.png")
        report["image_seconds"] = round(perf_counter() - image_started, 2)
        report["image_result"] = {"success":image_result.success, "error":image_result.error, "detail":image_result.detail, "path":str(image_result.image_path) if image_result.image_path else None}
        if image_result.success:
            ok, detail = verify_image_contract(prompt_path, destination / "daily_image.png")
            report.update(status="complete" if ok else "image_review_failed", verification={"ok":ok,"detail":detail})
        else:
            report["status"] = "image_failed"
        return report
    except Exception as exc:
        # The submitting stage is retained as evidence; never auto-resubmit it.
        report.update(status="stopped", error_type=type(exc).__name__)
        raise
    finally:
        report["total_seconds"] = round(perf_counter() - started, 2)
        report["source_unchanged"] = all(digest(source/name)==value for name,value in original_hashes.items())
        write_json(report_path, report)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    result = compare(args.source_dir, args.output_dir)
    print(json.dumps({k:result.get(k) for k in ("status","destination","prompt_seconds","image_seconds","total_seconds","source_unchanged")}, ensure_ascii=False))
    raise SystemExit(0 if result["status"] == "complete" else 1)
