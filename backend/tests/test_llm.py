import pytest

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


def test_raises_when_not_configured(tmp_config):
    with pytest.raises(RuntimeError):
        llm.get_llm_client()


def test_rebuilds_client_on_key_change(tmp_config):
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
