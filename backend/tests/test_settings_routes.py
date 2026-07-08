import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.services import config_store
from app.api.main import app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    yield TestClient(app)
    config_store._reset_cache()


def test_get_settings_masks_key(client):
    config_store.update({"llm": {"api_key": "sk-abcdef123456"}})
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["llm"]["api_key"] == "sk-***3456"


def test_put_settings_updates(client):
    r = client.put("/api/settings", json={"llm": {"model": "ep-x"}})
    assert r.status_code == 200
    assert r.json()["llm"]["model"] == "ep-x"


def test_put_ignores_masked_key(client):
    # 前端把脱敏 key 原样回传时,不能用掩码覆盖真 key
    config_store.update({"llm": {"api_key": "sk-realkey9999"}})
    client.put("/api/settings", json={"llm": {"api_key": "sk-***9999"}})
    assert config_store.get()["llm"]["api_key"] == "sk-realkey9999"


def test_embedding_test_connection(client, monkeypatch):
    # kind='embedding' 应走 embeddings 接口(而非 chat.completions)
    import app.api.routes.settings as s
    calls = {}

    class FakeEmb:
        def create(self, **kw):
            calls["embedding"] = kw
            return object()

    class FakeChat:
        class completions:
            @staticmethod
            def create(**kw):
                calls["chat"] = kw

    class FakeClient:
        def __init__(self, **kw):
            self.embeddings = FakeEmb()
            self.chat = FakeChat()

    monkeypatch.setattr(s, "OpenAI", FakeClient)
    r = client.post("/api/settings/test", json={
        "base_url": "u", "api_key": "sk-x", "model": "text-embedding-v3", "kind": "embedding"})
    assert r.json()["ok"] is True
    assert "embedding" in calls and "chat" not in calls   # 只调了 embeddings
