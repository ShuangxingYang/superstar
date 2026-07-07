import pytest
from fastapi.testclient import TestClient

from app.api.main import app
from app.api.routes import kb as kb_routes
from app.config import settings
from app.services import config_store, rag_store


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    kb = tmp_path / "kb"
    kb.mkdir()
    config_store._reset_cache()
    config_store.update({"security": {"kb_dir": str(kb)}})
    yield TestClient(app)
    config_store._reset_cache()


def test_list(client, monkeypatch):
    monkeypatch.setattr(rag_store, "list_documents", lambda: [{"source": "a.md", "chunks": 3}])
    r = client.get("/api/kb/list")
    assert r.status_code == 200
    assert r.json() == [{"source": "a.md", "chunks": 3}]


def test_stats(client, monkeypatch):
    monkeypatch.setattr(rag_store, "stats", lambda: {"documents": 1, "chunks": 3, "dimension": 1024})
    r = client.get("/api/kb/stats")
    assert r.json()["dimension"] == 1024


def test_upload_indexes(client, monkeypatch):
    captured = {}

    def fake_index(path, source):
        captured["source"] = source
        return {"source": source, "chunks": 2}
    monkeypatch.setattr(rag_store, "index_document", fake_index)
    r = client.post("/api/kb/upload", files={"file": ("note.md", b"hello", "text/markdown")})
    assert r.status_code == 200
    assert r.json()["chunks"] == 2
    assert captured["source"] == "note.md"


def test_delete(client, monkeypatch):
    monkeypatch.setattr(rag_store, "delete_document", lambda source: 3)
    r = client.request("DELETE", "/api/kb/a.md")
    assert r.status_code == 200
    assert r.json()["deleted"] == 3


def test_rebuild(client, monkeypatch):
    monkeypatch.setattr(rag_store, "rebuild", lambda: {"documents": 2, "chunks": 10})
    r = client.post("/api/kb/rebuild")
    assert r.json()["chunks"] == 10


def test_ragstore_error_returns_503(client, monkeypatch):
    def boom():
        raise rag_store.RagStoreError("知识库服务未启动")
    monkeypatch.setattr(rag_store, "stats", boom)
    r = client.get("/api/kb/stats")
    assert r.status_code == 503
    assert "未启动" in r.json()["detail"]


def test_safe_kb_path_rejects_traversal(client):
    # 沙箱加固:source 带 ../ 越界 → 400,钉不进 kb_dir(计划漏了这道校验)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        kb_routes._safe_kb_path("../../etc/passwd")
    assert ei.value.status_code == 400
    # 正常相对路径不受影响
    assert kb_routes._safe_kb_path("sub/note.md").name == "note.md"
