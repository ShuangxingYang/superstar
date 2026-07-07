"""会话 CRUD 路由。用 session_store 直接播种,再打接口。"""
import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import session_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    from app.api.main import app
    return TestClient(app)


def test_list_get_rename_delete(client):
    sid = session_store.create()
    session_store.append_message(sid, {"role": "user", "content": "你好呀"})

    r = client.get("/api/sessions")
    assert r.status_code == 200 and r.json()[0]["id"] == sid

    r = client.get(f"/api/sessions/{sid}")
    assert r.json()["messages"][0]["content"] == "你好呀"

    r = client.patch(f"/api/sessions/{sid}", json={"title": "改个名"})
    assert r.status_code == 200 and r.json()["title"] == "改个名"

    assert client.delete(f"/api/sessions/{sid}").status_code == 204
    assert client.get("/api/sessions").json() == []


def test_404_on_missing(client):
    assert client.get("/api/sessions/nope").status_code == 404
    assert client.patch("/api/sessions/nope", json={"title": "x"}).status_code == 404
    assert client.delete("/api/sessions/nope").status_code == 404


# ============ P2b: get_session 带 pending(历史回放待审批卡)============
from app.agent import pending


def test_get_session_includes_pending(client):
    sid = session_store.create()
    session_store.append_message(sid, {"role": "user", "content": "hi"})
    # 无 pending → null(向后兼容:新增字段)
    assert client.get(f"/api/sessions/{sid}").json()["pending"] is None
    # 有 pending → 回内容
    pending.write(sid, [{"id": "w1", "type": "function",
                         "function": {"name": "write_file", "arguments": "{}"}}],
                  {"w1": {"kind": "write", "path": "a", "diff": "d"}})
    body = client.get(f"/api/sessions/{sid}").json()
    assert body["pending"]["tool_calls"][0]["id"] == "w1"
