from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.ai.prompt_templates import DEFAULT_IMAGE_PROMPT_TEMPLATE

client = TestClient(app)


def _delete_by_prefix(prefix: str) -> None:
    for group in client.get("/api/groups").json():
        if group["display_name"].startswith(prefix):
            client.delete(f"/api/groups/{group['id']}")


def test_group_prompt_override_is_isolated_and_not_exposed_in_list():
    prefix = "Prompt隔离测试"
    with client:
        _delete_by_prefix(prefix)
        first = client.post("/api/groups", json={"display_name": f"{prefix}A"}).json()["id"]
        second = client.post("/api/groups", json={"display_name": f"{prefix}B"}).json()["id"]
        try:
            first_config = client.get(f"/api/groups/{first}/image-prompt").json()
            second_config = client.get(f"/api/groups/{second}/image-prompt").json()
            assert first_config["source"] == second_config["source"] == "global"
            assert first_config["content"].count("【漫画分镜】") == 1
            assert first_config["preview"].count("【漫画分镜】") == 1

            custom = DEFAULT_IMAGE_PROMPT_TEMPLATE.replace(
                "生成一张竖版微信群日报漫画信息图。",
                "为 {{group_name}} 生成一张竖版微信群日报漫画信息图。",
            )
            saved = client.put(
                f"/api/groups/{first}/image-prompt",
                json={
                    "content": custom,
                    "inherit_global": False,
                    "image_theme": "pink",
                    "expected_revision": first_config["revision"],
                },
            )
            assert saved.status_code == 200
            assert saved.json()["source"] == "group_override"
            assert client.get(f"/api/groups/{second}/image-prompt").json()["source"] == "global"

            listed = client.get("/api/groups").json()
            first_row = next(item for item in listed if item["id"] == first)
            second_row = next(item for item in listed if item["id"] == second)
            assert first_row["has_image_prompt_override"] is True
            assert second_row["has_image_prompt_override"] is False
            assert "image_prompt_override" not in first_row

            restored = client.put(
                f"/api/groups/{first}/image-prompt",
                json={
                    "content": "",
                    "inherit_global": True,
                    "image_theme": "blue_white",
                    "expected_revision": saved.json()["revision"],
                },
            )
            assert restored.status_code == 200
            assert restored.json()["source"] == "global"
        finally:
            client.delete(f"/api/groups/{first}")
            client.delete(f"/api/groups/{second}")


def test_group_prompt_revision_conflict_returns_409():
    prefix = "Prompt版本测试"
    with client:
        _delete_by_prefix(prefix)
        group_id = client.post("/api/groups", json={"display_name": prefix}).json()["id"]
        try:
            response = client.put(
                f"/api/groups/{group_id}/image-prompt",
                json={
                    "content": "【任务】\n新模板",
                    "inherit_global": False,
                    "image_theme": "blue_white",
                    "expected_revision": "stale-revision",
                },
            )
            assert response.status_code == 409
        finally:
            client.delete(f"/api/groups/{group_id}")


def test_named_theme_preview_only_replaces_canonical_theme_section():
    original = DEFAULT_IMAGE_PROMPT_TEMPLATE.replace(
        "{{overall_visual}}",
        "固定群聊漫画要求。\n\n根据当天真实聊天内容自由选择统一视觉风格。",
    ).replace("{{panels}}", "【版面1】\n张三说今天完成 3 项工作。")
    with client:
        response = client.post(
            "/api/v2/image-themes/resolve",
            json={
                "image_theme": "gouache_editorial",
                "prompt": original,
                "group_id": "group-1",
                "run_date": "2026-08-24",
            },
        )
        assert response.status_code == 200
        resolved = response.json()
        assert resolved["actual_key"] == "gouache_editorial"
        assert "不透明水粉社论" in resolved["prompt"]
        assert "张三说今天完成 3 项工作。" in resolved["prompt"]
        assert resolved["prompt"].count("【漫画分镜】") == 1
