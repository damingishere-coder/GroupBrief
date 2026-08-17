"""P6 测试：群名归一化、真实导出群名解析、绑定 API（不依赖外部数据）。"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from sqlmodel import SQLModel, Session, create_engine, select
from starlette.testclient import TestClient

from app.api import groups as groups_api
from app.db import repository as repo
from app.db.models import Group
from app.providers.history.base import (
    ChatHistoryProvider,
    GroupInfo,
    ProviderHealth,
    ProviderStatus,
)
from app.providers.history.mock import MockProvider
from app.services.history_service import GroupMatch, HistoryService, normalize_name


class StubProvider(ChatHistoryProvider):
    """健康且非 Mock 的假导出 Provider，仅用于测试。"""

    name = "stub_export"

    def __init__(self, groups: list[GroupInfo], health: ProviderHealth | None = None):
        self._groups = groups
        self._health = health or ProviderHealth(self.name, ProviderStatus.OK, "ok")

    def health_check(self) -> ProviderHealth:
        return self._health

    def list_groups(self) -> list[GroupInfo]:
        return self._groups

    def fetch_messages(self, *args, **kwargs):
        raise NotImplementedError


# ---------- 归一化 ----------

def test_normalize_name_basic():
    assert normalize_name("产品经理交流群") == "产品经理交流群"
    assert normalize_name("Eason张UED-4群") == normalize_name("Eason 张 UED-4 群")
    assert normalize_name("ＡＢＣｄ") == "abcd"  # NFKC + 大小写折叠
    assert normalize_name("  Product 群  ") == "product群"
    assert normalize_name("") == ""


def test_normalize_removes_joiner_and_variation():
    assert normalize_name("a\ufe0fb") == "ab"  # emoji 变体选择符
    assert normalize_name("a\u200db") == "ab"  # 零宽连接符
    assert normalize_name("a\u200bb") == "ab"  # 零宽空格
    assert normalize_name("产品群\ufe0f") == normalize_name("产品群")
    # 基础 emoji 字符本身保留，可区分群名
    assert normalize_name("群聊\U0001F600") != normalize_name("群聊")


# ---------- 服务层解析 ----------

def test_resolve_exact_first_then_partial():
    service = HistoryService()
    service.providers = [
        StubProvider(
            [
                GroupInfo("real-1", "产品经理交流群", 10),
                GroupInfo("real-2", "产品经理", 5),
            ]
        )
    ]
    matches = service.resolve_group_names("产品经理")
    assert [m.group_id for m in matches] == ["real-2", "real-1"]
    assert matches[0].match_type == "exact"
    assert matches[1].match_type == "partial"
    assert matches[0].provider == "stub_export"


def test_resolve_partial_only():
    service = HistoryService()
    service.providers = [StubProvider([GroupInfo("real-1", "产品经理交流群", 10)])]
    matches = service.resolve_group_names("产品经理")
    assert len(matches) == 1
    assert matches[0].match_type == "partial"


def test_resolve_no_match_returns_empty():
    service = HistoryService()
    service.providers = [StubProvider([GroupInfo("real-1", "产品经理交流群", 10)])]
    assert service.resolve_group_names("不存在的群") == []


def test_resolve_never_returns_mock_fixtures():
    service = HistoryService()
    service.providers = [
        StubProvider([GroupInfo("real-1", "产品经理交流群", 10)]),
        MockProvider(),
    ]
    matches = service.resolve_group_names("产品经理交流群")
    # Mock fixtures 里有同名 group-b，但解析路径绝不含 Mock
    assert [m.group_id for m in matches] == ["real-1"]
    assert all(m.provider == "stub_export" for m in matches)


def test_resolve_skips_unhealthy_provider():
    bad = StubProvider(
        [GroupInfo("real-1", "产品经理交流群", 10)],
        health=ProviderHealth("stub_export", ProviderStatus.UNAVAILABLE, "不可用"),
    )
    service = HistoryService()
    service.providers = [bad]
    assert service.resolve_group_names("产品经理交流群") == []


def test_resolve_dedupes_same_group():
    service = HistoryService()
    service.providers = [
        StubProvider([GroupInfo("real-1", "产品经理", 10)]),
        StubProvider([GroupInfo("real-1", "产品经理", 10)]),
    ]
    matches = service.resolve_group_names("产品经理")
    assert len(matches) == 1
    assert matches[0].group_id == "real-1"


# ---------- HTTP 层 ----------

@pytest.fixture
def client():
    from sqlalchemy.pool import StaticPool

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)

    def override_session():
        with Session(engine) as session:
            yield session

    app = FastAPI()
    app.include_router(groups_api.router)
    app.dependency_overrides[repo.get_session] = override_session

    with TestClient(app) as test_client:
        yield test_client, engine


def test_resolve_api_blank_name_400(client):
    test_client, _ = client
    assert test_client.get("/api/groups/resolve", params={"name": "  "}).status_code == 400
    assert test_client.get("/api/groups/resolve").status_code == 400


def test_resolve_api_returns_typed_matches(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        HistoryService,
        "resolve_group_names",
        lambda self, name: [
            GroupMatch("real-1", "产品经理交流群", 10, "stub_export", "exact")
        ],
    )
    resp = test_client.get("/api/groups/resolve", params={"name": "产品经理"})
    assert resp.status_code == 200
    assert resp.json() == [
        {
            "id": "real-1",
            "name": "产品经理交流群",
            "member_count": 10,
            "provider": "stub_export",
            "match_type": "exact",
        }
    ]


def test_from_name_single_exact_binds(client, monkeypatch):
    test_client, engine = client
    monkeypatch.setattr(
        HistoryService,
        "resolve_group_names",
        lambda self, name: [
            GroupMatch("real-1", "产品经理交流群", 10, "stub_export", "exact")
        ],
    )
    resp = test_client.post("/api/groups/from-name", json={"name": "产品经理交流群"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["bound"] is True
    assert body["already_existed"] is False
    with Session(engine) as session:
        rows = session.exec(select(Group).where(Group.wechat_group_id == "real-1")).all()
        assert len(rows) == 1
        assert rows[0].display_name == "产品经理交流群"
        assert rows[0].id == body["id"]


def test_from_name_ambiguous_409(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        HistoryService,
        "resolve_group_names",
        lambda self, name: [
            GroupMatch("g1", "产品经理交流群", 10, "stub_export", "exact"),
            GroupMatch("g2", "产品经理茶话会", 5, "stub_export", "exact"),
        ],
    )
    resp = test_client.post("/api/groups/from-name", json={"name": "产品经理"})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert "candidates" in detail
    assert len(detail["candidates"]) == 2


def test_from_name_ambiguous_with_id_binds(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        HistoryService,
        "resolve_group_names",
        lambda self, name: [
            GroupMatch("g1", "产品经理交流群", 10, "stub_export", "exact"),
            GroupMatch("g2", "产品经理茶话会", 5, "stub_export", "exact"),
        ],
    )
    resp = test_client.post("/api/groups/from-name", json={"name": "产品经理", "group_id": "g2"})
    assert resp.status_code == 200
    assert resp.json()["bound"] is True
    assert resp.json()["id"] is not None


def test_from_name_ambiguous_wrong_id_409(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        HistoryService,
        "resolve_group_names",
        lambda self, name: [
            GroupMatch("g1", "产品经理交流群", 10, "stub_export", "exact"),
            GroupMatch("g2", "产品经理茶话会", 5, "stub_export", "exact"),
        ],
    )
    resp = test_client.post("/api/groups/from-name", json={"name": "产品经理", "group_id": "g3"})
    assert resp.status_code == 409


def test_from_name_partial_with_id_binds(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        HistoryService,
        "resolve_group_names",
        lambda self, name: [
            GroupMatch("g1", "产品经理交流群", 10, "stub_export", "partial")
        ],
    )
    resp = test_client.post("/api/groups/from-name", json={"name": "产品经理", "group_id": "g1"})
    assert resp.status_code == 200
    assert resp.json()["bound"] is True


def test_from_name_partial_without_id_409(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(
        HistoryService,
        "resolve_group_names",
        lambda self, name: [
            GroupMatch("g1", "产品经理交流群", 10, "stub_export", "partial")
        ],
    )
    resp = test_client.post("/api/groups/from-name", json={"name": "产品经理"})
    assert resp.status_code == 409


def test_from_name_not_found_404(client, monkeypatch):
    test_client, _ = client
    monkeypatch.setattr(HistoryService, "resolve_group_names", lambda self, name: [])
    resp = test_client.post("/api/groups/from-name", json={"name": "不存在的群"})
    assert resp.status_code == 404


def test_from_name_blank_400(client):
    test_client, _ = client
    resp = test_client.post("/api/groups/from-name", json={"name": "   "})
    assert resp.status_code == 400


def test_from_name_duplicate_protection(client, monkeypatch):
    test_client, engine = client
    monkeypatch.setattr(
        HistoryService,
        "resolve_group_names",
        lambda self, name: [
            GroupMatch("real-1", "产品经理交流群", 10, "stub_export", "exact")
        ],
    )
    first = test_client.post("/api/groups/from-name", json={"name": "产品经理交流群"})
    second = test_client.post("/api/groups/from-name", json={"name": "产品经理交流群"})
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["id"] == second.json()["id"]
    assert second.json()["already_existed"] is True
    with Session(engine) as session:
        rows = session.exec(select(Group).where(Group.wechat_group_id == "real-1")).all()
        assert len(rows) == 1