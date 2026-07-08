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


def test_get_settings_returns_plaintext_key(client):
    # 本地自用:GET 回明文 key(前端默认密文、点眼睛看明文)
    config_store.update({"llm": {"api_key": "sk-abcdef123456"}})
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["llm"]["api_key"] == "sk-abcdef123456"


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


def test_llm_test_connection_uses_stream(client, monkeypatch):
    # 有些网关(如 tokenhub codex/v1)只接受流式请求;LLM 测连接必须带 stream=True,
    # 与对话循环一致。这里断言透传了 stream=True,且能消费流式返回。
    import app.api.routes.settings as s
    calls = {}

    class FakeCompletions:
        def create(self, **kw):
            calls.update(kw)
            return iter([object()])            # 可迭代,模拟流式分片

    class FakeChat:
        completions = FakeCompletions()

    class FakeClient:
        def __init__(self, **kw):
            self.chat = FakeChat()

    monkeypatch.setattr(s, "OpenAI", FakeClient)
    r = client.post("/api/settings/test", json={
        "base_url": "u", "api_key": "sk-x", "model": "gpt-5.4", "kind": "llm"})
    assert r.json()["ok"] is True
    assert calls.get("stream") is True
