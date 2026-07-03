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
