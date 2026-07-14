"""POST /api/memory/distill:手动触发蒸馏,返回 {"result": ...}。"""
from fastapi.testclient import TestClient

from app.services import distill


def test_distill_endpoint(monkeypatch):
    # monkeypatch 掉真实蒸馏,只验证路由把结果包成 {"result": ...}
    monkeypatch.setattr(distill, "distill_memory", lambda: "蒸馏完成,长期记忆已更新(长度 42)")
    from app.api.main import app
    client = TestClient(app)
    r = client.post("/api/memory/distill")
    assert r.status_code == 200
    assert r.json() == {"result": "蒸馏完成,长期记忆已更新(长度 42)"}
