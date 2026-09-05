"""图片产物来源门禁：诊断兜底永远不能进入外部发送。"""

from __future__ import annotations

from typing import Any, Mapping


def image_fallback_level(metadata: Mapping[str, Any] | None) -> int:
    """读取兜底等级；非空脏值按 Level 3 处理，保持 fail-closed。"""
    metadata = metadata if isinstance(metadata, Mapping) else {}
    raw_level = metadata.get("image_fallback_level", 0)
    try:
        return int(raw_level or 0)
    except (TypeError, ValueError):
        return 3 if str(raw_level or "").strip() else 0


def image_delivery_eligible(metadata: Mapping[str, Any] | None) -> bool:
    """只有真实或安全化生成图可以发送；Level 3/Pillow 仅供诊断。"""
    metadata = metadata if isinstance(metadata, Mapping) else {}
    fallback_level = image_fallback_level(metadata)
    image_variant = str(metadata.get("image_variant") or "").strip().lower()
    image_status = str(metadata.get("image_status") or "").strip().lower()
    image_job = metadata.get("image_job")
    job_status = (
        str(image_job.get("status") or "").strip().lower()
        if isinstance(image_job, Mapping)
        else ""
    )
    if (
        fallback_level >= 3
        or image_variant == "pillow"
        or image_status in {"failed", "diagnostic_fallback"}
        or job_status in {"failed", "ambiguous_result", "diagnostic_fallback"}
    ):
        return False

    recovery_status = str(
        metadata.get("image_recovery_status")
        or metadata.get("recovery_status")
        or ""
    ).strip().lower()
    if recovery_status == "existing_output_reused":
        diagnostic_history = " ".join(
            str(metadata.get(key) or "")
            for key in (
                "last_error_summary",
                "image_fallback_reason",
                "prompt_fallback_reason",
            )
        ).lower()
        if "本地诊断图" in diagnostic_history or "fallback=l3" in diagnostic_history:
            return False
    return True
