"""Prevent public release surfaces from advertising different versions."""

import json
from pathlib import Path

from app.main import app, version
from app.version import APP_VERSION


def test_release_version_surfaces_agree():
    root = Path(__file__).resolve().parents[1]
    package = json.loads((root / "frontend/package.json").read_text(encoding="utf-8"))
    lock = json.loads((root / "frontend/package-lock.json").read_text(encoding="utf-8"))
    assert version()["version"] == app.version == APP_VERSION
    assert package["version"] == lock["version"] == lock["packages"][""]["version"] == APP_VERSION
    assert f"GroupBrief v{APP_VERSION}" in (root / "start_windows.bat").read_text(encoding="utf-8")
