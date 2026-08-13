"""M0 验收：服务可启动、健康检查可用、版本正确。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app import __version__


def test_health_ok(client: TestClient) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["service"] == "ai-library"
    assert body["version"] == __version__


def test_health_unknown_route_404(client: TestClient) -> None:
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404
