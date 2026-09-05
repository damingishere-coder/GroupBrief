"""V2 P5：图片生成任务单元测试。

验证：图片格式识别 / verify_image / 串行队列顺序与隔离 / 已存在跳过 /
CodexImageGenerator health_check 不可用判定。
"""

from __future__ import annotations

from contextlib import nullcontext
import hashlib
import io
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest
from PIL import Image

from app.image import codex_generator
from app.image.codex_generator import CodexImageGenerator
from app.image.image_task import (
    ImageJob,
    SerialImageQueue,
    copy_generated_image,
    detect_image_format,
    verify_image,
)

# 1x1 透明且可被 Pillow 完整解码的 PNG。
_png_buffer = io.BytesIO()
Image.new("RGBA", (1, 1), (0, 0, 0, 0)).save(_png_buffer, format="PNG")
_PNG_1PX = _png_buffer.getvalue()


def test_detect_png(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_PNG_1PX)
    assert detect_image_format(p) == "png"


def test_detect_jpeg_signature(tmp_path):
    p = tmp_path / "a.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 10)
    assert detect_image_format(p) == "jpeg"


def test_detect_unknown(tmp_path):
    p = tmp_path / "a.txt"
    p.write_text("not an image", encoding="utf-8")
    assert detect_image_format(p) is None


def test_verify_image_ok(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(_PNG_1PX)
    ok, detail = verify_image(p)
    assert ok is True
    assert "png" in detail


def test_verify_image_missing(tmp_path):
    ok, detail = verify_image(tmp_path / "nope.png")
    assert ok is False
    assert "不存在" in detail


def test_verify_image_empty(tmp_path):
    p = tmp_path / "empty.png"
    p.write_bytes(b"")
    ok, detail = verify_image(p)
    assert ok is False
    assert "为空" in detail


def test_verify_image_not_image(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"\x00" * 10)
    ok, detail = verify_image(p)
    assert ok is False
    assert "无法完整解码" in detail


def test_verify_image_rejects_truncated_png_with_valid_signature(tmp_path):
    p = tmp_path / "truncated.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\n" + b"broken")

    ok, detail = verify_image(p)

    assert ok is False
    assert "无法完整解码" in detail


class FakeGenerator:
    """写入一个真实 PNG 的假生成器。"""

    def __init__(self, fail: bool = False, delay: float = 0):
        self.fail = fail
        self.calls: list[tuple[Path, Path]] = []

    def generate(self, prompt_file: Path, output_path: Path):
        from app.image.image_task import ImageTaskResult

        self.calls.append((prompt_file, output_path))
        if self.fail:
            return ImageTaskResult(False, error="生成器失败")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(_PNG_1PX)
        return ImageTaskResult(True, image_path=output_path)


def _job(tmp_path, name, generator, force=False) -> ImageJob:
    prompt = tmp_path / f"{name}_prompt.txt"
    prompt.write_text("生成图片", encoding="utf-8")
    return ImageJob(
        group_name=name,
        prompt_file=prompt,
        output_path=tmp_path / f"{name}" / "2026-08-18" / "daily_image.png",
        generator=generator,
        force=force,
    )


def test_serial_queue_sequential(tmp_path):
    gen = FakeGenerator()
    queue = SerialImageQueue()
    jobs = [_job(tmp_path, f"群{i}", gen) for i in range(3)]
    results = queue.run_all(jobs)
    assert [r["status"] for r in results] == ["success", "success", "success"]
    # 串行：调用顺序与任务顺序一致
    assert len(gen.calls) == 3
    for i, job in enumerate(jobs):
        assert gen.calls[i][1] == job.output_path
    # 每张图片可验证
    for job in jobs:
        ok, _ = verify_image(job.output_path)
        assert ok is True


def test_queue_surfaces_result_persistence_hook_failure(tmp_path):
    def broken_hook(_job, _result):
        raise OSError("disk unavailable")

    job = _job(tmp_path, "状态落盘失败群", FakeGenerator())
    result = SerialImageQueue(run_hook=broken_hook).run_all([job])[0]

    assert result["status"] == "failed"
    assert result["success"] is False
    assert result["error_type"] == "IMAGE_STATE_PERSIST_FAILED"
    assert result["hook_error"] is True
    assert "OSError" in result["detail"]


