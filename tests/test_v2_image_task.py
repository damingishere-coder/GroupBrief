"""V2 P5：图片生成任务单元测试。

验证：图片格式识别 / verify_image / 串行队列顺序与隔离 / 已存在跳过 /
CodexImageGenerator health_check 不可用判定。
"""

from __future__ import annotations

from pathlib import Path
import threading
import time

import pytest

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


def test_codex_generate_returns_failure_when_unavailable(tmp_path):
    gen = CodexImageGenerator(codex_path="definitely-not-existing-codex-cmd")
    prompt = tmp_path / "p.txt"
    prompt.write_text("test", encoding="utf-8")
    result = gen.generate(prompt, tmp_path / "out.png")
    assert result.success is False
    assert "不可用" in result.error


def test_codex_prompt_is_passed_via_stdin_not_command_line(tmp_path, monkeypatch):
    generated = tmp_path / "generated.png"
    generated.write_bytes(_PNG_1PX)
    prompt = tmp_path / "image_prompt.txt"
    prompt.write_text("包含引号 \"、换行\n和 $() 的完整 Prompt", encoding="utf-8")
    captured = {}

    class Proc:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        captured["command"] = command
        captured["input"] = kwargs.get("input")
        return Proc()

    gen = CodexImageGenerator(codex_path="codex-test")
    monkeypatch.setattr(gen, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(gen, "_snapshot", lambda: {})
    monkeypatch.setattr(gen, "_scan_new", lambda before: [generated])
    monkeypatch.setattr("app.image.codex_generator.subprocess.run", fake_run)

    result = gen.generate(prompt, tmp_path / "daily_image.png")

    assert result.success
    assert captured["command"][-1] == "-"
    assert "包含引号" not in " ".join(captured["command"])
    assert captured["input"] == "$imagegen 包含引号 \"、换行\n和 $() 的完整 Prompt"


def test_codex_rejects_multiple_new_images_as_ambiguous(tmp_path, monkeypatch):
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(_PNG_1PX)
    second.write_bytes(_PNG_1PX + b"different-image-content")
    prompt = tmp_path / "image_prompt.txt"
    prompt.write_text("test", encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    gen = CodexImageGenerator(codex_path="codex-test")
    monkeypatch.setattr(gen, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(gen, "_snapshot", lambda: {})
    monkeypatch.setattr(gen, "_scan_new", lambda before: [first, second])
    monkeypatch.setattr("app.image.codex_generator.subprocess.run", lambda *a, **k: Proc())

    result = gen.generate(prompt, tmp_path / "daily_image.png")

    assert result.success is False
    assert result.detail["stage"] == "ambiguous"
    assert not (tmp_path / "daily_image.png").exists()


def test_codex_deduplicates_identical_image_copies(tmp_path, monkeypatch):
    first = tmp_path / "generated-home.png"
    second = tmp_path / "task-copy.png"
    first.write_bytes(_PNG_1PX)
    second.write_bytes(_PNG_1PX)
    prompt = tmp_path / "image_prompt.txt"
    prompt.write_text("test", encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    gen = CodexImageGenerator(codex_path="codex-test")
    monkeypatch.setattr(gen, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(gen, "_snapshot", lambda: {})
    monkeypatch.setattr(gen, "_scan_new", lambda before: [first, second])
    monkeypatch.setattr("app.image.codex_generator.subprocess.run", lambda *a, **k: Proc())

    output = tmp_path / "daily_image.png"
    result = gen.generate(prompt, output)

    assert result.success is True
    assert output.exists()
    assert result.detail["format"] == "png"
    assert result.detail["width"] == 1
    assert result.detail["height"] == 1


def test_codex_rejects_corrupt_image_without_overwriting_existing(tmp_path, monkeypatch):
    corrupt = tmp_path / "corrupt.png"
    corrupt.write_bytes(b"not-an-image")
    output = tmp_path / "daily_image.png"
    output.write_bytes(_PNG_1PX)
    original = output.read_bytes()
    prompt = tmp_path / "image_prompt.txt"
    prompt.write_text("test", encoding="utf-8")

    class Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    gen = CodexImageGenerator(codex_path="codex-test")
    monkeypatch.setattr(gen, "health_check", lambda: (True, "ok"))
    monkeypatch.setattr(gen, "_snapshot", lambda: {})
    monkeypatch.setattr(gen, "_scan_new", lambda before: [corrupt])
    monkeypatch.setattr("app.image.codex_generator.subprocess.run", lambda *a, **k: Proc())

    result = gen.generate(prompt, output)

    assert result.success is False
    assert output.read_bytes() == original


def test_codex_imagegen_mutex_serializes_concurrent_requests():
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
