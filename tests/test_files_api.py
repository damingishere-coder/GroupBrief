from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import files as files_api
from app.config.settings import get_settings


@pytest.fixture(scope="module")
def files_client(tmp_path_factory):
    output_dir = tmp_path_factory.mktemp("files-api") / "output"
    api = FastAPI()
    api.include_router(files_api.router)
    api.dependency_overrides[get_settings] = lambda: SimpleNamespace(output_dir=output_dir)
    with TestClient(api) as client:
        yield client, output_dir


def test_dates_only_returns_valid_date_directories(files_client):
    client, output_dir = files_client
    (output_dir / "2026-08-24").mkdir(parents=True)
    (output_dir / "群目录" / "2026-08-24").mkdir(parents=True)
    (output_dir / "2026-02-30").mkdir()

    response = client.get("/api/files/dates")

    assert response.status_code == 200
    assert response.json() == ["2026-08-24"]


def test_list_day_returns_relative_path_without_host_path(files_client):
    client, output_dir = files_client
    group_dir = output_dir / "2026-08-24" / "安全群"
    group_dir.mkdir(parents=True)
    (group_dir / "handoff.json").write_text(json.dumps({"version": 1}), encoding="utf-8")
    (group_dir / "ranking.txt").write_text("排行榜", encoding="utf-8")

    response = client.get("/api/files/2026-08-24")

    assert response.status_code == 200
    assert response.json() == [
        {
            "date": "2026-08-24",
            "directory": "安全群",
            "path": "2026-08-24/安全群",
            "handoff": {"version": 1},
            "files": ["handoff.json", "ranking.txt"],
        }
    ]
    assert str(output_dir.resolve()) not in response.text


@pytest.mark.parametrize("report_date", ["2026-02-30", "..%5Clogs", "C:%5CWindows"])
def test_list_day_rejects_invalid_or_path_like_dates(files_client, report_date):
    client, _ = files_client
    assert client.get(f"/api/files/{report_date}").status_code == 400


def test_raw_file_uses_allowlist_and_blocks_sibling_prefix_escape(files_client):
    client, output_dir = files_client
    day_dir = output_dir / "2026-08-24" / "原始文件群"
    day_dir.mkdir(parents=True)
    (day_dir / "ranking.txt").write_text("合法排行榜", encoding="utf-8")
    (day_dir / "private.txt").write_text("secret", encoding="utf-8")

    sibling = output_dir / "2026-08-24-extra"
    sibling.mkdir(parents=True)
    (sibling / "ranking.txt").write_text("不应读取", encoding="utf-8")

    allowed = client.get("/api/files/2026-08-24/原始文件群/raw/ranking.txt")
    assert allowed.status_code == 200
    assert allowed.text == "合法排行榜"
    assert client.get("/api/files/2026-08-24/原始文件群/raw/private.txt").status_code == 400
    assert (
        client.get("/api/files/2026-08-24/..%5C2026-08-24-extra/raw/ranking.txt").status_code
        == 400
    )