def test_single_failure_does_not_block_others(tmp_path):
    queue = SerialImageQueue()
    jobs = [
        _job(tmp_path, "群1", FakeGenerator(fail=True)),
        _job(tmp_path, "群2", FakeGenerator()),
        _job(tmp_path, "群3", FakeGenerator(fail=True)),
    ]
    results = queue.run_all(jobs)
    assert [r["status"] for r in results] == [
        "failed",
        "success",
        "failed",
    ]
    assert [r["success"] for r in results] == [False, True, False]
    assert results[0]["generator_detail"]["local_infographic_disabled"] is True
    assert not jobs[0].output_path.exists()
    assert verify_image(jobs[1].output_path)[0] is True
    assert not jobs[2].output_path.exists()


def test_policy_rejection_uses_safe_prompt_once(tmp_path):
    from app.image.image_task import ImageTaskResult

    class PolicyThenSuccess:
        def __init__(self):
            self.prompts = []

        def generate(self, prompt_file, output_path):
            self.prompts.append(prompt_file.read_text(encoding="utf-8"))
            if len(self.prompts) == 1:
                return ImageTaskResult(
                    False,
                    error="blocked",
                    detail={"error_code": "CONTENT_FILTER"},
                )
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(_PNG_1PX)
            return ImageTaskResult(True, image_path=output_path)

    generator = PolicyThenSuccess()
    prompt = tmp_path / "image_prompt.txt"
    prompt.write_text("测试群 张三 讨论当天票房 500 万", encoding="utf-8")
    ranking = tmp_path / "ranking.json"
    ranking.write_text(
        json.dumps({"top_speakers": [{"name": "张三", "count": 3}]}, ensure_ascii=False),
        encoding="utf-8",
    )
    job = ImageJob(
        group_name="测试群",
        prompt_file=prompt,
        output_path=tmp_path / "daily_image.png",
        generator=generator,
    )

    result = job.run()

    assert result["status"] == "success"
    assert result["generator_detail"]["fallback_level"] == 2
    assert len(generator.prompts) == 2
    assert "测试群" not in generator.prompts[1]
    assert "张三" not in generator.prompts[1]
    assert "500" in generator.prompts[1]


def test_unknown_image_result_never_calls_safe_or_local_fallback(tmp_path):
    from app.image.image_task import ImageTaskResult

    class UnknownGenerator:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt_file, output_path):
            self.calls += 1
            return ImageTaskResult(
                False,
                error="receipt missing",
                detail={"outcome_unknown": True, "recovery_status": "result_unknown"},
            )

    generator = UnknownGenerator()
    job = _job(tmp_path, "群1", generator)

    result = job.run()

    assert result["status"] == "failed"
    assert generator.calls == 1
    assert not job.output_path.exists()


