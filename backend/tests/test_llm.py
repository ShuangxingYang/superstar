import pytest
from openai import AsyncOpenAI, OpenAI

from app.config import settings
from app.services import config_store, llm


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    llm._reset_client()
    yield tmp_path
    config_store._reset_cache()
    llm._reset_client()


# ── async client (get_llm_client) ──────────────────────────────────────────


def test_async_raises_when_not_configured(tmp_config):
    with pytest.raises(RuntimeError):
        llm.get_llm_client()


def test_async_client_is_async_openai(tmp_config):
    config_store.update({"llm": {"api_key": "sk-1", "model": "m1", "base_url": "http://a"}})
    client, model = llm.get_llm_client()
    assert isinstance(client, AsyncOpenAI)
    assert model == "m1"


def test_async_rebuilds_client_on_key_change(tmp_config):
    config_store.update({"llm": {"api_key": "sk-1", "model": "m1", "base_url": "http://a"}})
    c1, m1 = llm.get_llm_client()
    assert m1 == "m1"
    # 同配置再取 → 命中缓存,同一个 client
    c2, _ = llm.get_llm_client()
    assert c2 is c1
    # 改 key → 重建(不同对象)
    config_store.update({"llm": {"api_key": "sk-2"}})
    c3, _ = llm.get_llm_client()
    assert c3 is not c1
    assert c3.api_key == "sk-2"
    # 只改 model → 不重建(同 client),但返回新 model
    config_store.update({"llm": {"model": "m2"}})
    c4, m4 = llm.get_llm_client()
    assert c4 is c3
    assert m4 == "m2"


# ── sync client (get_sync_llm_client) ─────────────────────────────────────


def test_sync_raises_when_not_configured(tmp_config):
    with pytest.raises(RuntimeError):
        llm.get_sync_llm_client()


def test_sync_client_is_openai(tmp_config):
    config_store.update({"llm": {"api_key": "sk-1", "model": "m1", "base_url": "http://a"}})
    client, model = llm.get_sync_llm_client()
    assert isinstance(client, OpenAI)
    assert model == "m1"


def test_sync_rebuilds_client_on_key_change(tmp_config):
    config_store.update({"llm": {"api_key": "sk-1", "model": "m1", "base_url": "http://a"}})
    c1, m1 = llm.get_sync_llm_client()
    assert m1 == "m1"
    # 同配置再取 → 命中缓存
    c2, _ = llm.get_sync_llm_client()
    assert c2 is c1
    # 改 key → 重建
    config_store.update({"llm": {"api_key": "sk-2"}})
    c3, _ = llm.get_sync_llm_client()
    assert c3 is not c1
    assert c3.api_key == "sk-2"
    # 只改 model → 不重建
    config_store.update({"llm": {"model": "m2"}})
    c4, m4 = llm.get_sync_llm_client()
    assert c4 is c3
    assert m4 == "m2"


# ── 两套缓存独立 ────────────────────────────────────────────────────────────


def test_caches_are_independent(tmp_config):
    config_store.update({"llm": {"api_key": "sk-1", "model": "m1", "base_url": "http://a"}})
    async_client, _ = llm.get_llm_client()
    sync_client, _ = llm.get_sync_llm_client()
    # 类型不同
    assert isinstance(async_client, AsyncOpenAI)
    assert isinstance(sync_client, OpenAI)
    # 不是同一对象
    assert async_client is not sync_client
