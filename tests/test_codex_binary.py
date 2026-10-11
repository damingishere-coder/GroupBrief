from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.config.settings import Settings
from app.core.codex_binary import resolve_codex_binary
from app.image.codex_generator import CodexImageGenerator
from app.providers.ai.codex import CodexGPTProvider


@pytest.fixture
def desktop_install(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    root = tmp_path / "OpenAI" / "Codex" / "bin"
    root.mkdir(parents=True)
    return root


def _binary(root: Path, version: str, timestamp: int) -> Path:
    path = root / version / "codex.exe"
    path.parent.mkdir(exist_ok=True)
    path.write_bytes(b"test")
    os.utime(path, (timestamp, timestamp))
    return path


@pytest.mark.parametrize("provider", [CodexGPTProvider, CodexImageGenerator])
def test_both_providers_recover_removed_desktop_version(desktop_install, provider):
    root = desktop_install
    _binary(root, "1111111111111111", 100)
    current = _binary(root, "2222222222222222", 200)
    # 根目录旧启动器和非版本目录即使更新，也不能覆盖正确版本。
    (root / "codex.exe").write_bytes(b"old launcher")
    _binary(root, "unrelated", 300)
    settings = Settings(_env_file=None, codex_path=str(root / "0000000000000000" / "codex.exe"))
    assert provider(settings=settings)._resolve_binary() == str(current.resolve())


def test_existing_explicit_version_stays_pinned(desktop_install):
    pinned = _binary(desktop_install, "1111111111111111", 100)
    _binary(desktop_install, "2222222222222222", 200)
    assert Path(resolve_codex_binary(str(pinned))) == pinned


@pytest.mark.parametrize("relative", ["custom/codex.exe", "1111111111111111/other.exe"])
def test_invalid_desktop_paths_do_not_switch_binary(desktop_install, relative):
    _binary(desktop_install, "2222222222222222", 200)
    assert resolve_codex_binary(str(desktop_install / relative)) == ""


def test_missing_custom_install_does_not_use_desktop_binary(desktop_install, tmp_path):
    _binary(desktop_install, "2222222222222222", 200)
    assert resolve_codex_binary(str(tmp_path / "custom" / "1111111111111111" / "codex.exe")) == ""


def test_removed_version_without_replacement_remains_unavailable(desktop_install):
    (desktop_install / "codex.exe").write_bytes(b"old launcher")
    assert resolve_codex_binary(str(desktop_install / "1111111111111111" / "codex.exe")) == ""


def test_path_command_keeps_normal_resolution(monkeypatch):
    monkeypatch.setattr("app.core.codex_binary.shutil.which", lambda command: "installed/codex.cmd")
    assert resolve_codex_binary("codex") == "installed/codex.cmd"