def test_strict_verification_known_failure_does_not_create_statistical_fallback(tmp_path, monkeypatch):
    from app.image.image_task import ImageTaskResult

    class QualityRetryFailsKnown:
        def __init__(self):
            self.calls = 0

        def generate(self, _prompt_file, output_path, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(_PNG_1PX)
                return ImageTaskResult(True, image_path=output_path)
            return ImageTaskResult(
                False,
                error="network failure",
                detail={"outcome_unknown": False},
            )

    generator = QualityRetryFailsKnown()
    job = _job(tmp_path, "群1", generator)
    monkeypatch.setattr(
        "app.image.image_task.verify_image_contract",
        lambda *_args, **_kwargs: (False, "图片事实校验失败"),
    )
    monkeypatch.setattr(
        "app.image.fact_verification.strict_fact_verification_enabled",
        lambda _path: True,
    )
    result = job.run()

    assert result["status"] == "failed"
    assert result["success"] is False
    assert result["error_type"] == "IMAGE_CONTENT_VERIFICATION_FAILED"
    assert generator.calls == 2
    assert result["generator_detail"]["local_infographic_disabled"] is True
    assert not job.output_path.exists()


def test_fact_verification_retry_uses_correction_prompt(tmp_path, monkeypatch):
    from app.image.image_task import ImageTaskResult

    class CapturingGenerator:
        def __init__(self):
            self.prompts: list[Path] = []

        def generate(self, prompt_file, output_path, **_kwargs):
            self.prompts.append(Path(prompt_file))
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_bytes(_PNG_1PX)
            return ImageTaskResult(True, image_path=output_path)

    generator = CapturingGenerator()
    job = _job(tmp_path, "纠错重画群", generator)
    verification_results = iter(
        [
            (False, "图片事实校验失败：无证据数字：45元, 11218"),
            (True, "OK"),
        ]
    )
    monkeypatch.setattr(
        "app.image.image_task.verify_image_contract",
        lambda *_args, **_kwargs: next(verification_results),
    )
    monkeypatch.setattr(
        "app.image.fact_verification.strict_fact_verification_enabled",
        lambda _path: True,
    )

    result = job.run()

    assert result["status"] == "success"
    assert len(generator.prompts) == 2
    assert generator.prompts[1].name == "image_prompt.fact_retry.txt"
    correction = generator.prompts[1].read_text(encoding="utf-8")
    assert "新图不得出现上述字符串" in correction
    assert "45元、11218" in correction


def test_retry_does_not_treat_existing_diagnostic_png_as_success(tmp_path):
    generator = FakeGenerator()
    job = _job(tmp_path, "诊断图重试群", generator)
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    job.output_path.write_bytes(_PNG_1PX)
    (job.output_path.parent / "run.json").write_text(
        json.dumps(
            {
                "image_fallback_level": 3,
                "image_variant": "pillow",
                "image_force_local_fallback": False,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = job.run()

    assert result["status"] == "success"
    assert len(generator.calls) == 1


def test_retry_rejects_diagnostic_reused_after_metadata_was_reset(tmp_path):
    generator = FakeGenerator()
    job = _job(tmp_path, "诊断图元数据误清理群", generator)
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    job.output_path.write_bytes(_PNG_1PX)
    (job.output_path.parent / "run.json").write_text(
        json.dumps(
            {
                "image_fallback_level": 0,
                "image_variant": "normal",
                "image_recovery_status": "existing_output_reused",
                "last_error_summary": "图片生成失败，已保留不可发送的本地诊断图",
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    result = job.run()

    assert result["status"] == "success"
    assert len(generator.calls) == 1


@pytest.mark.parametrize(
    "run_state",
    [
        {"image_status": "failed"},
        {"image_job": {"status": "ambiguous_result"}},
    ],
)
def test_retry_never_reuses_failed_or_ambiguous_existing_output(tmp_path, run_state):
    generator = FakeGenerator()
    job = _job(tmp_path, "失败结果残留群", generator)
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    job.output_path.write_bytes(_PNG_1PX)
    (job.output_path.parent / "run.json").write_text(
        json.dumps(run_state, ensure_ascii=False),
        encoding="utf-8",
    )

    result = job.run()

    assert result["status"] == "success"
    assert len(generator.calls) == 1


def test_strict_verification_unknown_retry_stays_failed_closed(tmp_path, monkeypatch):
    from app.image.image_task import ImageTaskResult

    class QualityRetryUnknown:
        def __init__(self):
            self.calls = 0

        def generate(self, _prompt_file, output_path, **_kwargs):
            self.calls += 1
            if self.calls == 1:
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(_PNG_1PX)
                return ImageTaskResult(True, image_path=output_path)
            return ImageTaskResult(
                False,
                error="receipt missing",
                detail={"outcome_unknown": True},
            )

    generator = QualityRetryUnknown()
    job = _job(tmp_path, "群1", generator)
    monkeypatch.setattr(
        "app.image.image_task.verify_image_contract",
        lambda *_args, **_kwargs: (False, "图片事实校验失败"),
    )
    monkeypatch.setattr(
        "app.image.fact_verification.strict_fact_verification_enabled",
        lambda _path: True,
    )
    monkeypatch.setattr(
        ImageJob,
        "_local_fallback",
        lambda *_args, **_kwargs: pytest.fail("结果未知时不得进入本地兜底"),
    )

    result = job.run()

    assert result["status"] == "failed"
    assert result["error_type"] == "IMAGE_GENERATION_FAILED"
    assert generator.calls == 2


def test_skip_when_image_exists(tmp_path):
    gen = FakeGenerator()
    job = _job(tmp_path, "群1", gen)
    # 先成功生成一次
    assert job.run()["status"] == "success"
    (job.output_path.parent / "run.json").write_text(
        json.dumps(
            {
                "image_enabled": True,
                "image_fallback_level": 0,
                "image_variant": "normal",
                "image_status": "success",
                "image_job": {"status": "completed"},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    # 再跑：已存在有效图片 → 跳过（不重复生成）
    result = job.run()
    assert result["status"] == "skipped"
    assert len(gen.calls) == 1  # 未再次调用生成器


def test_force_regenerates(tmp_path):
    gen = FakeGenerator()
    job = _job(tmp_path, "群1", gen, force=True)
    job.run()
    result = job.run()  # force=True → 即使已存在也重新生成
    assert result["status"] == "success"
    assert len(gen.calls) == 2


def test_copy_generated_image(tmp_path):
    src = tmp_path / "src.png"
    src.write_bytes(_PNG_1PX)
    dst = tmp_path / "nested" / "daily_image.png"
    copy_generated_image(src, dst)
    assert dst.exists()
    assert detect_image_format(dst) == "png"


def test_codex_health_unavailable():
    # codex 命令不可用（PATH 里不存在名为 nonexistent-codex 的命令）
    gen = CodexImageGenerator(codex_path="definitely-not-existing-codex-cmd")
    ok, detail = gen.health_check()
    assert ok is False
    assert "不可用" in detail


def test_codex_health_rejects_existing_but_unexecutable_binary(tmp_path):
    fake = tmp_path / "codex.exe"
    fake.write_text("not a windows executable", encoding="utf-8")
    gen = CodexImageGenerator(codex_path=str(fake))

    ok, detail = gen.health_check()

    assert ok is False
    assert "执行" in detail or "无法" in detail


def test_codex_generate_returns_failure_when_unavailable_before_waiting_for_mutex(
    tmp_path,
    monkeypatch,
):
    gen = CodexImageGenerator(codex_path="definitely-not-existing-codex-cmd")
    prompt = tmp_path / "p.txt"
    prompt.write_text("test", encoding="utf-8")
    monkeypatch.setattr(
        codex_generator,
        "_imagegen_mutex",
        lambda *_args, **_kwargs: pytest.fail("Provider 不可用时不得等待生图锁"),
    )
    result = gen.generate(prompt, tmp_path / "out.png")
    assert result.success is False
    assert "不可用" in result.error


class _Proc:
    returncode = 0
    stdout = ""
    stderr = ""


def _codex_test_generator(tmp_path, monkeypatch) -> tuple[CodexImageGenerator, Path]:
    generated_root = tmp_path / "generated_images"
    generated_root.mkdir(exist_ok=True)
    prompt = tmp_path / "task" / "image_prompt.txt"
    prompt.parent.mkdir(exist_ok=True)
    prompt.write_text("包含引号 \"、换行\n和 $() 的完整 Prompt", encoding="utf-8")
    generator = CodexImageGenerator(
        codex_path="codex-test",
        generated_images_dir=str(generated_root),
    )
    monkeypatch.setattr(generator, "health_check", lambda: (True, "ok"))
    # 单元测试验证命令/产物契约，不应与 8766 的真实生图任务争抢系统 mutex。
    monkeypatch.setattr(codex_generator, "_imagegen_mutex", lambda *_args: nullcontext())
    monkeypatch.setattr(codex_generator, "_RECOVERY_POLL_ROUNDS", 0)
    return generator, prompt


def _attempt_paths(command: list[str]) -> tuple[Path, Path]:
    result_path = Path(command[command.index("--output-last-message") + 1])
    staging_path = result_path.with_name(
        result_path.name.replace("receipt-", "candidate-").replace(".json", ".png")
    )
    return staging_path, result_path


def _write_receipt(
    result_path: Path,
    image_path: Path | str,
    *,
    legacy: bool = False,
) -> None:
    payload = {
        "job_id": result_path.parent.name,
        "status": "success",
        "image_path": str(image_path),
        "error": "",
    }
    if legacy:
        payload = {"job_id": result_path.parent.name, "image_path": str(image_path)}
    result_path.write_text(
        json.dumps(payload),
        encoding="utf-8",
    )


def test_codex_prompt_hash_compares_raw_crlf_bytes(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    prompt.write_bytes("第一行\r\n第二行\r\n".encode("utf-8"))
    expected_hash = hashlib.sha256(prompt.read_bytes()).hexdigest()

    result = generator._generate_locked(
        prompt,
        prompt.parent / "daily_image.png",
        force=True,
        job_id="invalid!",
        prompt_sha256=expected_hash,
    )

    assert result.success is False
    assert "job_id" in result.error
    assert "Prompt 内容已变化" not in result.error


def test_codex_prompt_uses_stdin_and_explicit_output_contract(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    captured = {}

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs["input"]
        staging, result_path = _attempt_paths(command)
        generated = tmp_path / "generated_images" / "execution-1" / "final.png"
        generated.parent.mkdir()
        generated.write_bytes(_PNG_1PX)
        _write_receipt(result_path, generated.resolve())
        assert not staging.exists()
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    output = prompt.parent / "daily_image.png"
    result = generator.generate(prompt, output)

    assert result.success is True
    assert "--output-schema" in captured["command"]
    assert "--output-last-message" in captured["command"]
    assert captured["command"][-1] == "-"
    assert "包含引号" not in " ".join(captured["command"])
    assert "包含引号" in captured["input"]
    assert "复制到这个精确路径" not in captured["input"]
    assert "不要复制或另存" in captured["input"]
    assert "优先使用 1024×1536" in captured["input"]
    assert "其他竖版尺寸也可以直接采用" in captured["input"]
    assert "不要为了匹配尺寸裁切或拉伸" in captured["input"]
    assert "绝对路径" in captured["input"]
    assert "status=failed" in captured["input"]
    assert "禁止把错误说明伪装成 image_path" in captured["input"]
    assert result.detail["attempt_count"] == 1
    assert result.detail["recovery_status"] == "completed"


def test_codex_accepts_legacy_success_receipt(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)

    def fake_run(command, **kwargs):
        _, result_path = _attempt_paths(command)
        generated = tmp_path / "generated_images" / "legacy-success" / "final.png"
        generated.parent.mkdir()
        generated.write_bytes(_PNG_1PX)
        _write_receipt(result_path, generated.resolve(), legacy=True)
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is True
    assert result.detail["receipt_source"] == "structured_receipt"


def test_codex_explicit_failure_receipt_is_known_failure_without_retry(
    tmp_path, monkeypatch
):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        _, result_path = _attempt_paths(command)
        result_path.write_text(
            json.dumps(
                {
                    "job_id": result_path.parent.name,
                    "status": "failed",
                    "image_path": "",
                    "error": "connection failed: remote reset",
                }
            ),
            encoding="utf-8",
        )
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    job_id = "job-explicit-failure-001"
    result = generator.generate(
        prompt,
        prompt.parent / "daily_image.png",
        job_id=job_id,
    )
    manifest = json.loads(
        (prompt.parent / ".imagegen-jobs" / job_id / "attempt.json").read_text(
            encoding="utf-8"
        )
    )

    assert result.success is False
    assert calls == 1
    assert "connection failed" in result.error
    assert result.detail["outcome_unknown"] is False
    assert result.detail["recovery_status"] == "explicit_generation_failure"
    assert result.detail["candidate_diagnostics"] == []
    assert manifest["state"] == "exhausted"
    assert manifest["outcome"] == "explicit_failure"


def test_codex_keeps_success_after_post_promote_smoke_write_failure(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)

    def fake_run(command, **kwargs):
        _, result_path = _attempt_paths(command)
        generated = tmp_path / "generated_images" / "post-promote" / "final.png"
        generated.parent.mkdir()
        generated.write_bytes(_PNG_1PX)
        _write_receipt(result_path, generated.resolve())
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    monkeypatch.setattr(generator, "_is_project_output", lambda _path: True)
    monkeypatch.setattr(
        generator,
        "_save_last_smoke",
        lambda *_args: (_ for _ in ()).throw(OSError("disk busy")),
    )
    output = prompt.parent / "daily_image.png"

    result = generator.generate(prompt, output)

    assert result.success is True
    assert output.is_file()
    assert result.detail["post_promote_warnings"] == ["smoke_state:OSError"]


def test_codex_stdout_old_paths_are_not_candidates_and_hold_unknown(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    old = tmp_path / "generated_images" / "old.png"
    old.write_bytes(_PNG_1PX)
    calls = []

    class Proc(_Proc):
        stdout = json.dumps({"message": f"existing image {old}"})

    def fake_run(command, **kwargs):
        calls.append(command)
        return Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert len(calls) == 1
    assert result.detail["attempt_count"] == 1
    assert result.detail["outcome_unknown"] is True
    assert result.detail["candidate_diagnostics"] == []


def test_codex_rejects_structured_path_outside_allowed_roots(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    outside = tmp_path / "outside.png"
    outside.write_bytes(_PNG_1PX)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        _, result_path = _attempt_paths(command)
        _write_receipt(result_path, outside)
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert calls == 1
    assert result.detail["outcome_unknown"] is True
    assert result.detail["candidate_diagnostics"] == []


def test_codex_rejects_relative_structured_path(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        _, result_path = _attempt_paths(command)
        _write_receipt(result_path, "final.png")
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert calls == 1
    assert result.detail["outcome_unknown"] is True
    assert result.detail["candidate_diagnostics"] == []


@pytest.mark.parametrize("create_before", [False, True], ids=["missing", "stale"])
def test_codex_rejects_missing_or_stale_structured_path(tmp_path, monkeypatch, create_before):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    candidate = tmp_path / "generated_images" / "execution-old" / "final.png"
    candidate.parent.mkdir()
    if create_before:
        candidate.write_bytes(_PNG_1PX)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        _, result_path = _attempt_paths(command)
        _write_receipt(result_path, candidate.resolve())
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert calls == 1
    assert result.detail["outcome_unknown"] is True
    assert result.detail["candidate_diagnostics"] == []


def test_codex_does_not_guess_between_different_images(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        execution = tmp_path / "generated_images" / f"execution-{calls}"
        execution.mkdir()
        (execution / "first.png").write_bytes(_PNG_1PX + bytes([calls]))
        (execution / "second.png").write_bytes(_PNG_1PX + b"different" + bytes([calls]))
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert calls == 1
    assert result.detail["outcome_unknown"] is True
    assert result.detail["stage"] == "ambiguous"
    assert len(result.detail["candidate_diagnostics"]) == 2


def test_codex_structured_path_selects_one_of_multiple_candidates(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)

    def fake_run(command, **kwargs):
        _, result_path = _attempt_paths(command)
        execution = tmp_path / "generated_images" / "execution-1"
        execution.mkdir()
        selected = execution / "selected.png"
        selected.write_bytes(_PNG_1PX)
        (execution / "alternate.png").write_bytes(_PNG_1PX + b"different")
        _write_receipt(result_path, selected.resolve())
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    output = prompt.parent / "daily_image.png"
    result = generator.generate(prompt, output)

    assert result.success is True
    assert len(result.detail["candidate_diagnostics"]) == 2
    structured = next(
        item
        for item in result.detail["candidate_diagnostics"]
        if "structured" in item["sources"]
    )
    assert structured["relative_path"] == "execution-1/selected.png"


def test_codex_keeps_distinct_paths_even_when_candidate_hashes_match(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)

    def fake_run(command, **kwargs):
        staging, result_path = _attempt_paths(command)
        generated = tmp_path / "generated_images" / "execution-1" / "final.png"
        generated.parent.mkdir()
        generated.write_bytes(_PNG_1PX)
        staging.write_bytes(_PNG_1PX)
        _write_receipt(result_path, generated)
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is True
    assert len(result.detail["candidate_diagnostics"]) == 2
    assert sum(
        "structured" in item["sources"]
        for item in result.detail["candidate_diagnostics"]
    ) == 1


def test_codex_staged_image_without_matching_receipt_is_not_auto_claimed(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        staging, _ = _attempt_paths(command)
        staging.write_bytes(_PNG_1PX)
        raise subprocess.TimeoutExpired(command, generator.timeout)

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert calls == 1
    assert result.detail["attempt_count"] == 1
    assert result.detail["outcome_unknown"] is True
    assert len(result.detail["candidate_diagnostics"]) == 1


def test_codex_timeout_recovers_unique_candidate_from_same_thread(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    thread_id = "01a03-safe-thread"
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_start"](12345)
        kwargs["on_event"]({"type": "thread.started", "thread_id": thread_id})
        generated = tmp_path / "generated_images" / thread_id / "only.png"
        generated.parent.mkdir()
        generated.write_bytes(_PNG_1PX)
        raise subprocess.TimeoutExpired(command, generator.timeout, stderr="token=secret-value")

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    monkeypatch.setattr(generator, "_pid_is_running", lambda _pid: False)

    result = generator.generate(
        prompt,
        prompt.parent / "daily_image.png",
        job_id="job-thread-timeout-001",
    )

    assert result.success is True
    assert calls == 1
    assert result.detail["codex_thread_id"] == thread_id
    assert result.detail["receipt_source"] == "codex_thread_scan"
    assert result.detail["recovery_status"] == "recovered_after_timeout"
    assert "secret-value" not in result.detail["codex_stderr_tail"]


def test_codex_thread_candidate_outside_matching_directory_stays_unknown(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_event"]({"type": "thread.started", "thread_id": "thread-expected-001"})
        generated = tmp_path / "generated_images" / "thread-other-001" / "only.png"
        generated.parent.mkdir()
        generated.write_bytes(_PNG_1PX)
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)

    result = generator.generate(
        prompt,
        prompt.parent / "daily_image.png",
        job_id="job-thread-mismatch-001",
    )

    assert result.success is False
    assert calls == 1
    assert result.detail["outcome_unknown"] is True


def test_codex_multiple_candidates_in_same_thread_stay_unknown(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    thread_id = "thread-multiple-001"

    def fake_run(command, **kwargs):
        kwargs["on_event"]({"type": "thread.started", "thread_id": thread_id})
        generated = tmp_path / "generated_images" / thread_id
        generated.mkdir()
        (generated / "first.png").write_bytes(_PNG_1PX)
        (generated / "second.png").write_bytes(_PNG_1PX)
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)

    result = generator.generate(
        prompt,
        prompt.parent / "daily_image.png",
        job_id="job-thread-multiple-001",
    )

    assert result.success is False
    assert result.detail["stage"] == "ambiguous"
    assert result.detail["outcome_unknown"] is True


def test_codex_modified_old_file_in_thread_directory_is_not_new_candidate(
    tmp_path, monkeypatch
):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    thread_id = "thread-existing-001"
    generated = tmp_path / "generated_images" / thread_id / "old.png"
    generated.parent.mkdir()
    generated.write_bytes(_PNG_1PX)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        kwargs["on_event"]({"type": "thread.started", "thread_id": thread_id})
        generated.write_bytes(_PNG_1PX + b"modified")
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)

    result = generator.generate(
        prompt,
        prompt.parent / "daily_image.png",
        job_id="job-thread-existing-001",
    )

    assert result.success is False
    assert calls == 1
    assert result.detail["outcome_unknown"] is True
    assert all(
        "thread" not in item["sources"]
        for item in result.detail["candidate_diagnostics"]
    )


def test_codex_unknown_result_survives_next_call_without_second_process(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    job_id = "job-repeat-001"
    first = generator.generate(prompt, prompt.parent / "daily_image.png", job_id=job_id)
    second = generator.generate(prompt, prompt.parent / "daily_image.png", job_id=job_id)
    manifest = json.loads((prompt.parent / ".imagegen-jobs" / job_id / "attempt.json").read_text(encoding="utf-8"))

    assert first.success is False
    assert second.success is False
    assert calls == 1
    assert manifest["state"] == "result_unknown"
    assert "包含引号" not in json.dumps(manifest, ensure_ascii=False)


def test_codex_completed_manifest_with_missing_output_requires_explicit_force(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    job_id = "job-complete-001"
    job_dir = prompt.parent / ".imagegen-jobs" / job_id
    attempt = generator._new_attempt(prompt.parent, 1, [], job_id=job_id, job_dir=job_dir)
    attempt.update(state="completed", outcome="completed", finished_at="2026-08-25T12:00:00+08:00")
    generator._write_attempt_manifest(job_dir / "attempt.json", attempt)
    monkeypatch.setattr(
        codex_generator,
        "_run_codex_process",
        lambda *args, **kwargs: pytest.fail("已完成产物缺失时不得自动再次调用"),
    )

    result = generator.generate(prompt, prompt.parent / "daily_image.png", job_id=job_id)

    assert result.success is False
    assert result.detail["recovery_status"] == "completed_output_missing"


def test_codex_interrupted_staging_without_receipt_stays_result_unknown(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    job_id = "job-interrupted-001"
    job_dir = prompt.parent / ".imagegen-jobs" / job_id
    attempt = generator._new_attempt(prompt.parent, 1, [], job_id=job_id, job_dir=job_dir)
    Path(attempt["staging_path"]).write_bytes(_PNG_1PX)
    generator._write_attempt_manifest(job_dir / "attempt.json", attempt)
    monkeypatch.setattr(
        codex_generator,
        "_run_codex_process",
        lambda *args, **kwargs: pytest.fail("已恢复中断产物，不应启动新进程"),
    )

    result = generator.generate(prompt, prompt.parent / "daily_image.png", job_id=job_id)

    assert result.success is False
    assert result.detail["outcome_unknown"] is True
    assert result.detail["recovery_status"] == "interrupted_result_unknown"


def test_codex_rejects_corrupt_image_without_overwriting_existing(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    output = prompt.parent / "daily_image.png"
    output.write_bytes(_PNG_1PX)
    original = output.read_bytes()

    def fake_run(command, **kwargs):
        staging, _ = _attempt_paths(command)
        staging.write_bytes(b"not-an-image")
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, output, force=True)

    assert result.success is False
    assert output.read_bytes() == original


def test_codex_accepts_valid_non_default_dimensions_without_second_generation(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    prompt.write_text("优先生成 1024×1536 群聊漫画", encoding="utf-8")
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        _, result_path = _attempt_paths(command)
        generated = tmp_path / "generated_images" / f"flexible-size-{calls}" / "final.png"
        generated.parent.mkdir()
        Image.new("RGB", (864, 1821), (30, 60, 90)).save(generated, format="PNG")
        _write_receipt(result_path, generated.resolve())
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    output = prompt.parent / "daily_image.png"
    result = generator.generate(prompt, output, job_id="job-flexible-size-001")

    assert result.success is True
    assert calls == 1
    assert output.exists()
    with Image.open(output) as image:
        image.load()
        assert image.size == (864, 1821)
    assert result.detail["recovery_status"] == "completed"
    assert result.detail["width"] == 864
    assert result.detail["height"] == 1821


def test_codex_timeout_terminates_process_tree(monkeypatch):
    class FakeProcess:
        pid = 12345
        returncode = None

        def __init__(self):
            self.wait_calls = 0
            self.stdin = io.StringIO()
            self.stdout = io.StringIO("")
            self.stderr = io.StringIO("")

        def wait(self, **kwargs):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise subprocess.TimeoutExpired("codex", 1)
            return 1

        def poll(self):
            return None

        def kill(self):
            return None

    process = FakeProcess()
    terminated = []
    monkeypatch.setattr(codex_generator.subprocess, "Popen", lambda *a, **k: process)
    monkeypatch.setattr(codex_generator, "_terminate_process_tree", lambda value: terminated.append(value))

    with pytest.raises(subprocess.TimeoutExpired):
        codex_generator._run_codex_process(
            ["codex", "exec"],
            timeout=1,
            cwd=".",
            input="$imagegen test",
            env={},
        )

    assert terminated == [process]
    assert process.wait_calls == 2


def test_codex_imagegen_mutex_serializes_concurrent_requests(monkeypatch):
    # 使用独立的进程内锁和 Windows mutex 名，避免与 8766 的真实生图互相阻塞。
    monkeypatch.setattr(codex_generator, "_PROCESS_IMAGE_LOCK", threading.Lock())
    monkeypatch.setattr(
        codex_generator,
        "_MUTEX_NAME",
        f"Local\\GroupBrief.ImageGen.Test.{id(monkeypatch)}",
    )
    from app.image.codex_generator import _imagegen_mutex

    active = 0
    max_active = 0
    guard = threading.Lock()

    def worker():
        nonlocal active, max_active
        with _imagegen_mutex(2):
            with guard:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.05)
            with guard:
                active -= 1

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert max_active == 1
