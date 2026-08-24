from __future__ import annotations

from pathlib import Path

import pytest

from app.core.path_security import PathBoundaryError, resolve_within, validate_path_label
from app.v2.run_store import RunStore


def test_resolve_within_accepts_child_and_rejects_parent_or_sibling(tmp_path):
    root = tmp_path / "output" / "2026-08-24"
    root.mkdir(parents=True)

    child = resolve_within(root, "群A", "ranking.txt")
    assert child == (root / "群A" / "ranking.txt").resolve()

    with pytest.raises(PathBoundaryError):
        resolve_within(root, Path("..") / "logs" / "ranking.txt")
    with pytest.raises(PathBoundaryError):
        resolve_within(root, Path("..") / "2026-08-24-extra" / "ranking.txt")
    with pytest.raises(PathBoundaryError):
        resolve_within(root, root)


@pytest.mark.parametrize(
    "value",
    [".", "..", "../logs", r"..\logs", r"C:\Windows", r"\\server\share", "/etc"],
)
def test_validate_path_label_rejects_navigation_and_absolute_paths(value):
    with pytest.raises(PathBoundaryError):
        validate_path_label(value, field_name="group_name")


def test_validate_path_label_keeps_ordinary_display_name_punctuation():
    assert validate_path_label("设计/开发群（A.B）", field_name="group_name") == "设计/开发群（A.B）"


@pytest.mark.parametrize("group_name", [".", "..", "../logs", r"..\logs", r"C:\Windows", r"\\server\share"])
def test_run_store_rejects_unsafe_group_paths(tmp_path, group_name):
    store = RunStore(tmp_path / "output")
    with pytest.raises(PathBoundaryError):
        store.group_dir(group_name, "2026-08-24")


def test_run_store_group_directory_is_resolved_under_output(tmp_path):
    root = tmp_path / "output"
    path = RunStore(root).group_dir("设计/开发群", "2026-08-24")
    assert path == (root / "设计-开发群" / "2026-08-24").resolve()
    assert path.is_relative_to(root.resolve())
