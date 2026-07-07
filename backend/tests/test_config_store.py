import json

import pytest

from app.config import settings
from app.services import config_store


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    # 把配置目录指到临时目录,互不污染
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    yield tmp_path
    config_store._reset_cache()


def test_get_returns_defaults_when_missing(tmp_config):
    cfg = config_store.get()
    assert cfg["llm"]["model"] == ""          # 默认 key/model 留空
    assert "grep" in cfg["security"]["cmd_whitelist"]


def test_update_deep_merges_and_persists(tmp_config):
    config_store.update({"llm": {"api_key": "sk-abc"}})
    cfg = config_store.get()
    # 只改 api_key,base_url 默认值应保留(深合并,不是整段替换)
    assert cfg["llm"]["api_key"] == "sk-abc"
    assert cfg["llm"]["base_url"] == config_store.DEFAULTS["llm"]["base_url"]
    # 已落盘
    saved = json.loads((tmp_config / "config.json").read_text(encoding="utf-8"))
    assert saved["llm"]["api_key"] == "sk-abc"


def test_is_llm_configured(tmp_config):
    assert config_store.is_llm_configured() is False
    config_store.update({"llm": {"api_key": "sk-abc", "model": "ep-1"}})
    assert config_store.is_llm_configured() is True


def test_defaults_have_rag_section(tmp_config):
    # P3:embedding 加维度,新增 rag 段(切块/召回/精排参数)
    cfg = config_store.get()
    assert cfg["embedding"]["dimension"] == 1024
    assert cfg["rag"]["chunk_size"] == 500
    assert cfg["rag"]["overlap"] == 80
    assert cfg["rag"]["top_n"] == 20
    assert cfg["rag"]["top_k"] == 5
    assert cfg["rag"]["rerank_model"] == "gte-rerank"
