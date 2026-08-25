"""V2 P5：图片生成任务单元测试。

验证：图片格式识别 / verify_image / 串行队列顺序与隔离 / 已存在跳过 /
CodexImageGenerator health_check 不可用判定。
"""

from __future__ import annotations

from contextlib import nullcontext
import json
from pathlib import Path
import subprocess
import threading
import time

import pytest

from app.image import codex_generator
from app.image.codex_generator import CodexImageGenerator
from app.image.image_task import (
    ImageJob,
    SerialImageQueue,
    copy_generated_image,
    detect_image_format,
    verify_image,
)

# 1x1 透明 PNG
_PNG_1PX = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63f8cfc0f01f00050001fff83f240000000049454e44ae426082"
)


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
    assert "不是可识别的图片格式" in detail


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


def test_single_failure_does_not_block_others(tmp_path):
    queue = SerialImageQueue()
    jobs = [
        _job(tmp_path, "群1", FakeGenerator(fail=True)),
        _job(tmp_path, "群2", FakeGenerator()),
        _job(tmp_path, "群3", FakeGenerator(fail=True)),
    ]
    results = queue.run_all(jobs)
    assert [r["status"] for r in results] == ["failed", "success", "failed"]
    assert results[0]["error_type"] == "IMAGE_GENERATION_FAILED"
    # 失败的群不产出图片，其他群不受影响
    ok2, _ = verify_image(jobs[1].output_path)
    assert ok2 is True


def test_skip_when_image_exists(tmp_path):
    gen = FakeGenerator()
    job = _job(tmp_path, "群1", gen)
    # 先成功生成一次
    assert job.run()["status"] == "success"
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
    staging_path = result_path.with_name(result_path.name.replace(".result.json", ".png"))
    return staging_path, result_path


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
        result_path.write_text(json.dumps({"image_path": str(generated.resolve())}), encoding="utf-8")
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
    assert "绝对路径" in captured["input"]
    assert result.detail["attempt_count"] == 1
    assert result.detail["recovery_status"] == "completed"


def test_codex_stdout_old_paths_are_not_candidates_and_retry_once(tmp_path, monkeypatch):
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
    assert len(calls) == 2
    assert result.detail["attempt_count"] == 2
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
        result_path.write_text(json.dumps({"image_path": str(outside)}), encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert calls == 2
    assert result.detail["candidate_diagnostics"] == []


def test_codex_rejects_relative_structured_path(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        _, result_path = _attempt_paths(command)
        result_path.write_text(json.dumps({"image_path": "final.png"}), encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert calls == 2
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
        result_path.write_text(json.dumps({"image_path": str(candidate.resolve())}), encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is False
    assert calls == 2
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
    assert calls == 2
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
        result_path.write_text(json.dumps({"image_path": str(selected.resolve())}), encoding="utf-8")
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


def test_codex_deduplicates_staging_and_generated_copy_by_hash(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)

    def fake_run(command, **kwargs):
        staging, result_path = _attempt_paths(command)
        generated = tmp_path / "generated_images" / "execution-1" / "final.png"
        generated.parent.mkdir()
        generated.write_bytes(_PNG_1PX)
        staging.write_bytes(_PNG_1PX)
        result_path.write_text(json.dumps({"image_path": str(generated)}), encoding="utf-8")
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is True
    assert len(result.detail["candidate_diagnostics"]) == 1
    assert result.detail["candidate_diagnostics"][0]["sources"] == ["staging", "structured", "scan"]


def test_codex_recovers_single_staged_image_after_timeout(tmp_path, monkeypatch):
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

    assert result.success is True
    assert calls == 1
    assert result.detail["attempt_count"] == 1
    assert result.detail["recovery_status"] == "recovered_after_timeout"


def test_codex_retry_budget_survives_next_call(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    calls = 0

    def fake_run(command, **kwargs):
        nonlocal calls
        calls += 1
        return _Proc()

    monkeypatch.setattr(codex_generator, "_run_codex_process", fake_run)
    first = generator.generate(prompt, prompt.parent / "daily_image.png")
    second = generator.generate(prompt, prompt.parent / "daily_image.png")
    manifest = json.loads((prompt.parent / ".codex-image-attempt.json").read_text(encoding="utf-8"))

    assert first.success is False
    assert second.success is False
    assert calls == 2
    assert manifest["state"] == "exhausted"
    assert "包含引号" not in json.dumps(manifest, ensure_ascii=False)


def test_codex_recovers_interrupted_attempt_before_new_process(tmp_path, monkeypatch):
    generator, prompt = _codex_test_generator(tmp_path, monkeypatch)
    attempt = generator._new_attempt(prompt.parent, 1, [])
    Path(attempt["staging_path"]).write_bytes(_PNG_1PX)
    generator._write_attempt_manifest(prompt.parent / ".codex-image-attempt.json", attempt)
    monkeypatch.setattr(
        codex_generator,
        "_run_codex_process",
        lambda *args, **kwargs: pytest.fail("已恢复中断产物，不应启动新进程"),
    )

    result = generator.generate(prompt, prompt.parent / "daily_image.png")

    assert result.success is True
    assert result.detail["recovery_status"] == "recovered_after_interruption"


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
    result = generator.generate(prompt, output)

    assert result.success is False
    assert output.read_bytes() == original


def test_codex_timeout_terminates_process_tree(monkeypatch):
    class FakeProcess:
        pid = 12345
        returncode = None

        def __init__(self):
            self.communicate_calls = 0

        def communicate(self, **kwargs):
            self.communicate_calls += 1
            if self.communicate_calls == 1:
                raise subprocess.TimeoutExpired("codex", 1)
            return ("", "")

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
    assert process.communicate_calls == 2


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
