import sys
import types

import pytest

from app.config import settings
from app.services import config_store, rag_store


@pytest.fixture(autouse=True)
def fake_qdrant_models(monkeypatch):
    """注入假的 qdrant_client.models(Distance/VectorParams),让测试不依赖真库。

    qdrant_client 到 Task 9 才装;这里只需要 VectorParams 这个「size+distance 容器」
    和 Distance.COSINE 这个枚举值,用轻量替身顶上,建集合分支就能跑通。
    """
    mod = types.ModuleType("qdrant_client.models")

    class VectorParams:
        def __init__(self, size, distance):
            self.size = size
            self.distance = distance

    class Distance:
        COSINE = "Cosine"

    mod.VectorParams = VectorParams
    mod.Distance = Distance
    monkeypatch.setitem(sys.modules, "qdrant_client.models", mod)
    yield


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))
    config_store._reset_cache()
    rag_store._reset()
    config_store.update({"embedding": {"api_key": "sk-x", "model": "text-embedding-v3", "dimension": 1024}})
    yield
    config_store._reset_cache()
    rag_store._reset()


class _FakeQdrant:
    """假 Qdrant:记录调用,内存存点。"""

    def __init__(self):
        self.collections = {}   # name -> dim
        self.points = {}        # name -> list[point-like]
        self.upserted = []

    def collection_exists(self, name):
        return name in self.collections

    def create_collection(self, collection_name, vectors_config):
        self.collections[collection_name] = vectors_config.size
        self.points[collection_name] = []

    def get_collection(self, name):
        dim = self.collections[name]

        class C:  # 仿 qdrant 返回结构 config.params.vectors.size
            class config:
                class params:
                    class vectors:
                        size = dim

        C.config.params.vectors.size = dim
        return C


def test_ensure_collection_creates_when_absent(cfg, monkeypatch):
    fake = _FakeQdrant()
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    rag_store._ensure_collection()
    assert rag_store.COLLECTION in fake.collections
    assert fake.collections[rag_store.COLLECTION] == 1024


def test_ensure_collection_dimension_mismatch_raises(cfg, monkeypatch):
    fake = _FakeQdrant()
    fake.collections[rag_store.COLLECTION] = 768   # 已存在且 768 维
    fake.points[rag_store.COLLECTION] = []
    monkeypatch.setattr(rag_store, "_get_qdrant", lambda: fake)
    config_store.update({"embedding": {"dimension": 1024}})  # 当前配置 1024
    with pytest.raises(rag_store.RagStoreError, match="维"):
        rag_store._ensure_collection()


def test_qdrant_connection_error_wrapped(cfg, monkeypatch):
    def boom():
        raise ConnectionError("refused")
    monkeypatch.setattr(rag_store, "_get_qdrant", boom)
    with pytest.raises(rag_store.RagStoreError, match="未启动"):
        rag_store._ensure_collection()
